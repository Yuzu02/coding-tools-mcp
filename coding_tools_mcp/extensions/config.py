from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence, cast

from coding_tools_mcp.config_schema import (
    ConfigError,
    ConfigNode,
    freeze_mapping as _freeze_mapping,
    list_of,
    map_of as map_of,
    merge_node as _merge_node,
    read_toml as _read_toml,
    scalar,
    table,
    validate_node as _validate_node,
)
from coding_tools_mcp.envutils import ENV_PREFIX


CONFIG_VERSION = 1
EXTENSION_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class RuntimeConfig:
    config_version: int
    enabled_extensions: tuple[str, ...]
    extension_settings: Mapping[str, Mapping[str, object]]
    sources: tuple[Path, ...]

    @classmethod
    def defaults(
        cls,
        *,
        enabled: Sequence[str],
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> "RuntimeConfig":
        frozen_settings = {name: _freeze_mapping(value) for name, value in (settings or {}).items()}
        return cls(
            config_version=CONFIG_VERSION,
            enabled_extensions=tuple(enabled),
            extension_settings=MappingProxyType(frozen_settings),
            sources=(),
        )

    def extension(self, name: str) -> Mapping[str, object]:
        return self.extension_settings.get(name, MappingProxyType({}))


def _resolve_selected_path(raw: Path | str, *, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def resolve_config_paths(
    *,
    cwd: Path,
    environ: Mapping[str, str],
    public_path: Path | str | bool | None = None,
    local_path: Path | str | bool | None = None,
) -> tuple[Path | None, Path | None]:
    """Resolve config paths without searching parent or home directories."""

    resolved_cwd = cwd.expanduser().resolve()

    if public_path is False:
        public = None
    elif public_path is not None:
        if isinstance(public_path, bool):
            raise ConfigError("public config path must be a path or false")
        public = _resolve_selected_path(public_path, cwd=resolved_cwd)
    elif raw_public := environ.get(f"{ENV_PREFIX}_CONFIG"):
        public = _resolve_selected_path(raw_public, cwd=resolved_cwd)
    else:
        candidate = resolved_cwd / "coding-tools.toml"
        public = candidate if candidate.is_file() else None

    local_base = public.parent if public is not None else resolved_cwd
    if local_path is False:
        local = None
    elif local_path is not None:
        if isinstance(local_path, bool):
            raise ConfigError("local config path must be a path or false")
        local = _resolve_selected_path(local_path, cwd=resolved_cwd)
    elif raw_local := environ.get(f"{ENV_PREFIX}_LOCAL_CONFIG"):
        local = _resolve_selected_path(raw_local, cwd=resolved_cwd)
    else:
        candidate = local_base / "coding-tools.local.toml"
        local = candidate if candidate.is_file() else None

    return public, local


def _runtime_schema(extension_schemas: Mapping[str, ConfigNode]) -> ConfigNode:
    return table(
        {
            "config_version": scalar(int),
            "extensions": table(
                {
                    "enabled": list_of(scalar(str)),
                    **extension_schemas,
                }
            ),
        }
    )


def _validate_known_extension_tables(
    data: Mapping[str, object],
    extension_schemas: Mapping[str, ConfigNode],
) -> None:
    raw_extensions = data.get("extensions", {})
    if not isinstance(raw_extensions, dict):
        return
    for key in raw_extensions:
        if key != "enabled" and key not in extension_schemas:
            raise ConfigError(f"unknown extension: {key}")


def _validate_enabled(
    values: Sequence[str],
    extension_schemas: Mapping[str, ConfigNode],
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for name in values:
        if EXTENSION_NAME_RE.fullmatch(name) is None:
            raise ConfigError(f"invalid extension name: {name}")
        if name in seen:
            raise ConfigError(f"duplicate enabled extension: {name}")
        if name not in extension_schemas:
            raise ConfigError(f"unknown extension: {name}")
        seen.add(name)
        result.append(name)
    return tuple(result)


def parse_extension_list(raw: str) -> tuple[str, ...]:
    if raw.strip() == "":
        return ()
    names = tuple(part.strip() for part in raw.split(","))
    if any(not name for name in names):
        raise ConfigError("extension list contains an empty name")
    seen: set[str] = set()
    for name in names:
        if EXTENSION_NAME_RE.fullmatch(name) is None:
            raise ConfigError(f"invalid extension name: {name}")
        if name in seen:
            raise ConfigError(f"duplicate enabled extension: {name}")
        seen.add(name)
    return names


def load_runtime_config(
    *,
    cwd: Path,
    extension_schemas: Mapping[str, ConfigNode],
    default_enabled: Sequence[str],
    environ: Mapping[str, str] | None = None,
    public_path: Path | str | bool | None = None,
    local_path: Path | str | bool | None = None,
    cli_extensions: Sequence[str] | None = None,
) -> RuntimeConfig:
    env = os.environ if environ is None else environ
    public, local = resolve_config_paths(
        cwd=cwd,
        environ=env,
        public_path=public_path,
        local_path=local_path,
    )
    root_schema = _runtime_schema(extension_schemas)
    merged: object = {
        "config_version": CONFIG_VERSION,
        "extensions": {"enabled": list(default_enabled)},
    }
    sources: list[Path] = []
    for path in (public, local):
        if path is None:
            continue
        parsed = _read_toml(path)
        if parsed.get("config_version") != CONFIG_VERSION:
            raise ConfigError(f"{path}: config_version must be {CONFIG_VERSION}")
        _validate_known_extension_tables(parsed, extension_schemas)
        merged = _merge_node(merged, parsed, root_schema, "config")
        sources.append(path)

    data = cast(dict[str, object], merged)
    extensions = cast(dict[str, object], data["extensions"])
    env_override = env.get(f"{ENV_PREFIX}_EXTENSIONS")
    if env_override is not None:
        extensions["enabled"] = list(parse_extension_list(env_override))
    if cli_extensions is not None:
        extensions["enabled"] = list(_validate_enabled(cli_extensions, extension_schemas))

    _validate_node(data, root_schema, "config")
    enabled = _validate_enabled(cast(Sequence[str], extensions.get("enabled", ())), extension_schemas)
    settings = {
        name: _freeze_mapping(cast(Mapping[str, object], extensions.get(name, {})))
        for name in extension_schemas
    }
    return RuntimeConfig(
        config_version=CONFIG_VERSION,
        enabled_extensions=enabled,
        extension_settings=MappingProxyType(settings),
        sources=tuple(sources),
    )
