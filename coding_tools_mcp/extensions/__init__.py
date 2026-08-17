"""Fork-owned internal extension architecture."""

from .api import Extension, ExtensionManifest
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
from .registry import ExtensionRegistry, ExtensionRegistryError
from .services import CORE_WORKSPACE, CapabilityKey, ServiceRegistry, ServiceRegistryError, WorkspaceAccess

__all__ = [
    "ConfigError",
    "ConfigNode",
    "CORE_WORKSPACE",
    "CapabilityKey",
    "Extension",
    "ExtensionManifest",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "RuntimeConfig",
    "ServiceRegistry",
    "ServiceRegistryError",
    "WorkspaceAccess",
    "list_of",
    "load_runtime_config",
    "map_of",
    "parse_extension_list",
    "resolve_config_paths",
    "scalar",
    "table",
]
