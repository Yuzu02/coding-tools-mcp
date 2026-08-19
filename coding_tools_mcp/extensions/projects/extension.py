from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.host_config import ConfigSnapshot

from ..api import ExtensionContext, ExtensionManifest
from ..config import map_of, scalar, table
from ..contributions import SchemaPatch, ToolAnnotations, ToolContribution, ToolDecorator, ToolHandler
from ..services import (
    CORE_CONFIG_SNAPSHOT,
    CORE_WORKSPACE,
    CORE_WORKSPACE_RUNTIMES,
    CapabilityKey,
    ServiceRegistryError,
)
from .project_catalog import ProjectCatalog, build_project_catalog
from .registry import (
    PROJECT_ID_RE,
    PROJECT_REGISTRY,
    ProjectRegistry,
    ProjectRegistryError,
    build_project_registry,
    build_project_registry_from_records,
)
from .runtime import PROJECT_RUNTIMES, ProjectRuntimeManager
from .skill_catalog import ProjectNotFoundError, SkillInvalidError, SkillNotFoundError


PROJECT_CATALOG = CapabilityKey[ProjectCatalog]("projects.catalog")
PROJECT_SCOPED_CORE_TOOLS = (
    "check_exec_environment",
    "read_file",
    "list_dir",
    "list_files",
    "search_text",
    "apply_patch",
    "exec_command",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_blame",
)
OPTIONAL_PROJECT_SCOPED_CORE_TOOLS = ("view_image",)
PROJECT_SCOPED_PUBLIC_TOOLS = frozenset(
    (*PROJECT_SCOPED_CORE_TOOLS, *OPTIONAL_PROJECT_SCOPED_CORE_TOOLS, "list_skills", "read_skill")
)


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
                            "project_config": scalar(str),
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
        self._snapshot: ConfigSnapshot | None = None

    def configure(self, config: Mapping[str, object]) -> None:
        self._config = config

    def prepare(self) -> None:
        return None

    def register(self, context: ExtensionContext) -> None:
        workspace = context.services.require(CORE_WORKSPACE)
        workspace_runtimes = context.services.require(CORE_WORKSPACE_RUNTIMES)
        try:
            snapshot = context.services.require(CORE_CONFIG_SNAPSHOT)
        except ServiceRegistryError:
            snapshot = None
            registry = build_project_registry(
                self._config,
                fallback_root=workspace.root,
                validate_root=workspace_runtimes.validate_root,
            )
        else:
            assert snapshot is not None
            registry = build_project_registry_from_records(
                snapshot.registered_projects,
                validate_root=workspace_runtimes.validate_root,
            )
        runtimes = ProjectRuntimeManager(registry, workspace_runtimes)
        catalog = build_project_catalog(workspace.root)
        self._registry = registry
        self._runtimes = runtimes
        self._snapshot = snapshot
        context.services.provide(PROJECT_REGISTRY, registry)
        context.services.provide(PROJECT_RUNTIMES, runtimes)
        context.services.provide(PROJECT_CATALOG, catalog)
        context.add_tool(self._list_projects_tool())
        context.add_tool(self._resolve_project_tool())
        context.add_tool(self._list_skills_tool())
        context.add_tool(self._read_skill_tool())
        context.add_tool(self._project_context_tool())
        context.add_tool(self._doctor_tool())
        context.add_server_instructions(
            (
                "This endpoint serves multiple explicitly registered projects. "
                "Call list_projects to discover stable IDs. Every project-scoped filesystem, Git, process, "
                "image, environment, or skill request must include project_id; paths and workdirs remain "
                "relative to that selected project. No previous request selects a current project. "
                "Read project-scoped instruction files returned by list_skills/read_file before modifying "
                "that scope."
            ),
            replace_default=True,
        )
        context.add_decorator(self._project_routing_decorator())
        context.add_decorator(self._command_id_routing_decorator())
        context.add_decorator(self._read_output_routing_decorator())
        context.add_decorator(self._get_command_routing_decorator())
        context.add_decorator(self._list_commands_routing_decorator())
        context.add_decorator(self._server_info_routing_decorator())
        context.add_decorator(self._request_permissions_routing_decorator())

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

    def _project_context_tool(self) -> ToolContribution:
        return ToolContribution(
            name="project_context",
            title="Project context",
            description=(
                "Return one compact, bounded orientation view for an explicitly selected project."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project_id": self._project_id_schema(),
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "default": "summary",
                    },
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
            handler=self.project_context,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
        )

    def _doctor_tool(self) -> ToolContribution:
        return ToolContribution(
            name="doctor",
            title="Doctor",
            description=(
                "Run bounded read-only runtime/project health checks and return structured recovery hints."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project_id": self._project_id_schema(),
                    "detail": {
                        "type": "string",
                        "enum": ["summary", "full"],
                        "default": "summary",
                    },
                },
                "additionalProperties": False,
            },
            handler=self.doctor,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
        )

    def _project_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=PROJECT_SCOPED_CORE_TOOLS,
            optional_targets=OPTIONAL_PROJECT_SCOPED_CORE_TOOLS,
            schema_patch=SchemaPatch(
                properties={
                    "project_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": PROJECT_ID_RE.pattern,
                    }
                },
                required=("project_id",),
            ),
            wrap_handler=self._wrap_project_scoped_core_handler,
        )

    def _command_id_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("write_stdin", "kill_command"),
            schema_patch=SchemaPatch(),
            wrap_handler=self._wrap_command_id_handler,
        )

    def _read_output_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("read_output",),
            schema_patch=SchemaPatch(),
            wrap_handler=self._wrap_read_output_handler,
        )

    def _get_command_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("get_command",),
            schema_patch=SchemaPatch(properties={"project_id": self._project_id_schema()}),
            wrap_handler=self._wrap_get_command_handler,
        )

    def _list_commands_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("list_commands",),
            schema_patch=SchemaPatch(properties={"project_id": self._project_id_schema()}),
            wrap_handler=self._wrap_list_commands_handler,
        )

    def _server_info_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("server_info",),
            schema_patch=SchemaPatch(),
            wrap_handler=self._wrap_server_info_handler,
        )

    def _request_permissions_routing_decorator(self) -> ToolDecorator:
        return ToolDecorator(
            targets=("request_permissions",),
            schema_patch=SchemaPatch(),
            wrap_handler=self._wrap_request_permissions_handler,
        )

    @staticmethod
    def _project_id_schema() -> dict[str, Any]:
        return {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": PROJECT_ID_RE.pattern,
        }

    def _wrap_project_scoped_core_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            project_id = str(args.get("project_id", ""))
            clean = dict(args)
            clean.pop("project_id", None)
            try:
                payload = self._require_runtimes().invoke(project_id, next_handler, clean)
            except ToolFailure as exc:
                self._restore_project_failure(exc, project_id)
                raise
            return self._restore_project_addressing(payload, project_id)

        return routed

    def _wrap_command_id_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            command_id = str(args.get("command_id", ""))
            runtimes = self._require_runtimes()
            owner = runtimes.command_owners.owner(command_id)
            payload = runtimes.invoke(owner, next_handler, dict(args))
            return self._restore_project_addressing(payload, owner)

        return routed

    def _wrap_read_output_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            output_ref = str(args.get("output_ref", ""))
            match = re.fullmatch(r"command:([^:]+):(stdout|stderr)", output_ref)
            if match is None:
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "output_ref must look like command:<id>:stdout or command:<id>:stderr.",
                    category="validation",
                )
            runtimes = self._require_runtimes()
            owner = runtimes.command_owners.owner(match.group(1))
            payload = runtimes.invoke(owner, next_handler, dict(args))
            return self._restore_project_addressing(payload, owner)

        return routed

    def _wrap_get_command_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            clean = dict(args)
            supplied_project = clean.pop("project_id", None)
            command_id = clean.get("command_id")
            client_request_id = clean.get("client_request_id")
            if (command_id is None) == (client_request_id is None):
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "Provide exactly one of command_id or client_request_id.",
                    category="validation",
                )
            runtimes = self._require_runtimes()
            if command_id is not None:
                owner = runtimes.command_owners.owner(str(command_id))
                if supplied_project is not None and str(supplied_project) != owner:
                    raise ToolFailure(
                        "INVALID_ARGUMENT",
                        "project_id does not own the requested command_id.",
                        category="validation",
                    )
                try:
                    payload = runtimes.invoke(owner, next_handler, clean)
                except ToolFailure as exc:
                    self._restore_project_failure(exc, owner)
                    raise
                return self._restore_project_addressing(payload, owner)

            if supplied_project is None or not str(supplied_project):
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "project_id is required with client_request_id.",
                    category="validation",
                )
            project_id = str(supplied_project)
            self._require_registry_project(project_id)
            runtime = runtimes.active_for(project_id)
            if runtime is None:
                raise ToolFailure(
                    "COMMAND_NOT_FOUND",
                    "No command is retained for client_request_id in the selected project.",
                    category="not_found",
                )
            try:
                payload = runtimes.workspace_runtimes.invoke(runtime.workspace, next_handler, clean)
            except ToolFailure as exc:
                self._restore_project_failure(exc, project_id)
                raise
            return self._restore_project_addressing(payload, project_id)

        return routed

    def _wrap_list_commands_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            clean = dict(args)
            supplied_project = clean.pop("project_id", None)
            client_request_id = clean.get("client_request_id")
            runtimes = self._require_runtimes()
            if client_request_id is not None and (supplied_project is None or not str(supplied_project)):
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "project_id is required with client_request_id.",
                    category="validation",
                )

            if supplied_project is not None:
                project_id = str(supplied_project)
                self._require_registry_project(project_id)
                runtime = runtimes.active_for(project_id)
                if runtime is None:
                    if client_request_id is not None:
                        raise ToolFailure(
                            "COMMAND_NOT_FOUND",
                            "No command is retained for client_request_id in the selected project.",
                            category="not_found",
                        )
                    return self._empty_command_list(project_id=project_id)
                payload = runtimes.workspace_runtimes.invoke(runtime.workspace, next_handler, clean)
                return self._annotate_command_list(payload, project_id)

            status_filter = str(clean.get("status", "all"))
            limit = max(1, min(int(clean.get("limit", 20)), 100))
            commands: list[dict[str, Any]] = []
            for runtime in runtimes.active():
                per_project = runtimes.workspace_runtimes.invoke(
                    runtime.workspace,
                    next_handler,
                    {"status": "all", "limit": 100},
                )
                for raw_item in per_project.get("commands", []):
                    if isinstance(raw_item, dict):
                        item = dict(raw_item)
                        item["project_id"] = runtime.project.project_id
                        commands.append(item)
            if status_filter == "running":
                commands = [item for item in commands if item.get("status") == "running"]
            elif status_filter == "completed":
                commands = [item for item in commands if item.get("status") != "running"]
            commands.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
            total = len(commands)
            return {
                "commands": commands[:limit],
                "count": min(total, limit),
                "total": total,
                "truncated": total > limit,
                "pending": False,
                "ok": True,
                "warnings": [],
            }

        return routed

    def _wrap_server_info_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            payload = dict(next_handler(args))
            for field in (
                "workspace",
                "runtime_dir",
                "home",
                "tmpdir",
                "cache_dir",
                "project_context",
            ):
                payload.pop(field, None)
            projects = self._require_registry().projects()
            payload["projects"] = {
                "count": len(projects),
                "ids": [project.project_id for project in projects],
                "available": sum(1 for project in projects if project.available),
            }
            return payload

        return routed

    def _wrap_request_permissions_handler(self, next_handler: ToolHandler) -> ToolHandler:
        def routed(args: dict[str, Any]) -> dict[str, Any]:
            raw_target = args.get("arguments")
            if not isinstance(raw_target, dict):
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "arguments must be an object.",
                    category="validation",
                )
            project_id = raw_target.get("project_id")
            if not isinstance(project_id, str) or not project_id:
                raise ToolFailure(
                    "INVALID_ARGUMENT",
                    "arguments.project_id is required for project-scoped permission requests.",
                    category="validation",
                )
            clean_target = dict(raw_target)
            clean_target.pop("project_id", None)
            clean_outer = dict(args)
            clean_outer["arguments"] = clean_target
            payload = self._require_runtimes().invoke(
                project_id,
                next_handler,
                clean_outer,
                target_workdir=str(clean_target.get("workdir", ".")),
            )
            return self._restore_permission_target(payload, project_id)

        return routed

    @staticmethod
    def _restore_permission_target(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
        restored = dict(payload)

        def restore_requested(container: object) -> object:
            if not isinstance(container, dict):
                return container
            copied = dict(container)
            raw_requested = copied.get("requested")
            if isinstance(raw_requested, dict):
                requested = dict(raw_requested)
                raw_arguments = requested.get("arguments")
                if isinstance(raw_arguments, dict):
                    arguments = dict(raw_arguments)
                    arguments["project_id"] = project_id
                    requested["arguments"] = arguments
                copied["requested"] = requested
            return copied

        if "constraints" in restored:
            restored["constraints"] = restore_requested(restored["constraints"])
        raw_error = restored.get("error")
        if isinstance(raw_error, dict):
            error = dict(raw_error)
            if "details" in error:
                error["details"] = restore_requested(error["details"])
            restored["error"] = error
        return restored

    def _require_registry_project(self, project_id: str) -> None:
        try:
            self._require_registry().require_available(project_id)
        except ProjectRegistryError as exc:
            self._raise_registry_failure(exc)

    @staticmethod
    def _empty_command_list(*, project_id: str) -> dict[str, Any]:
        return {
            "commands": [],
            "count": 0,
            "total": 0,
            "truncated": False,
            "pending": False,
            "project_id": project_id,
            "ok": True,
            "warnings": [],
        }

    @staticmethod
    def _annotate_command_list(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
        annotated = dict(payload)
        raw_commands = annotated.get("commands")
        if isinstance(raw_commands, list):
            annotated["commands"] = [
                {**item, "project_id": project_id} if isinstance(item, dict) else item
                for item in raw_commands
            ]
        annotated["project_id"] = project_id
        return annotated

    @staticmethod
    def _restore_project_addressing(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
        restored = dict(payload)
        restored["project_id"] = project_id

        def restore_action(raw_action: object) -> object:
            if not isinstance(raw_action, dict):
                return raw_action
            action = dict(raw_action)
            tool = action.get("tool")
            raw_arguments = action.get("arguments")
            if tool in PROJECT_SCOPED_PUBLIC_TOOLS and isinstance(raw_arguments, dict):
                arguments = dict(raw_arguments)
                arguments["project_id"] = project_id
                action["arguments"] = arguments
            return action

        if "next_action" in restored:
            restored["next_action"] = restore_action(restored["next_action"])
        raw_actions = restored.get("next_actions")
        if isinstance(raw_actions, list):
            restored["next_actions"] = [restore_action(action) for action in raw_actions]
        return restored

    @staticmethod
    def _restore_project_failure(exc: ToolFailure, project_id: str) -> None:
        raw_recovery = exc.details.get("recovery")
        if not isinstance(raw_recovery, dict) or raw_recovery.get("kind") != "call_tool":
            return
        tool = raw_recovery.get("tool")
        raw_arguments = raw_recovery.get("arguments")
        if not isinstance(tool, str) or not isinstance(raw_arguments, dict):
            return
        needs_project = tool in PROJECT_SCOPED_PUBLIC_TOOLS or (
            tool in {"get_command", "list_commands"} and "client_request_id" in raw_arguments
        )
        if not needs_project:
            return
        recovery = dict(raw_recovery)
        arguments = dict(raw_arguments)
        arguments.setdefault("project_id", project_id)
        recovery["arguments"] = arguments
        exc.details["recovery"] = recovery

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

    @staticmethod
    def _git_summary(root: Path) -> dict[str, Any]:
        git = shutil.which("git")
        if git is None:
            return {"available": False, "is_repo": False, "warnings": ["git executable is unavailable"]}

        def run(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [git, "-C", str(root), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )

        inside = run("rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {"available": True, "is_repo": False, "warnings": []}

        head = run("rev-parse", "--short=12", "HEAD")
        branch = run("symbolic-ref", "--short", "-q", "HEAD")
        status = run("status", "--porcelain=v1", "--untracked-files=no")
        upstream = run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        ahead = 0
        behind = 0
        upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else ""
        if upstream_name:
            counts = run("rev-list", "--left-right", "--count", f"{upstream_name}...HEAD")
            if counts.returncode == 0:
                fields = counts.stdout.strip().split()
                if len(fields) == 2 and all(field.isdigit() for field in fields):
                    behind, ahead = (int(fields[0]), int(fields[1]))
        return {
            "available": True,
            "is_repo": True,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
            "head": head.stdout.strip() if head.returncode == 0 else "",
            "upstream": upstream_name,
            "ahead": ahead,
            "behind": behind,
            "clean_tracked": status.returncode == 0 and not status.stdout.strip(),
            "warnings": [],
        }

    def _semantic_configured(self, project_id: str) -> bool | None:
        snapshot = self._snapshot
        if snapshot is None:
            return None
        effective = snapshot.projects.get(project_id)
        if effective is None:
            return None
        return "semantic" in effective.enabled_capabilities

    def project_context(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        runtime = self._require_runtimes().require(project_id)
        project = runtime.project
        skills = runtime.skills.list_for(".")
        warnings = [*project.warnings, *runtime.catalog.warnings, *skills.warnings]
        return {
            "ok": True,
            "project_id": project_id,
            "available": project.available,
            "markers": list(project.markers),
            "git": self._git_summary(project.root),
            "semantic": {"configured": self._semantic_configured(project_id)},
            "instructions": {
                "instruction_files": list(skills.instruction_files[:32]),
                "skill_count": len(skills.skills),
                "warning_count": len(skills.warnings),
            },
            "warnings": warnings[:32],
        }

    @staticmethod
    def _doctor_check(
        check_id: str,
        status: str,
        summary: str,
        *,
        details: Mapping[str, Any] | None = None,
        recovery: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        check: dict[str, Any] = {"id": check_id, "status": status, "summary": summary}
        if details:
            check["details"] = dict(details)
        if recovery:
            check["recovery"] = dict(recovery)
        return check

    def doctor(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = args.get("project_id")
        checks: list[dict[str, Any]] = []
        registry = self._require_registry()
        snapshot = self._snapshot
        config_warnings = tuple(snapshot.warnings) if snapshot is not None else ()
        checks.append(
            self._doctor_check(
                "configuration",
                "warn" if config_warnings else "pass",
                "Configuration is loaded with warnings." if config_warnings else "Configuration is valid.",
                details={"warning_count": len(config_warnings)},
            )
        )
        checks.append(
            self._doctor_check(
                "project_registry",
                "pass" if registry.projects() else "warn",
                f"{len(registry.projects())} project(s) are registered.",
            )
        )

        payload: dict[str, Any] = {"ok": True, "checks": checks, "warnings": []}
        if project_id is None:
            return payload
        selected_id = str(project_id)
        runtime = self._require_runtimes().require(selected_id)
        project = runtime.project
        payload["project_id"] = selected_id
        checks.append(
            self._doctor_check(
                "project_root",
                "pass" if project.root.is_dir() else "fail",
                "Registered project root is accessible." if project.root.is_dir() else "Registered project root is unavailable.",
            )
        )
        git = self._git_summary(project.root)
        git_status = "pass" if git.get("is_repo") else "warn"
        checks.append(
            self._doctor_check(
                "git",
                git_status,
                "Git repository is available." if git_status == "pass" else "Project root is not a usable Git worktree.",
                details={
                    "available": git.get("available"),
                    "is_repo": git.get("is_repo"),
                },
            )
        )
        skills = runtime.skills.list_for(".")
        checks.append(
            self._doctor_check(
                "instructions_skills",
                "warn" if skills.warnings else "pass",
                "Instructions/skills parsed with warnings." if skills.warnings else "Instructions/skills parsed successfully.",
                details={"skill_count": len(skills.skills), "warning_count": len(skills.warnings)},
            )
        )
        payload["ok"] = not any(check["status"] == "fail" for check in checks)
        return payload

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
