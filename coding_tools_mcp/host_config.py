from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from enum import StrEnum
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
PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
DEFAULT_PROJECT_CONFIG = ".coding-tools-mcp.toml"
PROJECT_REDUCIBLE_CAPABILITIES = frozenset({"semantic"})


class AuthorityKind(StrEnum):
    HOST_ONLY = "host-only"
    PROJECT_SELECT_FROM_HOST_SET = "project-select-from-host-set"
    PROJECT_NARROW_HOST_LIMIT = "project-narrow-host-limit"
    PROJECT_PROVIDE_DATA_UNDER_HOST_POLICY = "project-provide-data-under-host-policy"


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
    auth_token_ref: SecretRef | None = None
    oauth_client_id: str | None = None
    oauth_client_secret_ref: SecretRef | None = None
    oauth_password_ref: SecretRef | None = None
    oauth_token_secret_ref: SecretRef | None = None
    oauth_server_url: str | None = None
    oauth_redirect_uris: tuple[str, ...] = ()
    oauth_token_ttl_seconds: int | None = None


@dataclass(frozen=True)
class HostTunnelConfig:
    mode: Literal["disabled", "profile", "profile-file", "generated"]
    client: str = "tunnel-client"
    profile: str | None = None
    profile_dir: Path | None = None
    profile_file: Path | None = None
    health_listen_addr: str = "127.0.0.1:0"
    tunnel_id: str | None = None
    api_key_ref: SecretRef | None = None
    control_plane_base_url: str = "https://api.openai.com"
    control_plane_url_path: str | None = None
    mcp_server_url: str | None = None
    generated_profile_name: str | None = None
    write_profile: Path | None = None
    force_profile_write: bool = False
    open_web_ui: bool = False


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


def credential_registry_dir(config: HostConfig) -> Path:
    return config.source.parent / "credentials.d"


def credential_broker_dir(config: HostConfig) -> Path:
    runtime = config.runtime
    if runtime is None or runtime.state_root is None:
        raise ConfigError("HostConfig runtime.state_root is required for credential providers")
    return runtime.state_root / "credentials"


@dataclass(frozen=True)
class RegisteredProjectConfig:
    project_id: str
    root: Path
    allow_unavailable: bool = False
    project_config: str | None = None


@dataclass(frozen=True)
class ProjectCapabilities:
    disabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectConfig:
    project_config_version: int
    capabilities: ProjectCapabilities
    source: Path


@dataclass(frozen=True)
class EffectiveProjectConfig:
    project_id: str
    root: Path
    allow_unavailable: bool
    source: Path | None
    enabled_capabilities: frozenset[str]
    disabled_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ConfigSource:
    role: str
    path: Path


