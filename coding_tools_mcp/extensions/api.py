from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .config import ConfigNode, table


class ExtensionContext(Protocol):
    """Registration context contract completed by the contribution layer in Task 4."""


@dataclass(frozen=True)
class ExtensionManifest:
    name: str
    requires: tuple[str, ...] = ()
    description: str = ""
    config_schema: ConfigNode = field(default_factory=lambda: table({}))


class Extension(Protocol):
    manifest: ExtensionManifest

    def configure(self, config: Mapping[str, object]) -> None:
        raise NotImplementedError

    def register(self, context: ExtensionContext) -> None:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
