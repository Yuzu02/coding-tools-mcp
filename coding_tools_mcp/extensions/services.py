from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, cast


T = TypeVar("T")


@dataclass(frozen=True)
class CapabilityKey(Generic[T]):
    name: str


class ServiceRegistryError(RuntimeError):
    """Raised when extension service publication or lookup is invalid."""


class ServiceRegistry:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._frozen = False

    def provide(self, key: CapabilityKey[T], value: T) -> None:
        if self._frozen:
            raise ServiceRegistryError("service registry is frozen")
        if key.name in self._values:
            raise ServiceRegistryError(f"duplicate capability provider: {key.name}")
        self._values[key.name] = value

    def require(self, key: CapabilityKey[T]) -> T:
        try:
            value = self._values[key.name]
        except KeyError as exc:
            raise ServiceRegistryError(f"required capability unavailable: {key.name}") from exc
        return cast(T, value)

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen


class ResolvedPathLike(Protocol):
    display: str
    path: Path


class WorkspaceAccess(Protocol):
    root: Path

    def resolve_existing(self, raw_path: str = ".") -> ResolvedPathLike:
        raise NotImplementedError


WorkspaceToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
CommandLifecycleCallback = Callable[[str], None]


class WorkspaceRuntimeHandle(Protocol):
    @property
    def root(self) -> Path:
        raise NotImplementedError

    @property
    def runtime_dir(self) -> Path:
        raise NotImplementedError


class WorkspaceRuntimeService(Protocol):
    def validate_root(self, root: Path, *, require_exists: bool = True) -> Path:
        raise NotImplementedError

    def create(
        self,
        root: Path,
        *,
        excluded_roots: tuple[Path, ...] = (),
        on_command_registered: CommandLifecycleCallback | None = None,
        on_command_removed: CommandLifecycleCallback | None = None,
    ) -> WorkspaceRuntimeHandle:
        raise NotImplementedError

    def invoke(
        self,
        handle: WorkspaceRuntimeHandle,
        handler: WorkspaceToolHandler,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def resolve_existing(
        self,
        handle: WorkspaceRuntimeHandle,
        raw_path: str = ".",
    ) -> ResolvedPathLike:
        raise NotImplementedError

    def close(self, handle: WorkspaceRuntimeHandle) -> None:
        raise NotImplementedError


CORE_WORKSPACE = CapabilityKey[WorkspaceAccess]("core.workspace")
CORE_WORKSPACE_RUNTIMES = CapabilityKey[WorkspaceRuntimeService]("core.workspace_runtimes")
