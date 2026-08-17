from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Sequence, cast

from .config_schema import (
    ConfigError,
    ConfigNode,
    freeze_mapping,
    list_of,
    read_toml,
    scalar,
    table,
    validate_node,
)
from .extensions.config import EXTENSION_NAME_RE, RuntimeConfig


HOST_CONFIG_VERSION = 2
PROJECT_CONFIG_VERSION = 1
ENV_SECRET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class SecretRef:
    scheme: Literal["env", "file"]
    target: str


def parse_secret_ref(raw: str) -> SecretRef:
    if raw.startswith("env:"):
        target = raw[4:]
        if ENV_SECRET_RE.fullmatch(target):
            return SecretRef(scheme="env", target=target)
    if raw.startswith("file:"):
        target = raw[5:]
        if target and Path(target).is_absolute():
            return SecretRef(scheme="file", target=target)
    raise ConfigError("secret reference must use env:NAME or file:/absolute/path")


def resolve_secret_ref(ref: SecretRef, *, environ: Mapping[str, str]) -> str:
    if ref.scheme == "env":
        value = environ.get(ref.target)
        if value is None:
            raise ConfigError("environment secret reference is unavailable")
        return value

    try:
        value = Path(ref.target).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("file secret reference is unavailable") from exc
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


def standard_host_config_path(*, environ: Mapping[str, str], home: Path) -> Path:
    raw_xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(raw_xdg).expanduser() if raw_xdg else home.expanduser() / ".config"
    return base / "coding-tools-mcp" / "config.toml"


@dataclass(frozen=True)
class HostRuntimeConfig:
    bootstrap_workspace: Path
    runtime_root: Path | None = None
    state_root: Path | None = None
    cache_root: Path | None = None
    enable_view_image: bool = False


@dataclass(frozen=True)
class HostTransportConfig:
    kind: str
    host: str
    port: int


@dataclass(frozen=True)
class HostSecurityConfig:
    permission_mode: str
    shell_env_inherit: str
    allow_network: bool
    auth_mode: str


@dataclass(frozen=True)
class HostTunnelConfig:
    mode: Literal["disabled", "profile", "profile-file", "generated"]
    client: str = "tunnel-client"
    profile_file: Path | None = None
    health_listen_addr: str = "127.0.0.1:0"


@dataclass(frozen=True)
class HostDeploymentConfig:
    mcp_repository: Path | None
    sync: bool
    sync_extras: tuple[str, ...]
    startup_timeout_seconds: float
    shutdown_timeout_seconds: float
    poll_interval_seconds: float
    logs_root: Path | None
    tunnel: HostTunnelConfig


@dataclass(frozen=True)
class HostConfig:
    config_version: int
    runtime: HostRuntimeConfig | None
    transport: HostTransportConfig
    security: HostSecurityConfig
    extensions: RuntimeConfig
    deployment: HostDeploymentConfig
    source: Path


def host_config_schema(extension_schemas: Mapping[str, ConfigNode]) -> ConfigNode:
    return table(
        {
            "config_version": scalar(int),
            "runtime": table(
                {
                    "bootstrap_workspace": scalar(str),
                    "runtime_root": scalar(str),
                    "state_root": scalar(str),
                    "cache_root": scalar(str),
                    "enable_view_image": scalar(bool),
                }
            ),
            "transport": table(
                {
                    "kind": scalar(str),
                    "host": scalar(str),
                    "port": scalar(int),
                }
            ),
            "security": table(
                {
                    "permission_mode": scalar(str),
                    "shell_env_inherit": scalar(str),
                    "allow_network": scalar(bool),
                    "auth_mode": scalar(str),
                }
            ),
            "extensions": table(
                {
                    "enabled": list_of(scalar(str)),
                    **extension_schemas,
                }
            ),
            "deployment": table(
                {
                    "mcp_repository": scalar(str),
                    "sync": scalar(bool),
                    "sync_extras": list_of(scalar(str)),
                    "startup_timeout_seconds": scalar(int, float),
                    "shutdown_timeout_seconds": scalar(int, float),
                    "poll_interval_seconds": scalar(int, float),
                    "logs_root": scalar(str),
                    "tunnel": table(
                        {
                            "mode": scalar(str),
                            "client": scalar(str),
                            "profile_file": scalar(str),
                            "health_listen_addr": scalar(str),
                        }
                    ),
                }
            ),
        }
    )