@dataclass(frozen=True)
class ConfigSnapshot:
    resolution_mode: Literal["host", "developer"]
    sources: tuple[ConfigSource, ...]
    host_config: HostConfig | None
    runtime_config: RuntimeConfig
    registered_projects: tuple[RegisteredProjectConfig, ...]
    projects: Mapping[str, EffectiveProjectConfig]
    config_versions: Mapping[str, int]
    warnings: tuple[str, ...]
    secret_references: Mapping[str, str]
    fingerprint: str


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
                    "auth_token_ref": scalar(str),
                    "oauth_client_id": scalar(str),
                    "oauth_client_secret_ref": scalar(str),
                    "oauth_password_ref": scalar(str),
                    "oauth_token_secret_ref": scalar(str),
                    "oauth_server_url": scalar(str),
                    "oauth_redirect_uris": list_of(scalar(str)),
                    "oauth_token_ttl_seconds": scalar(int),
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
                            "profile": scalar(str),
                            "profile_dir": scalar(str),
                            "profile_file": scalar(str),
                            "health_listen_addr": scalar(str),
                            "tunnel_id": scalar(str),
                            "api_key_ref": scalar(str),
                            "control_plane_base_url": scalar(str),
                            "control_plane_url_path": scalar(str),
                            "mcp_server_url": scalar(str),
                            "generated_profile_name": scalar(str),
                            "write_profile": scalar(str),
                            "force_profile_write": scalar(bool),
                            "open_web_ui": scalar(bool),
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
    profile = tunnel.get("profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ConfigError("host.deployment.tunnel.profile must be a non-empty string")
    if mode == "profile" and profile is None:
        raise ConfigError("host.deployment.tunnel.profile is required for profile mode")
    tunnel_id = tunnel.get("tunnel_id")
    if tunnel_id is not None and (not isinstance(tunnel_id, str) or not tunnel_id.strip()):
        raise ConfigError("host.deployment.tunnel.tunnel_id must be a non-empty string")
    api_key_ref = (
        parse_secret_ref(cast(str, tunnel["api_key_ref"]))
        if "api_key_ref" in tunnel
        else None
    )
    if mode == "generated" and (tunnel_id is None or api_key_ref is None):
        raise ConfigError(
            "host.deployment.tunnel.generated mode requires tunnel_id and api_key_ref"
        )
    return HostTunnelConfig(
        mode=cast(Literal["disabled", "profile", "profile-file", "generated"], mode),
        client=str(tunnel.get("client", "tunnel-client")),
        profile=cast(str | None, profile),
        profile_dir=_optional_absolute_path(
            tunnel.get("profile_dir"),
            field_name="host.deployment.tunnel.profile_dir",
        ),
        profile_file=profile_file,
        health_listen_addr=str(tunnel.get("health_listen_addr", "127.0.0.1:0")),
        tunnel_id=cast(str | None, tunnel_id),
        api_key_ref=api_key_ref,
        control_plane_base_url=str(
            tunnel.get("control_plane_base_url", "https://api.openai.com")
        ),
        control_plane_url_path=cast(str | None, tunnel.get("control_plane_url_path")),
        mcp_server_url=cast(str | None, tunnel.get("mcp_server_url")),
        generated_profile_name=cast(str | None, tunnel.get("generated_profile_name")),
        write_profile=_optional_absolute_path(
            tunnel.get("write_profile"),
            field_name="host.deployment.tunnel.write_profile",
        ),
        force_profile_write=bool(tunnel.get("force_profile_write", False)),
        open_web_ui=bool(tunnel.get("open_web_ui", False)),
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
    raw_auth_token_ref = security_map.get("auth_token_ref")
    auth_token_ref = (
        parse_secret_ref(cast(str, raw_auth_token_ref)) if raw_auth_token_ref is not None else None
    )
    if auth_mode == "bearer" and auth_token_ref is None:
        raise ConfigError("host.security.auth_token_ref is required for bearer auth")

    def optional_secret_ref(name: str) -> SecretRef | None:
        raw = security_map.get(name)
        return parse_secret_ref(cast(str, raw)) if raw is not None else None

    oauth_client_id = security_map.get("oauth_client_id")
    if oauth_client_id is not None and (
        not isinstance(oauth_client_id, str) or not oauth_client_id.strip()
    ):
        raise ConfigError("host.security.oauth_client_id must be a non-empty string")
    oauth_server_url = security_map.get("oauth_server_url")
    if oauth_server_url is not None and (
        not isinstance(oauth_server_url, str) or not oauth_server_url.strip()
    ):
        raise ConfigError("host.security.oauth_server_url must be a non-empty string")
    raw_redirect_uris = security_map.get("oauth_redirect_uris", [])
    oauth_redirect_uris = tuple(cast(Sequence[str], raw_redirect_uris))
    if any(not item.strip() for item in oauth_redirect_uris):
        raise ConfigError("host.security.oauth_redirect_uris cannot contain empty values")
    raw_oauth_ttl = security_map.get("oauth_token_ttl_seconds")
    oauth_token_ttl_seconds: int | None = None
    if raw_oauth_ttl is not None:
        if type(raw_oauth_ttl) is not int or not 60 <= cast(int, raw_oauth_ttl) <= 604_800:
            raise ConfigError(
                "host.security.oauth_token_ttl_seconds must be between 60 and 604800 seconds"
            )
        oauth_token_ttl_seconds = cast(int, raw_oauth_ttl)
    security = HostSecurityConfig(
        permission_mode=permission_mode,
        shell_env_inherit=shell_env_inherit,
        allow_network=allow_network,
        auth_mode=auth_mode,
        auth_token_ref=auth_token_ref,
        oauth_client_id=cast(str | None, oauth_client_id),
        oauth_client_secret_ref=optional_secret_ref("oauth_client_secret_ref"),
        oauth_password_ref=optional_secret_ref("oauth_password_ref"),
        oauth_token_secret_ref=optional_secret_ref("oauth_token_secret_ref"),
        oauth_server_url=cast(str | None, oauth_server_url),
        oauth_redirect_uris=oauth_redirect_uris,
        oauth_token_ttl_seconds=oauth_token_ttl_seconds,
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


def _contains_path(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _registered_projects_from_host(config: HostConfig) -> tuple[RegisteredProjectConfig, ...]:
    project_settings = config.extensions.extension("projects")
    raw_registry = project_settings.get("registry")
    if raw_registry is None or raw_registry == {}:
        if config.runtime is None:
            raise ConfigError("host runtime bootstrap_workspace is required when project registry is empty")
        return (
            RegisteredProjectConfig(
                project_id="default",
                root=config.runtime.bootstrap_workspace.resolve(strict=False),
            ),
        )
    if not isinstance(raw_registry, Mapping):
        raise ConfigError("host.extensions.projects.registry must be a table")

    projects: list[RegisteredProjectConfig] = []
    roots: dict[Path, str] = {}
    for raw_project_id, raw_settings in raw_registry.items():
        project_id = str(raw_project_id)
        if PROJECT_ID_RE.fullmatch(project_id) is None:
            raise ConfigError(f"invalid project_id: {project_id}")
        if not isinstance(raw_settings, Mapping):
            raise ConfigError(f"host.extensions.projects.registry.{project_id} must be a table")
        root = _absolute_path(
            raw_settings.get("root"),
            field_name=f"host.extensions.projects.registry.{project_id}.root",
        )
        allow_unavailable = raw_settings.get("allow_unavailable", False)
        if type(allow_unavailable) is not bool:
            raise ConfigError(
                f"host.extensions.projects.registry.{project_id}.allow_unavailable must be boolean"
            )
        if not root.is_dir() and not allow_unavailable:
            raise ConfigError(f"project root does not exist: {project_id}: {root}")
        previous = roots.get(root)
        if previous is not None:
            raise ConfigError(
                f"projects {previous!r} and {project_id!r} resolve to the same canonical root: {root}"
            )
        roots[root] = project_id
        raw_project_config = raw_settings.get("project_config")
        project_config: str | None = None
        if raw_project_config is not None:
            if not isinstance(raw_project_config, str) or not raw_project_config.strip():
                raise ConfigError(
                    f"host.extensions.projects.registry.{project_id}.project_config must be a non-empty path"
                )
            project_config = raw_project_config
        projects.append(
            RegisteredProjectConfig(
                project_id=project_id,
                root=root,
                allow_unavailable=allow_unavailable,
                project_config=project_config,
            )
        )
    return tuple(projects)


def resolve_project_config_path(
    project: RegisteredProjectConfig,
    *,
    registered_roots: Sequence[Path],
) -> Path | None:
    selected = project.project_config or DEFAULT_PROJECT_CONFIG
    relative = Path(selected)
    if relative.is_absolute():
        raise ConfigError(f"project {project.project_id} project_config must be relative")

    root = project.root.resolve(strict=False)
    resolved = (root / relative).resolve(strict=False)
    if not _contains_path(root, resolved):
        raise ConfigError(f"project {project.project_id} project_config escapes registered project root")

    for registered_root in registered_roots:
        nested = registered_root.resolve(strict=False)
        if nested == root or not _contains_path(root, nested):
            continue
        if _contains_path(nested, resolved):
            raise ConfigError(f"project {project.project_id} project_config crosses registered project boundary")

    if resolved.is_file():
        return resolved
    if resolved.exists():
        raise ConfigError(f"project config is not a file: {project.project_id}: {resolved}")
    if project.project_config is not None:
        raise ConfigError(f"required project config does not exist: {project.project_id}: {resolved}")
    return None


def _project_config_schema() -> ConfigNode:
    return table(
        {
            "project_config_version": scalar(int),
            "capabilities": table({"disabled": list_of(scalar(str))}),
        }
    )


def _load_project_config(path: Path) -> ProjectConfig:
    data = read_toml(path)
    if data.get("project_config_version") != PROJECT_CONFIG_VERSION:
        raise ConfigError(f"{path}: project_config_version must be {PROJECT_CONFIG_VERSION}")
    validate_node(data, _project_config_schema(), "project")
    raw_capabilities = data.get("capabilities")
    capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}
    raw_disabled = capabilities.get("disabled", [])
    disabled: list[str] = []
    seen: set[str] = set()
    for raw_name in cast(Sequence[str], raw_disabled):
        name = str(raw_name)
        if name in seen:
            raise ConfigError(f"duplicate disabled project capability: {name}")
        seen.add(name)
        disabled.append(name)
    return ProjectConfig(
        project_config_version=PROJECT_CONFIG_VERSION,
        capabilities=ProjectCapabilities(disabled=tuple(disabled)),
        source=path,
    )


def _effective_project_config(
    project: RegisteredProjectConfig,
    *,
    project_config: ProjectConfig | None,
    host_capabilities: frozenset[str],
) -> EffectiveProjectConfig:
    disabled = () if project_config is None else project_config.capabilities.disabled
    for capability in disabled:
        if capability not in PROJECT_REDUCIBLE_CAPABILITIES or capability not in host_capabilities:
            raise ConfigError(f"project capability is not authorized by host: {capability}")
    return EffectiveProjectConfig(
        project_id=project.project_id,
        root=project.root,
        allow_unavailable=project.allow_unavailable,
        source=None if project_config is None else project_config.source,
        enabled_capabilities=frozenset(host_capabilities.difference(disabled)),
        disabled_capabilities=tuple(disabled),
    )


def _secret_ref_identity(ref: SecretRef) -> str:
    digest = hashlib.sha256(ref.target.encode("utf-8")).hexdigest()[:24]
    return f"{ref.scheme}:sha256:{digest}"


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SecretRef):
        return _secret_ref_identity(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (frozenset, set)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _host_fingerprint_payload(config: HostConfig) -> dict[str, object]:
    runtime = config.runtime
    return {
        "config_version": config.config_version,
        "runtime": None
        if runtime is None
        else {
            "bootstrap_workspace": str(runtime.bootstrap_workspace),
            "runtime_root": _jsonable(runtime.runtime_root),
            "state_root": _jsonable(runtime.state_root),
            "cache_root": _jsonable(runtime.cache_root),
            "enable_view_image": runtime.enable_view_image,
        },
        "transport": {
            "kind": config.transport.kind,
            "host": config.transport.host,
            "port": config.transport.port,
        },
        "security": {
            "permission_mode": config.security.permission_mode,
            "shell_env_inherit": config.security.shell_env_inherit,
            "allow_network": config.security.allow_network,
            "auth_mode": config.security.auth_mode,
            "auth_token_ref": _jsonable(config.security.auth_token_ref),
            "oauth_client_id": config.security.oauth_client_id,
            "oauth_client_secret_ref": _jsonable(config.security.oauth_client_secret_ref),
            "oauth_password_ref": _jsonable(config.security.oauth_password_ref),
            "oauth_token_secret_ref": _jsonable(config.security.oauth_token_secret_ref),
            "oauth_server_url": config.security.oauth_server_url,
            "oauth_redirect_uris": list(config.security.oauth_redirect_uris),
            "oauth_token_ttl_seconds": config.security.oauth_token_ttl_seconds,
        },
        "extensions": {
            "enabled": list(config.extensions.enabled_extensions),
            "settings": _jsonable(config.extensions.extension_settings),
        },
        "deployment": {
            "mcp_repository": _jsonable(config.deployment.mcp_repository),
            "sync": config.deployment.sync,
            "sync_extras": list(config.deployment.sync_extras),
            "startup_timeout_seconds": config.deployment.startup_timeout_seconds,
            "shutdown_timeout_seconds": config.deployment.shutdown_timeout_seconds,
            "poll_interval_seconds": config.deployment.poll_interval_seconds,
            "logs_root": _jsonable(config.deployment.logs_root),
            "tunnel": {
                "mode": config.deployment.tunnel.mode,
                "client": config.deployment.tunnel.client,
                "profile": config.deployment.tunnel.profile,
                "profile_dir": _jsonable(config.deployment.tunnel.profile_dir),
                "profile_file": _jsonable(config.deployment.tunnel.profile_file),
                "health_listen_addr": config.deployment.tunnel.health_listen_addr,
                "tunnel_id": config.deployment.tunnel.tunnel_id,
                "api_key_ref": _jsonable(config.deployment.tunnel.api_key_ref),
                "control_plane_base_url": config.deployment.tunnel.control_plane_base_url,
                "control_plane_url_path": config.deployment.tunnel.control_plane_url_path,
                "mcp_server_url": config.deployment.tunnel.mcp_server_url,
                "generated_profile_name": config.deployment.tunnel.generated_profile_name,
                "write_profile": _jsonable(config.deployment.tunnel.write_profile),
                "force_profile_write": config.deployment.tunnel.force_profile_write,
                "open_web_ui": config.deployment.tunnel.open_web_ui,
            },
        },
    }


def build_host_snapshot(config: HostConfig) -> ConfigSnapshot:
    registered_projects = _registered_projects_from_host(config)
    registered_roots = tuple(project.root for project in registered_projects)
    host_capabilities = frozenset(
        capability
        for capability in PROJECT_REDUCIBLE_CAPABILITIES
        if capability in config.extensions.enabled_extensions
    )
    effective_projects: dict[str, EffectiveProjectConfig] = {}
    warnings: list[str] = []
    for project in registered_projects:
        if not project.root.is_dir() and project.allow_unavailable:
            if project.project_config is not None and Path(project.project_config).is_absolute():
                raise ConfigError(f"project {project.project_id} project_config must be relative")
            warnings.append(f"project {project.project_id} root is unavailable until restart")
            effective_projects[project.project_id] = _effective_project_config(
                project,
                project_config=None,
                host_capabilities=host_capabilities,
            )
            continue
        config_path = resolve_project_config_path(project, registered_roots=registered_roots)
        project_config = None if config_path is None else _load_project_config(config_path)
        effective_projects[project.project_id] = _effective_project_config(
            project,
            project_config=project_config,
            host_capabilities=host_capabilities,
        )

    secret_references: dict[str, str] = {}
    for name, ref in (
        ("security.auth_token_ref", config.security.auth_token_ref),
        ("security.oauth_client_secret_ref", config.security.oauth_client_secret_ref),
        ("security.oauth_password_ref", config.security.oauth_password_ref),
        ("security.oauth_token_secret_ref", config.security.oauth_token_secret_ref),
        ("deployment.tunnel.api_key_ref", config.deployment.tunnel.api_key_ref),
    ):
        if ref is not None:
            secret_references[name] = _secret_ref_identity(ref)

    config_versions = MappingProxyType(
        {
            "host": HOST_CONFIG_VERSION,
            "project": PROJECT_CONFIG_VERSION,
        }
    )
    projects = MappingProxyType(dict(effective_projects))
    fingerprint_payload = {
        "resolution_mode": "host",
        "host": _host_fingerprint_payload(config),
        "registered_projects": [
            {
                "project_id": project.project_id,
                "root": str(project.root),
                "allow_unavailable": project.allow_unavailable,
                "project_config": project.project_config,
            }
            for project in sorted(registered_projects, key=lambda item: item.project_id)
        ],
        "projects": {
            project_id: {
                "root": str(effective.root),
                "allow_unavailable": effective.allow_unavailable,
                "enabled_capabilities": sorted(effective.enabled_capabilities),
                "disabled_capabilities": list(effective.disabled_capabilities),
                "project_config": None
                if effective.source is None
                else _jsonable(read_toml(effective.source)),
            }
            for project_id, effective in projects.items()
        },
        "config_versions": dict(config_versions),
        "secret_references": secret_references,
    }
    encoded = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return ConfigSnapshot(
        resolution_mode="host",
        sources=(ConfigSource(role="host", path=config.source),),
        host_config=config,
        runtime_config=config.extensions,
        registered_projects=registered_projects,
        projects=projects,
        config_versions=config_versions,
        warnings=tuple(warnings),
        secret_references=MappingProxyType(dict(secret_references)),
        fingerprint=fingerprint,
    )


def _registered_projects_from_runtime_config(
    runtime_config: RuntimeConfig,
    *,
    bootstrap_workspace: Path,
) -> tuple[RegisteredProjectConfig, ...]:
    raw_registry = runtime_config.extension("projects").get("registry")
    if raw_registry is None or raw_registry == {}:
        return (
            RegisteredProjectConfig(
                project_id="default",
                root=bootstrap_workspace.expanduser().resolve(strict=False),
            ),
        )
    if not isinstance(raw_registry, Mapping):
        raise ConfigError("extensions.projects.registry must be a table")

    records: list[RegisteredProjectConfig] = []
    roots: dict[Path, str] = {}
    for raw_project_id, raw_settings in raw_registry.items():
        project_id = str(raw_project_id)
        if PROJECT_ID_RE.fullmatch(project_id) is None:
            raise ConfigError(f"invalid project_id: {project_id}")
        if not isinstance(raw_settings, Mapping):
            raise ConfigError(f"extensions.projects.registry.{project_id} must be a table")
        raw_root = raw_settings.get("root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ConfigError(f"extensions.projects.registry.{project_id}.root is required")
        root = Path(raw_root).expanduser().resolve(strict=False)
        allow_unavailable = raw_settings.get("allow_unavailable", False)
        if type(allow_unavailable) is not bool:
            raise ConfigError(
                f"extensions.projects.registry.{project_id}.allow_unavailable must be boolean"
            )
        previous = roots.get(root)
        if previous is not None:
            raise ConfigError(
                f"projects {previous!r} and {project_id!r} resolve to the same canonical root: {root}"
            )
        roots[root] = project_id
        raw_project_config = raw_settings.get("project_config")
        project_config: str | None = None
        if raw_project_config is not None:
            if not isinstance(raw_project_config, str) or not raw_project_config.strip():
                raise ConfigError(
                    f"extensions.projects.registry.{project_id}.project_config must be a non-empty path"
                )
            project_config = raw_project_config
        records.append(
            RegisteredProjectConfig(
                project_id=project_id,
                root=root,
                allow_unavailable=allow_unavailable,
                project_config=project_config,
            )
        )
    return tuple(records)


def build_developer_snapshot(
    *,
    runtime_config: RuntimeConfig,
    bootstrap_workspace: Path,
) -> ConfigSnapshot:
    registered_projects = _registered_projects_from_runtime_config(
        runtime_config,
        bootstrap_workspace=bootstrap_workspace,
    )
    registered_roots = tuple(project.root for project in registered_projects)
    host_capabilities = frozenset(
        capability
        for capability in PROJECT_REDUCIBLE_CAPABILITIES
        if capability in runtime_config.enabled_extensions
    )
    effective_projects: dict[str, EffectiveProjectConfig] = {}
    warnings: list[str] = []
    for project in registered_projects:
        if not project.root.is_dir() and project.allow_unavailable:
            if project.project_config is not None and Path(project.project_config).is_absolute():
                raise ConfigError(f"project {project.project_id} project_config must be relative")
            warnings.append(f"project {project.project_id} root is unavailable until restart")
            effective_projects[project.project_id] = _effective_project_config(
                project,
                project_config=None,
                host_capabilities=host_capabilities,
            )
            continue
        config_path = resolve_project_config_path(project, registered_roots=registered_roots)
        project_config = None if config_path is None else _load_project_config(config_path)
        effective_projects[project.project_id] = _effective_project_config(
            project,
            project_config=project_config,
            host_capabilities=host_capabilities,
        )

    projects = MappingProxyType(dict(effective_projects))
    config_versions = MappingProxyType(
        {
            "developer": runtime_config.config_version,
            "project": PROJECT_CONFIG_VERSION,
        }
    )
    fingerprint_payload = {
        "resolution_mode": "developer",
        "bootstrap_workspace": str(bootstrap_workspace.expanduser().resolve(strict=False)),
        "runtime_config": {
            "config_version": runtime_config.config_version,
            "enabled_extensions": list(runtime_config.enabled_extensions),
            "extension_settings": _jsonable(runtime_config.extension_settings),
        },
        "registered_projects": [
            {
                "project_id": project.project_id,
                "root": str(project.root),
                "allow_unavailable": project.allow_unavailable,
                "project_config": project.project_config,
            }
            for project in sorted(registered_projects, key=lambda item: item.project_id)
        ],
        "projects": {
            project_id: {
                "root": str(effective.root),
                "allow_unavailable": effective.allow_unavailable,
                "enabled_capabilities": sorted(effective.enabled_capabilities),
                "disabled_capabilities": list(effective.disabled_capabilities),
                "project_config": None
                if effective.source is None
                else _jsonable(read_toml(effective.source)),
            }
            for project_id, effective in projects.items()
        },
        "config_versions": dict(config_versions),
    }
    encoded = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    sources = tuple(
        ConfigSource(role=f"developer-{index}", path=path)
        for index, path in enumerate(runtime_config.sources, start=1)
    )
    return ConfigSnapshot(
        resolution_mode="developer",
        sources=sources,
        host_config=None,
        runtime_config=runtime_config,
        registered_projects=registered_projects,
        projects=projects,
        config_versions=config_versions,
        warnings=tuple(warnings),
        secret_references=MappingProxyType({}),
        fingerprint=hashlib.sha256(encoded).hexdigest(),
    )
