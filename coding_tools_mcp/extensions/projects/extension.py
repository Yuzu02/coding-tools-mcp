from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from coding_tools_mcp.errors import ToolFailure

from ..api import ExtensionContext, ExtensionManifest
from ..config import ConfigError, table
from ..contributions import ToolAnnotations, ToolContribution
from ..services import CORE_WORKSPACE, CapabilityKey, WorkspaceAccess
from .project_catalog import ProjectCatalog, build_project_catalog
from .skill_catalog import ProjectNotFoundError, SkillCatalog, SkillInvalidError, SkillNotFoundError


PROJECT_CATALOG = CapabilityKey[ProjectCatalog]("projects.catalog")


class ProjectsExtension:
    manifest = ExtensionManifest(
        name="projects",
        description="Single-workspace project scope, instructions, and skills discovery.",
        config_schema=table({}),
    )

    def __init__(self) -> None:
        self._workspace: WorkspaceAccess | None = None
        self._skills: SkillCatalog | None = None

    def configure(self, config: Mapping[str, object]) -> None:
        if config:
            raise ConfigError("extensions.projects has no Phase 0 settings")

    def register(self, context: ExtensionContext) -> None:
        workspace = context.services.require(CORE_WORKSPACE)
        catalog = build_project_catalog(workspace.root)
        self._workspace = workspace
        self._skills = SkillCatalog(catalog)
        context.services.provide(PROJECT_CATALOG, catalog)
        context.add_tool(self._list_skills_tool())
        context.add_tool(self._read_skill_tool())

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def _list_skills_tool(self) -> ToolContribution:
        return ToolContribution(
            name="list_skills",
            title="List skills",
            description="List project-scoped skills and instruction files for the explicit workspace-relative workdir.",
            input_schema={
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "default": "."},
                },
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
                    "workdir": {"type": "string", "default": "."},
                    "skill": {"type": "string", "minLength": 1},
                },
                "required": ["skill"],
                "additionalProperties": False,
            },
            handler=self.read_skill,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            error_status="failed",
            text_renderer=self._render_read_skill,
        )

    def _require_workspace(self) -> WorkspaceAccess:
        if self._workspace is None:
            raise RuntimeError("projects extension is not registered")
        return self._workspace

    def _require_skills(self) -> SkillCatalog:
        if self._skills is None:
            raise RuntimeError("projects extension is not registered")
        return self._skills

    def _resolve_skill_workdir(self, raw_workdir: str) -> Path:
        workspace = self._require_workspace()
        resolved = workspace.resolve_existing(raw_workdir or ".")
        if not resolved.path.is_dir():
            raise ToolFailure("NOT_A_DIRECTORY", "workdir must be a directory.", category="validation")
        return resolved.path

    def list_skills(self, args: dict[str, Any]) -> dict[str, Any]:
        workdir = self._resolve_skill_workdir(str(args.get("workdir", ".")))
        catalog = self._require_skills()
        try:
            context = catalog.list_for(workdir)
        except ValueError as exc:
            raise ToolFailure("INVALID_ARGUMENT", str(exc), category="validation") from exc
        return {"ok": True, **context.payload()}

    def read_skill(self, args: dict[str, Any]) -> dict[str, Any]:
        workdir = self._resolve_skill_workdir(str(args.get("workdir", ".")))
        skill = str(args.get("skill", ""))
        if not skill:
            raise ToolFailure("INVALID_ARGUMENT", "skill is required.", category="validation")
        catalog = self._require_skills()
        try:
            loaded = catalog.read(workdir, skill)
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
        return {"ok": True, **loaded.payload()}

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
