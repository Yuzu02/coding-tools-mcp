from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ToolFailure
from .execution_target import ExecutionTarget, current_execution_target
from .operation_context import current_operation_context


@dataclass(frozen=True)
class ChildOperationTarget:
    """Canonical target/operation identity passed to child-process policy code."""

    execution: ExecutionTarget
    operation_id: str | None

    @property
    def root(self) -> Path:
        return self.execution.root

    @property
    def workdir(self) -> Path:
        return self.execution.workdir

    @property
    def project_id(self) -> str:
        return self.execution.project_id


def child_operation_target(root: Path, workdir: Path) -> ChildOperationTarget:
    """Resolve the shared child-operation target without re-reading user input."""

    canonical_root = root.resolve(strict=True)
    canonical_workdir = workdir.resolve(strict=True)
    bound = current_execution_target()
    if bound is not None:
        if bound.root != canonical_root or bound.workdir != canonical_workdir:
            raise ToolFailure(
                "INVALID_ARGUMENT",
                "Resolved execution target does not match the bound project operation.",
                category="security",
            )
        execution = bound
    else:
        try:
            relative = canonical_workdir.relative_to(canonical_root).as_posix()
        except ValueError as exc:
            raise ToolFailure(
                "PATH_OUTSIDE_WORKSPACE",
                "Child workdir escapes the configured workspace.",
                category="security",
            ) from exc
        execution = ExecutionTarget(
            project_id="default",
            root=canonical_root,
            workdir=canonical_workdir,
            relative_workdir="." if relative == "." else relative,
        )
    operation = current_operation_context()
    return ChildOperationTarget(
        execution=execution,
        operation_id=operation.operation_id if operation is not None else None,
    )


__all__ = ["ChildOperationTarget", "child_operation_target"]