def _absolute_path(raw: object, *, field_name: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{field_name} must be a non-empty absolute path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"{field_name} must be an absolute path")
    return path.resolve(strict=False)


def _loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _optional_absolute_path(raw: object | None, *, field_name: str) -> Path | None:
    if raw is None:
        return None
    return _absolute_path(raw, field_name=field_name)


def _positive_number(raw: object, *, field_name: str) -> float:
    if type(raw) not in {int, float} or float(cast(int | float, raw)) <= 0:
        raise ConfigError(f"{field_name} must be a positive number")
    return float(cast(int | float, raw))


def _normalize_extensions(
    data: Mapping[str, object],
    *,
    extension_schemas: Mapping[str, ConfigNode],
    default_enabled: Sequence[str],
    source: Path,
) -> RuntimeConfig:
    raw_extensions = data.get("extensions")
    extension_map = raw_extensions if isinstance(raw_extensions, dict) else {}
    raw_enabled = extension_map.get("enabled", list(default_enabled))
    if not isinstance(raw_enabled, list):
        raise ConfigError("host.extensions.enabled must be a list")

    enabled: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_enabled:
        if not isinstance(raw_name, str) or EXTENSION_NAME_RE.fullmatch(raw_name) is None:
            raise ConfigError(f"invalid extension name: {raw_name}")
        if raw_name in seen:
            raise ConfigError(f"duplicate enabled extension: {raw_name}")
        if raw_name not in extension_schemas:
            raise ConfigError(f"unknown extension: {raw_name}")
        seen.add(raw_name)
        enabled.append(raw_name)

    settings = {
        name: freeze_mapping(cast(Mapping[str, object], extension_map.get(name, {})))
        for name in extension_schemas
    }
    return RuntimeConfig(
        config_version=1,
        enabled_extensions=tuple(enabled),
        extension_settings=MappingProxyType(settings),
        sources=(source,),
    )


def _normalize_tunnel(data: Mapping[str, object]) -> HostTunnelConfig:
    raw_tunnel = data.get("tunnel")
    tunnel = raw_tunnel if isinstance(raw_tunnel, dict) else {}
    mode = str(tunnel.get("mode", "disabled"))
    if mode not in {"disabled", "profile", "profile-file", "generated"}:
        raise ConfigError("host.deployment.tunnel.mode is invalid")
    profile_file = _optional_absolute_path(
        tunnel.get("profile_file"),
        field_name="host.deployment.tunnel.profile_file",
    )
    if mode == "profile-file" and profile_file is None:
        raise ConfigError("host.deployment.tunnel.profile_file is required for profile-file mode")
    return HostTunnelConfig(
        mode=cast(Literal["disabled", "profile", "profile-file", "generated"], mode),
        client=str(tunnel.get("client", "tunnel-client")),
        profile_file=profile_file,
        health_listen_addr=str(tunnel.get("health_listen_addr", "127.0.0.1:0")),
    )


