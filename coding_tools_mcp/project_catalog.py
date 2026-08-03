from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)
EXCLUDED_PROJECT_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".worktrees",
        ".reference",
        ".venv",
        "venv",
        ".tox",
        "node_modules",
        "dist",
        "build",
        "target",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)
MAX_MAIN_PROJECTS = 256
MAX_SUBPROJECTS_PER_MAIN = 256
MAX_PROJECT_WARNINGS = 128


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    root: Path
    display_root: str
    markers: tuple[str, ...]
    kind: Literal["main", "subproject"]
    parent_project_id: str | None

    def summary(self) -> dict[str, object]:
        return {
            "id": self.project_id,
            "root": self.display_root,
            "markers": list(self.markers),
            "kind": self.kind,
            "parent_project_id": self.parent_project_id,
        }


@dataclass(frozen=True)
class ProjectSelection:
    main_project: ProjectRecord
    subprojects: tuple[ProjectRecord, ...]

    @property
    def scope_chain(self) -> tuple[ProjectRecord, ...]:
        return (self.main_project, *self.subprojects)


class ProjectCatalog:
    def __init__(
        self,
        workspace: Path,
        main_projects: tuple[ProjectRecord, ...],
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.workspace = workspace.expanduser().resolve(strict=True)
        self.main_projects = main_projects
        self.warnings = warnings
        self._selection_cache: dict[Path, ProjectSelection | None] = {}
        self._cache_lock = threading.RLock()

    def resolve(self, raw_path: Path | str) -> ProjectSelection | None:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Project path must remain inside the configured workspace.") from exc
        directory = resolved if resolved.is_dir() else resolved.parent
        with self._cache_lock:
            if directory in self._selection_cache:
                return self._selection_cache[directory]
        main = self._containing_main_project(directory)
        if main is None:
            selection = None
        else:
            selection = ProjectSelection(main, self._subprojects_for(main, directory))
        with self._cache_lock:
            self._selection_cache[directory] = selection
        return selection

    def _containing_main_project(self, directory: Path) -> ProjectRecord | None:
        candidates = [project for project in self.main_projects if _is_relative_to(directory, project.root)]
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item.root.parts))

    def _subprojects_for(self, main: ProjectRecord, directory: Path) -> tuple[ProjectRecord, ...]:
        relative = directory.relative_to(main.root)
        if not relative.parts:
            return ()
        discovered: list[ProjectRecord] = []
        parent_id = main.project_id
        current = main.root
        for part in relative.parts:
            current /= part
            if part in EXCLUDED_PROJECT_DIRS:
                break
            markers = _project_markers(current)
            if not markers:
                continue
            project_id = _display_path(current, self.workspace)
            discovered.append(
                ProjectRecord(
                    project_id=project_id,
                    root=current,
                    display_root=project_id,
                    markers=markers,
                    kind="subproject",
                    parent_project_id=parent_id,
                )
            )
            parent_id = project_id
            if len(discovered) >= MAX_SUBPROJECTS_PER_MAIN:
                break
        return tuple(discovered)

    def summary(self) -> dict[str, object]:
        return {
            "main_projects": [project.summary() for project in self.main_projects],
            "main_project_count": len(self.main_projects),
            "warnings": list(self.warnings),
        }


def build_project_catalog(workspace: Path) -> ProjectCatalog:
    root = workspace.expanduser().resolve(strict=True)
    warnings: list[str] = []
    root_markers = _project_markers(root)
    if root_markers:
        projects = (
            ProjectRecord(
                project_id=".",
                root=root,
                display_root=".",
                markers=root_markers,
                kind="main",
                parent_project_id=None,
            ),
        )
        return ProjectCatalog(root, projects)

    projects: list[ProjectRecord] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        return ProjectCatalog(root, (), (f"Could not list workspace projects: {exc}",))
    for child in children:
        if len(projects) >= MAX_MAIN_PROJECTS:
            _append_warning(warnings, f"Main project list truncated to {MAX_MAIN_PROJECTS} entries.")
            break
        if child.name in EXCLUDED_PROJECT_DIRS or not child.is_dir():
            continue
        if child.is_symlink():
            try:
                physical = child.resolve(strict=True)
                physical.relative_to(root)
            except (OSError, ValueError):
                _append_warning(warnings, f"Skipped unsafe project directory: {child.name}")
                continue
        markers = _project_markers(child)
        if not markers:
            continue
        display = _display_path(child, root)
        projects.append(
            ProjectRecord(
                project_id=display,
                root=child.resolve(strict=True),
                display_root=display,
                markers=markers,
                kind="main",
                parent_project_id=None,
            )
        )
    return ProjectCatalog(root, tuple(projects), tuple(warnings))


def _project_markers(path: Path) -> tuple[str, ...]:
    return tuple(marker for marker in PROJECT_MARKERS if (path / marker).exists())


def _display_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _append_warning(warnings: list[str], message: str) -> None:
    if len(warnings) < MAX_PROJECT_WARNINGS:
        warnings.append(message)
