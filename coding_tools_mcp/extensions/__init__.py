"""Fork-owned internal extension architecture."""

from .api import Extension, ExtensionContext, ExtensionManifest
from .config import (
    ConfigError,
    ConfigNode,
    RuntimeConfig,
    list_of,
    load_runtime_config,
    map_of,
    parse_extension_list,
    resolve_config_paths,
    scalar,
    table,
)
from .contributions import (
    ComposedTool,
    ContributionError,
    ContributionRegistry,
    SchemaPatch,
    ServerMetadataContribution,
    ToolAnnotations,
    ToolContribution,
    ToolDecorator,
    compose_tools,
)
from .registry import ExtensionRegistry, ExtensionRegistryError
from .services import CORE_WORKSPACE, CapabilityKey, ServiceRegistry, ServiceRegistryError, WorkspaceAccess

__all__ = [
    "ConfigError",
    "ConfigNode",
    "CORE_WORKSPACE",
    "CapabilityKey",
    "ComposedTool",
    "ContributionError",
    "ContributionRegistry",
    "Extension",
    "ExtensionContext",
    "ExtensionManifest",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "RuntimeConfig",
    "SchemaPatch",
    "ServerMetadataContribution",
    "ServiceRegistry",
    "ServiceRegistryError",
    "WorkspaceAccess",
    "ToolAnnotations",
    "ToolContribution",
    "ToolDecorator",
    "compose_tools",
    "list_of",
    "load_runtime_config",
    "map_of",
    "parse_extension_list",
    "resolve_config_paths",
    "scalar",
    "table",
]
