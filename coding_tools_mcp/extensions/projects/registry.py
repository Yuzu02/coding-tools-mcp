from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions.config import ConfigError
from coding_tools_mcp.extensions.services import CapabilityKey

from .project_catalog import project_markers


PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class RootValidator(Protocol):
    def __call__(self, root: Path, *, require_exists: bool = True) -> Path:
        raise NotImplementedError


class RegisteredProjectRecord(Protocol):
    @property
    def project_id(self) -> str:
        raise NotImplementedError

    @property
    def root(self) -> Path:
        raise NotImplementedError

    @property
    def allow_unavailable(self) -> bool:
        raise NotImplementedError


def _contains(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class RegisteredProject:
    project_id: str
    root: Path
    markers: tuple[str, ...]
    available: bool
    warnings: tuple[str, ...] = ()

    def summary(self, *, expose_root: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.project_id,
            "markers": list(self.markers),
            "available": self.available,
            "warnings": list(self.warnings),
        }
        if expose_root:
            payload["root"] = str(self.root)
        return payload


class ProjectRegistryError(LookupError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProjectRegistry:
    def __init__(self, projects: tuple[RegisteredProject, ...]) -> None:
        self._projects = projects
        self._by_id = {project.project_id: project for project in projects}

    def ids(self) -> tuple[str, ...]:
        return tuple(project.project_id for project in self._projects)

    def projects(self) -> tuple[RegisteredProject, ...]:
        return self._projects

    def get(self, project_id: str) -> RegisteredProject:
        project = self._by_id.get(project_id)
        if project is None:
            raise ProjectRegistryError(
                "PROJECT_NOT_FOUND",
                f"Unknown configured project: {project_id}",
            )
        return project

    def require_available(self, project_id: str) -> RegisteredProject:
        project = self.get(project_id)
        if not project.available:
            raise ProjectRegistryError(
                "PROJECT_UNAVAILABLE",
                f"Configured project is unavailable until restart: {project_id}",
            )
        return project

    def excluded_roots_for(self, project_id: str) -> tuple[Path, ...]:
        project = self.require_available(project_id)
        nested = {
            candidate.root
            for candidate in self._projects
            if candidate.available
            and candidate.project_id != project_id
            and candidate.root != project.root
            and _contains(project.root, candidate.root)
        }
        return tuple(sorted(nested, key=lambda path: len(path.parts), reverse=True))

    def resolve_absolute(self, path: Path | str) -> tuple[RegisteredProject, Path]:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            raise ProjectRegistryError(
                "INVALID_PROJECT_PATH",
                "Project path must be absolute.",
            )

        lexical = Path(os.path.abspath(str(raw)))
        lexical_candidates = [project for project in self._projects if _contains(project.root, lexical)]
        if not lexical_candidates:
            raise ProjectRegistryError(
                "PROJECT_NOT_FOUND",
                f"No configured project contains path: {raw}",
            )
        lexical_project = max(lexical_candidates, key=lambda project: len(project.root.parts))
        if not lexical_project.available:
            raise ProjectRegistryError(
                "PROJECT_UNAVAILABLE",
                f"Configured project is unavailable until restart: {lexical_project.project_id}",
            )

        try:
            resolved = raw.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ProjectRegistryError(
                "INVALID_PROJECT_PATH",
                f"Project path does not resolve to an existing path: {raw}",
            ) from exc

        physical_candidates = [
            project
            for project in self._projects
            if project.available and _contains(project.root, resolved)
        ]
        if not physical_candidates:
            raise ProjectRegistryError(
                "INVALID_PROJECT_PATH",
                "Project path escapes the registered root through a symlink.",
            )
        physical_project = max(physical_candidates, key=lambda project: len(project.root.parts))
        if physical_project.project_id != lexical_project.project_id:
            raise ProjectRegistryError(
                "INVALID_PROJECT_PATH",
                "Project path crosses a registered project boundary through a symlink.",
            )
        return physical_project, resolved


def build_project_registry_from_records(
    records: tuple[RegisteredProjectRecord, ...],
    *,
    validate_root: RootValidator,
) -> ProjectRegistry:
    projects: list[RegisteredProject] = []
    roots: dict[Path, str] = {}
    for record in records:
        project_id = record.project_id
        if PROJECT_ID_RE.fullmatch(project_id) is None:
            raise ConfigError(f"invalid project_id: {project_id}")
        try:
            root = validate_root(
                record.root,
                require_exists=not record.allow_unavailable,
            )
        except ToolFailure as exc:
            raise ConfigError(f"invalid root for project {project_id}: {exc.message}") from exc
        available = root.is_dir()
        if not available and not record.allow_unavailable:
            raise ConfigError(f"project root does not exist: {project_id}: {root}")
        previous = roots.get(root)
        if previous is not None:
            raise ConfigError(
                f"projects {previous!r} and {project_id!r} resolve to the same canonical root: {root}"
            )
        roots[root] = project_id
        warnings = () if available else ("configured project root is unavailable until restart",)
        projects.append(
            RegisteredProject(
                project_id=project_id,
                root=root,
                markers=project_markers(root) if available else (),
                available=available,
                warnings=warnings,
            )
        )
    return ProjectRegistry(tuple(projects))


def build_project_registry(
    settings: Mapping[str, object],
    *,
    fallback_root: Path,
    validate_root: RootValidator,
) -> ProjectRegistry:
    raw_registry = settings.get("registry")
    if raw_registry is None or raw_registry == {}:
        root = validate_root(fallback_root, require_exists=True)
        return ProjectRegistry(
            (
                RegisteredProject(
                    project_id="default",
                    root=root,
                    markers=project_markers(root),
                    available=True,
                ),
            )
        )
    if not isinstance(raw_registry, Mapping):
        raise ConfigError("extensions.projects.registry must be a table")

    records: list[RegisteredProject] = []
    roots: dict[Path, str] = {}
    for raw_project_id, raw_settings in raw_registry.items():
        project_id = str(raw_project_id)
        if PROJECT_ID_RE.fullmatch(project_id) is None:
            raise ConfigError(f"invalid project_id: {project_id}")
        if not isinstance(raw_settings, Mapping):
            raise ConfigError(f"extensions.projects.registry.{project_id} must be a table")
        raw_root = raw_settings.get("root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ConfigError(f"extensions.projects.registry.{project_id}.root is required")
        allow_unavailable = raw_settings.get("allow_unavailable", False)
        if type(allow_unavailable) is not bool:
            raise ConfigError(
                f"extensions.projects.registry.{project_id}.allow_unavailable must be boolean"
            )
        try:
            root = validate_root(
                Path(raw_root),
                require_exists=not allow_unavailable,
            )
        except ToolFailure as exc:
            raise ConfigError(f"invalid root for project {project_id}: {exc.message}") from exc
        available = root.is_dir()
        if not available and not allow_unavailable:
            raise ConfigError(f"project root does not exist: {project_id}: {root}")
        previous = roots.get(root)
        if previous is not None:
            raise ConfigError(
                f"projects {previous!r} and {project_id!r} resolve to the same canonical root: {root}"
            )
        roots[root] = project_id
        warnings = () if available else ("configured project root is unavailable until restart",)
        records.append(
            RegisteredProject(
                project_id=project_id,
                root=root,
                markers=project_markers(root) if available else (),
                available=available,
                warnings=warnings,
            )
        )
    return ProjectRegistry(tuple(records))


PROJECT_REGISTRY = CapabilityKey[ProjectRegistry]("projects.registry")
