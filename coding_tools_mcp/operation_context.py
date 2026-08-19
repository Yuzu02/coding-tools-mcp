from __future__ import annotations

import contextvars
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .errors import ToolFailure


class CancellationToken:
    """Thread-safe request cancellation state shared with request-owned work."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ToolFailure(
                "OPERATION_CANCELLED",
                "The MCP request was cancelled.",
                category="cancelled",
                retryable=True,
            )


@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    request_id: str | int | None
    cancellation: CancellationToken


_CURRENT_OPERATION: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar(
    "coding_tools_mcp_operation_context",
    default=None,
)


def new_operation_context(request_id: str | int | None) -> OperationContext:
    return OperationContext(
        operation_id=secrets.token_urlsafe(12),
        request_id=request_id,
        cancellation=CancellationToken(),
    )


@contextmanager
def bind_operation_context(context: OperationContext | None) -> Iterator[None]:
    if context is None:
        yield
        return
    token = _CURRENT_OPERATION.set(context)
    try:
        yield
    finally:
        _CURRENT_OPERATION.reset(token)


def current_operation_context() -> OperationContext | None:
    return _CURRENT_OPERATION.get()


def raise_if_operation_cancelled() -> None:
    context = current_operation_context()
    if context is not None:
        context.cancellation.raise_if_cancelled()
