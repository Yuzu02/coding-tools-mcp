from __future__ import annotations

import contextvars
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .errors import ToolFailure


OPERATION_ID_MAX_LENGTH = 32
OPERATION_LABEL_MAX_LENGTH = 64


@dataclass(frozen=True)
class OperationObservabilitySnapshot:
    project_id: str | None = None
    worktree_id: str | None = None
    backend: str | None = None
    provider: str | None = None


class OperationObservability:
    """Closed non-secret labels attached to one request-owned operation.

    There is deliberately no generic ``set`` method: callers can only attach
    the bounded identities that the telemetry contract explicitly permits.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._project_id: str | None = None
        self._worktree_id: str | None = None
        self._backend: str | None = None
        self._provider: str | None = None

    @staticmethod
    def _bounded(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:OPERATION_LABEL_MAX_LENGTH] if text else None

    def set_project_id(self, value: str | None) -> None:
        with self._lock:
            self._project_id = self._bounded(value)

    def set_worktree_id(self, value: str | None) -> None:
        with self._lock:
            self._worktree_id = self._bounded(value)

    def set_backend(self, value: str | None) -> None:
        with self._lock:
            self._backend = self._bounded(value)

    def set_provider(self, value: str | None) -> None:
        with self._lock:
            self._provider = self._bounded(value)

    def snapshot(self) -> OperationObservabilitySnapshot:
        with self._lock:
            return OperationObservabilitySnapshot(
                project_id=self._project_id,
                worktree_id=self._worktree_id,
                backend=self._backend,
                provider=self._provider,
            )


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
    observability: OperationObservability


_CURRENT_OPERATION: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar(
    "coding_tools_mcp_operation_context",
    default=None,
)


def new_operation_context(request_id: str | int | None) -> OperationContext:
    operation_id = secrets.token_urlsafe(12)
    if len(operation_id) > OPERATION_ID_MAX_LENGTH:  # defensive if token encoding changes
        operation_id = operation_id[:OPERATION_ID_MAX_LENGTH]
    return OperationContext(
        operation_id=operation_id,
        request_id=request_id,
        cancellation=CancellationToken(),
        observability=OperationObservability(),
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


def observe_project_id(project_id: str | None) -> None:
    context = current_operation_context()
    if context is not None:
        context.observability.set_project_id(project_id)


def observe_backend(backend: str | None) -> None:
    context = current_operation_context()
    if context is not None:
        context.observability.set_backend(backend)


def observe_provider(provider: str | None) -> None:
    context = current_operation_context()
    if context is not None:
        context.observability.set_provider(provider)


def raise_if_operation_cancelled() -> None:
    context = current_operation_context()
    if context is not None:
        context.cancellation.raise_if_cancelled()