def load_host_config(
    path: Path,
    *,
    extension_schemas: Mapping[str, ConfigNode],
    default_enabled: Sequence[str],
) -> HostConfig:
    resolved = path.expanduser().resolve()
    data = read_toml(resolved)
    if data.get("config_version") != HOST_CONFIG_VERSION:
        raise ConfigError(f"{resolved}: config_version must be {HOST_CONFIG_VERSION}")
    validate_node(data, host_config_schema(extension_schemas), "host")

    runtime_data = data.get("runtime")
    runtime: HostRuntimeConfig | None = None
    if isinstance(runtime_data, dict):
        bootstrap = runtime_data.get("bootstrap_workspace")
        if bootstrap is None:
            raise ConfigError("host.runtime.bootstrap_workspace is required")
        runtime = HostRuntimeConfig(
            bootstrap_workspace=_absolute_path(
                bootstrap,
                field_name="host.runtime.bootstrap_workspace",
            ),
            runtime_root=_optional_absolute_path(
                runtime_data.get("runtime_root"),
                field_name="host.runtime.runtime_root",
            ),
            state_root=_optional_absolute_path(
                runtime_data.get("state_root"),
                field_name="host.runtime.state_root",
            ),
            cache_root=_optional_absolute_path(
                runtime_data.get("cache_root"),
                field_name="host.runtime.cache_root",
            ),
            enable_view_image=bool(runtime_data.get("enable_view_image", False)),
        )

    transport_data = data.get("transport")
    transport_map = transport_data if isinstance(transport_data, dict) else {}
    kind = str(transport_map.get("kind", "http"))
    if kind not in {"http", "stdio"}:
        raise ConfigError("host.transport.kind must be 'http' or 'stdio'")
    host = str(transport_map.get("host", "127.0.0.1"))
    port = transport_map.get("port", 8000)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("host.transport.port must be an integer between 1 and 65535")
    transport = HostTransportConfig(kind=kind, host=host, port=port)

    security_data = data.get("security")
    security_map = security_data if isinstance(security_data, dict) else {}
    permission_mode = str(security_map.get("permission_mode", "safe"))
    if permission_mode not in {"safe", "dangerous"}:
        raise ConfigError("host.security.permission_mode must be 'safe' or 'dangerous'")
    shell_env_inherit = str(security_map.get("shell_env_inherit", "filtered"))
    if shell_env_inherit not in {"filtered", "all", "none"}:
        raise ConfigError("host.security.shell_env_inherit must be 'filtered', 'all', or 'none'")
    allow_network = security_map.get("allow_network", False)
    if type(allow_network) is not bool:
        raise ConfigError("host.security.allow_network must be a bool")
    auth_mode = str(security_map.get("auth_mode", "noauth"))
    if auth_mode not in {"noauth", "bearer", "oauth"}:
        raise ConfigError("host.security.auth_mode must be 'noauth', 'bearer', or 'oauth'")
    security = HostSecurityConfig(
        permission_mode=permission_mode,
        shell_env_inherit=shell_env_inherit,
        allow_network=allow_network,
        auth_mode=auth_mode,
    )

    if transport.kind == "http" and security.auth_mode == "noauth" and not _loopback_host(host):
        raise ConfigError("unauthenticated HTTP HostConfig requires a loopback host")

    extensions = _normalize_extensions(
        data,
        extension_schemas=extension_schemas,
        default_enabled=default_enabled,
        source=resolved,
    )

    deployment_data = data.get("deployment")
    deployment_map = deployment_data if isinstance(deployment_data, dict) else {}
    deployment = HostDeploymentConfig(
        mcp_repository=_optional_absolute_path(
            deployment_map.get("mcp_repository"),
            field_name="host.deployment.mcp_repository",
        ),
        sync=bool(deployment_map.get("sync", True)),
        sync_extras=tuple(cast(Sequence[str], deployment_map.get("sync_extras", ()))),
        startup_timeout_seconds=_positive_number(
            deployment_map.get("startup_timeout_seconds", 60),
            field_name="host.deployment.startup_timeout_seconds",
        ),
        shutdown_timeout_seconds=_positive_number(
            deployment_map.get("shutdown_timeout_seconds", 10),
            field_name="host.deployment.shutdown_timeout_seconds",
        ),
        poll_interval_seconds=_positive_number(
            deployment_map.get("poll_interval_seconds", 0.25),
            field_name="host.deployment.poll_interval_seconds",
        ),
        logs_root=_optional_absolute_path(
            deployment_map.get("logs_root"),
            field_name="host.deployment.logs_root",
        ),
        tunnel=_normalize_tunnel(deployment_map),
    )

    return HostConfig(
        config_version=HOST_CONFIG_VERSION,
        runtime=runtime,
        transport=transport,
        security=security,
        extensions=extensions,
        deployment=deployment,
        source=resolved,
    )
