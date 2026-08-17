from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from coding_tools_mcp.errors import ToolFailure

from ..api import ExtensionContext, ExtensionManifest
from ..config import map_of, scalar, table
from ..contributions import ToolAnnotations, ToolContribution
from ..services import CORE_WORKSPACE, CORE_WORKSPACE_RUNTIMES, CapabilityKey
from .project_catalog import ProjectCatalog, build_project_catalog
from .registry import PROJECT_ID_RE, PROJECT_REGISTRY, ProjectRegistry, ProjectRegistryError, build_project_registry
from .runtime import PROJECT_RUNTIMES, ProjectRuntimeManager
from .skill_catalog import ProjectNotFoundError, SkillInvalidError, SkillNotFoundError


PROJECT_CATALOG = CapabilityKey[ProjectCatalog]("projects.catalog")


class ProjectsExtension:
    manifest = ExtensionManifest(
        name="projects",
        description="Single-workspace project scope, instructions, and skills discovery.",
        config_schema=table(
            {
                "registry": map_of(
                    table(
                        {
                            "root": scalar(str),
                            "allow_unavailable": scalar(bool),
                        }
                    )
                )
            }
        ),
    )

    def __init__(self) -> None:
        self._config: Mapping[str, object] = {}
        self._registry: ProjectRegistry | None = None
        self._runtimes: ProjectRuntimeManager | None = None

    def configure(self, config: Mapping[str, object]) -> None:
        self._config = config

    def register(self, context: ExtensionContext) -> None:
        workspace = context.services.require(CORE_WORKSPACE)
        workspace_runtimes = context.services.require(CORE_WORKSPACE_RUNTIMES)
        registry = build_project_registry(
            self._config,
            fallback_root=workspace.root,
            validate_root=workspace_runtimes.validate_root,
        )
        runtimes = ProjectRuntimeManager(registry, workspace_runtimes)
        catalog = build_project_catalog(workspace.root)
        self._registry = registry
        self._runtimes = runtimes
        context.services.provide(PROJECT_REGISTRY, registry)
        context.services.provide(PROJECT_RUNTIMES, runtimes)
        context.services.provide(PROJECT_CATALOG, catalog)
        context.add_tool(self._list_projects_tool())
        context.add_tool(self._resolve_project_tool())
        context.add_tool(self._list_skills_tool())
        context.add_tool(self._read_skill_tool())

    def start(self) -> None:
        return None

    def stop(self) -> None:
        runtimes = self._runtimes
        self._runtimes = None
        if runtimes is None:
            return None
        warnings = runtimes.close()
        if warnings:
            raise RuntimeError("; ".join(warnings))
        return None

    def _list_projects_tool(self) -> ToolContribution:
        return ToolContribution(
            name="list_projects",
            title="List projects",
            description="List explicitly registered projects and their stable project IDs.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=self.list_projects,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
        )

    def _resolve_project_tool(self) -> ToolContribution:
        return ToolContribution(
            name="resolve_project",
            title="Resolve project",
            description=(
                "Resolve an absolute server path to its registered project ID and structural project scope."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=self.resolve_project,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            error_status="failed",
        )

    def _list_skills_tool(self) -> ToolContribution:
        return ToolContribution(
            name="list_skills",
            title="List skills",
            description="List project-scoped skills and instruction files for the explicit workspace-relative workdir.",
            input_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": PROJECT_ID_RE.pattern,
                    },
                    "workdir": {"type": "string", "default": "."},
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
            handler=self.list_skills,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            text_renderer=self._render_list_skills,
        )

    def _read_skill_tool(self) -> ToolContribution:
        return ToolContribution(
            name="read_skill",
            title="Read skill",
            description="Read the effective named skill for the explicit workspace-relative workdir.",
            input_schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": PROJECT_ID_RE.pattern,
                    },
                    "workdir": {"type": "string", "default": "."},
                    "skill": {"type": "string", "minLength": 1},
                },
                "required": ["project_id", "skill"],
                "additionalProperties": False,
            },
            handler=self.read_skill,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            error_status="failed",
            text_renderer=self._render_read_skill,
        )

    def _require_registry(self) -> ProjectRegistry:
        if self._registry is None:
            raise RuntimeError("projects extension is not registered")
        return self._registry

    def _require_runtimes(self) -> ProjectRuntimeManager:
        if self._runtimes is None:
            raise RuntimeError("projects extension is not registered")
        return self._runtimes

    def _resolve_skill_workdir(self, project_id: str, raw_workdir: str) -> Path:
        resolved = self._require_runtimes().resolve_existing(project_id, raw_workdir or ".")
        if not resolved.path.is_dir():
            raise ToolFailure("NOT_A_DIRECTORY", "workdir must be a directory.", category="validation")
        return resolved.path

    @staticmethod
    def _raise_registry_failure(exc: ProjectRegistryError) -> None:
        raise ToolFailure(
            exc.code,
            exc.message,
            category="not_found" if exc.code in {"PROJECT_NOT_FOUND", "PROJECT_UNAVAILABLE"} else "validation",
        ) from exc

    def list_projects(self, _args: dict[str, Any]) -> dict[str, Any]:
        registry = self._require_registry()
        projects = registry.projects()
        return {
            "ok": True,
            "projects": [project.summary(expose_root=True) for project in projects],
            "project_count": len(projects),
            "warnings": [],
        }

    def resolve_project(self, args: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(args.get("path", ""))
        registry = self._require_registry()
        try:
            project, resolved_path = registry.resolve_absolute(raw_path)
        except ProjectRegistryError as exc:
            self._raise_registry_failure(exc)
            raise AssertionError("unreachable")

        runtime = self._require_runtimes().require(project.project_id)
        relative = resolved_path.relative_to(project.root).as_posix()
        if relative == ".":
            relative = "."
        selection = runtime.catalog.resolve(resolved_path)
        scope_chain = []
        if selection is not None:
            scope_chain = [
                {
                    "scope_id": scope.scope_id,
                    "scope_root": scope.display_root,
                    "kind": scope.kind,
                    "markers": list(scope.markers),
                    "parent_scope_id": scope.parent_scope_id,
                }
                for scope in selection.scope_chain
            ]
        return {
            "ok": True,
            "project_id": project.project_id,
            "root": str(project.root),
            "relative_path": relative,
            "markers": list(project.markers),
            "scope_chain": scope_chain,
            "available": project.available,
            "warnings": [*project.warnings, *runtime.catalog.warnings],
        }

    def list_skills(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        runtime = self._require_runtimes().require(project_id)
        workdir = self._resolve_skill_workdir(project_id, str(args.get("workdir", ".")))
        try:
            context = runtime.skills.list_for(workdir)
        except ValueError as exc:
            raise ToolFailure("INVALID_ARGUMENT", str(exc), category="validation") from exc
        return {"ok": True, "project_id": project_id, **context.payload()}

    def read_skill(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        runtime = self._require_runtimes().require(project_id)
        workdir = self._resolve_skill_workdir(project_id, str(args.get("workdir", ".")))
        skill = str(args.get("skill", ""))
        if not skill:
            raise ToolFailure("INVALID_ARGUMENT", "skill is required.", category="validation")
        try:
            loaded = runtime.skills.read(workdir, skill)
        except ValueError as exc:
            raise ToolFailure("INVALID_ARGUMENT", str(exc), category="validation") from exc
        except ProjectNotFoundError as exc:
            raise ToolFailure("PROJECT_NOT_FOUND", str(exc), category="not_found") from exc
        except SkillNotFoundError as exc:
            raise ToolFailure(
                "SKILL_NOT_FOUND",
                str(exc),
                category="not_found",
                details={"available": list(exc.available)},
            ) from exc
        except SkillInvalidError as exc:
            raise ToolFailure("SKILL_INVALID", str(exc), category="invalid_state") from exc
        return {"ok": True, "project_id": project_id, **loaded.payload()}

    @staticmethod
    def _render_list_skills(payload: dict[str, Any]) -> str:
        skills = payload.get("skills")
        if not isinstance(skills, list) or not skills:
            return "No skills found."
        lines: list[str] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            description = item.get("description", "")
            source = item.get("source", "")
            if description:
                lines.append(f"{name}: {description} ({source})")
            else:
                lines.append(f"{name} ({source})")
        return "\n".join(lines)

    @staticmethod
    def _render_read_skill(payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if not isinstance(content, str):
            return ""
        if not payload.get("truncated"):
            return content
        total_bytes = payload.get("total_bytes", "?")
        returned_bytes = payload.get("returned_bytes", "?")
        return (
            f"[showing {returned_bytes} of {total_bytes} bytes; skill body truncated]\n"
            f"{content}"
        )
