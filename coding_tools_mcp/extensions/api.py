from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .config import ConfigNode, table
from .contributions import (
    ContributionRegistry,
    ServerMetadataContribution,
    ToolContribution,
    ToolDecorator,
)
from .services import ServiceRegistry


@dataclass(frozen=True)
class ExtensionContext:
    services: ServiceRegistry
    contributions: ContributionRegistry
    extension_name: str

    def add_tool(self, tool: ToolContribution) -> None:
        self.contributions.add_tool(self.extension_name, tool)

    def add_decorator(self, decorator: ToolDecorator) -> None:
        self.contributions.add_decorator(self.extension_name, decorator)

    def add_metadata(self, key: str, value: object) -> None:
        self.contributions.add_metadata(
            self.extension_name,
            ServerMetadataContribution(key=key, value=value),
        )


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
