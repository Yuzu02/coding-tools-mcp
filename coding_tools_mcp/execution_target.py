from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ExecutionTarget:
    """Canonical logical/physical target for one project-scoped operation."""

    project_id: str
    root: Path
    workdir: Path
    relative_workdir: str


_CURRENT_EXECUTION_TARGET: contextvars.ContextVar[ExecutionTarget | None] = contextvars.ContextVar(
    "coding_tools_mcp_execution_target",
    default=None,
)


@contextmanager
def bind_execution_target(target: ExecutionTarget) -> Iterator[None]:
    token = _CURRENT_EXECUTION_TARGET.set(target)
    try:
        yield
    finally:
        _CURRENT_EXECUTION_TARGET.reset(token)


def current_execution_target() -> ExecutionTarget | None:
    return _CURRENT_EXECUTION_TARGET.get()
