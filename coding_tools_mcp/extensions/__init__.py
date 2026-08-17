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
    ServerInstructionsContribution,
    ServerMetadataContribution,
    ToolAnnotations,
    ToolContribution,
    ToolDecorator,
    compose_tools,
)
from .host import ExtensionHost
from .registry import ExtensionRegistry, ExtensionRegistryError
from .services import (
    CORE_WORKSPACE,
    CORE_WORKSPACE_RUNTIMES,
    CapabilityKey,
    ServiceRegistry,
    ServiceRegistryError,
    WorkspaceAccess,
    WorkspaceRuntimeHandle,
    WorkspaceRuntimeService,
)


def builtin_extension_registry() -> ExtensionRegistry:
    """Return the fork's statically registered built-in extensions."""

    from .projects import ProjectsExtension

    return ExtensionRegistry([ProjectsExtension], default_enabled=("projects",))


__all__ = [
    "ConfigError",
    "ConfigNode",
    "CORE_WORKSPACE",
    "CORE_WORKSPACE_RUNTIMES",
    "CapabilityKey",
    "ComposedTool",
    "ContributionError",
    "ContributionRegistry",
    "Extension",
    "ExtensionContext",
    "ExtensionHost",
    "ExtensionManifest",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "RuntimeConfig",
    "SchemaPatch",
    "ServerInstructionsContribution",
    "ServerMetadataContribution",
    "ServiceRegistry",
    "ServiceRegistryError",
    "WorkspaceAccess",
    "WorkspaceRuntimeHandle",
    "WorkspaceRuntimeService",
    "ToolAnnotations",
    "ToolContribution",
    "ToolDecorator",
    "builtin_extension_registry",
    "compose_tools",
    "list_of",
    "load_runtime_config",
    "map_of",
    "parse_extension_list",
    "resolve_config_paths",
    "scalar",
    "table",
]
