from __future__ import annotations

import threading
from dataclasses import dataclass

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.execution_target import ExecutionTarget, bind_execution_target
from coding_tools_mcp.project_context import ProjectContext, load_project_context

from ..services import CapabilityKey, ResolvedPathLike, WorkspaceRuntimeHandle, WorkspaceRuntimeService
from .project_catalog import ProjectCatalog, build_project_catalog
from .registry import PROJECT_REGISTRY, ProjectRegistry, ProjectRegistryError, RegisteredProject
from .skill_catalog import SkillCatalog


_COMMAND_RECOVERY_HINT = (
    "This command_id has expired or never existed in the configured project runtimes. "
    "Retrying the same command_id cannot succeed. Start the work again with exec_command "
    "for the intended project_id and use the command_id it returns."
)


class CommandOwnershipIndex:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, project_id: str, command_id: str) -> None:
        with self._lock:
            existing = self._owners.get(command_id)
            if existing is not None and existing != project_id:
                raise RuntimeError(f"command ownership collision: {command_id}")
            self._owners[command_id] = project_id

    def remove(self, project_id: str, command_id: str) -> None:
        with self._lock:
            if self._owners.get(command_id) == project_id:
                self._owners.pop(command_id, None)

    def owner(self, command_id: str) -> str:
        with self._lock:
            owner = self._owners.get(command_id)
        if owner is None:
            raise ToolFailure(
                "COMMAND_NOT_FOUND",
                "Command is not retained by any configured project.",
                category="not_found",
                details={"retry_hint": _COMMAND_RECOVERY_HINT},
            )
        return owner


@dataclass(frozen=True)
class ProjectRuntime:
    project: RegisteredProject
    workspace: WorkspaceRuntimeHandle
    catalog: ProjectCatalog
    skills: SkillCatalog
    project_context: ProjectContext


def _project_failure(exc: ProjectRegistryError) -> ToolFailure:
    return ToolFailure(
        exc.code,
        exc.message,
        category="not_found" if exc.code in {"PROJECT_NOT_FOUND", "PROJECT_UNAVAILABLE"} else "validation",
    )


class ProjectRuntimeManager:
    def __init__(
        self,
        registry: ProjectRegistry,
        workspace_runtimes: WorkspaceRuntimeService,
    ) -> None:
        self.registry = registry
        self.workspace_runtimes = workspace_runtimes
        self.command_owners = CommandOwnershipIndex()
        self._runtimes: dict[str, ProjectRuntime] = {}
        self._lock = threading.RLock()
        self._closed = False

    def require(self, project_id: str) -> ProjectRuntime:
        try:
            project = self.registry.require_available(project_id)
        except ProjectRegistryError as exc:
            raise _project_failure(exc) from exc

        with self._lock:
            if self._closed:
                raise RuntimeError("project runtime manager is closed")
            existing = self._runtimes.get(project_id)
            if existing is not None:
                return existing

            handle = self.workspace_runtimes.create(
                project.root,
                excluded_roots=self.registry.excluded_roots_for(project_id),
                on_command_registered=lambda command_id: self.command_owners.register(project_id, command_id),
                on_command_removed=lambda command_id: self.command_owners.remove(project_id, command_id),
            )
            try:
                catalog = build_project_catalog(project.root)
                runtime = ProjectRuntime(
                    project=project,
                    workspace=handle,
                    catalog=catalog,
                    skills=SkillCatalog(catalog),
                    project_context=load_project_context(project.root),
                )
            except Exception:
                self.workspace_runtimes.close(handle)
                raise
            self._runtimes[project_id] = runtime
            return runtime

    def invoke(
        self,
        project_id: str,
        handler,
        arguments: dict[str, object],
        *,
        target_workdir: str | None = None,
    ) -> dict[str, object]:
        runtime = self.require(project_id)
        raw_workdir = target_workdir if target_workdir is not None else str(arguments.get("workdir", "."))
        target = self.resolve_target(project_id, raw_workdir, runtime=runtime)
        normalized_arguments = dict(arguments)
        if "workdir" in normalized_arguments:
            normalized_arguments["workdir"] = target.relative_workdir
        with bind_execution_target(target):
            return self.workspace_runtimes.invoke(runtime.workspace, handler, normalized_arguments)

    def resolve_target(
        self,
        project_id: str,
        raw_workdir: str = ".",
        *,
        runtime: ProjectRuntime | None = None,
    ) -> ExecutionTarget:
        selected = runtime or self.require(project_id)
        resolved = self.workspace_runtimes.resolve_existing(selected.workspace, raw_workdir)
        if not resolved.path.is_dir():
            raise ToolFailure(
                "NOT_A_DIRECTORY",
                "workdir must resolve to a directory inside the selected project.",
                category="validation",
            )
        return ExecutionTarget(
            project_id=project_id,
            root=selected.workspace.root,
            workdir=resolved.path,
            relative_workdir=resolved.display,
        )

    def resolve_existing(self, project_id: str, raw_path: str = ".") -> ResolvedPathLike:
        runtime = self.require(project_id)
        return self.workspace_runtimes.resolve_existing(runtime.workspace, raw_path)

    def active(self) -> tuple[ProjectRuntime, ...]:
        with self._lock:
            return tuple(self._runtimes.values())

    def active_for(self, project_id: str) -> ProjectRuntime | None:
        with self._lock:
            return self._runtimes.get(project_id)

    def close(self) -> tuple[str, ...]:
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()

        warnings: list[str] = []
        for runtime in runtimes:
            try:
                self.workspace_runtimes.close(runtime.workspace)
            except Exception as exc:  # noqa: BLE001 - shutdown must attempt every project
                warnings.append(f"{runtime.project.project_id}: {str(exc)[:512]}")
        return tuple(warnings)


PROJECT_RUNTIMES = CapabilityKey[ProjectRuntimeManager]("projects.runtimes")


__all__ = [
    "CommandOwnershipIndex",
    "PROJECT_REGISTRY",
    "PROJECT_RUNTIMES",
    "ProjectRuntime",
    "ProjectRuntimeManager",
]
