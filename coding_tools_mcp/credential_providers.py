from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config_schema import ConfigError


ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
COMMAND_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
SECRET_NAME_RE = re.compile(r"(?:token|secret|credential|api[_-]?key|password|passwd|private)", re.I)
FORBIDDEN_PATH_NAMES = frozenset(
    {"HOME", "PATH", "PATHEXT", "TMP", "TEMP", "TMPDIR", "COMSPEC", "SYSTEMROOT", "WINDIR"}
)
FORBIDDEN_PASSTHROUGH_NAMES = FORBIDDEN_PATH_NAMES | {
    "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"
}


@dataclass(frozen=True)
class CredentialProvider:
    name: str
    commands: tuple[str, ...]
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    env_passthrough: tuple[str, ...]
    env_paths: tuple[tuple[str, Path], ...]


@dataclass(frozen=True)
class CredentialRegistrySnapshot:
    providers: tuple[CredentialProvider, ...]
    health: Literal["healthy", "invalid"]
    error: str | None = None
    generation: str = ""
    fingerprint: str = ""


def _path(raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{field} must be a non-empty absolute path")
    value = Path(raw).expanduser()
    if not value.is_absolute():
        raise ConfigError(f"{field} must be an absolute path")
    return value.resolve(strict=False)


def _declared_path(raw: object, field: str) -> Path:
    if not isinstance(raw, str):
        raise ConfigError(f"{field} must be an absolute path")
    candidate = Path(raw).expanduser()
    for part in (candidate, *candidate.parents):
        if part.is_symlink():
            raise ConfigError(f"{field} cannot contain symlinked roots")
    return _path(raw, field)


def _env_path(raw: object, field: str) -> tuple[str, Path]:
    if not isinstance(raw, str):
        raise ConfigError(f"{field} must use NAME=/absolute/path")
    name, separator, value = raw.partition("=")
    if not separator or not ENV_NAME_RE.fullmatch(name):
        raise ConfigError(f"{field} must use NAME=/absolute/path")
    if SECRET_NAME_RE.search(name):
        raise ConfigError(f"{field} cannot set a secret-like environment variable")
    if name.upper() in FORBIDDEN_PASSTHROUGH_NAMES:
        raise ConfigError(f"{field} cannot override the isolated process environment")
    return name, _declared_path(value, field)


def _parse_fragment(path: Path, broker_dir: Path) -> CredentialProvider:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid credential provider fragment {path.name}") from exc
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or Path(name).name != name or name in {".", ".."}:
        raise ConfigError("credential provider name must be a safe non-empty name")
    name = name.strip()
    raw_commands = data.get("commands", [])
    if not isinstance(raw_commands, list):
        raise ConfigError(f"provider {name}.commands must be a list")
    commands = tuple(str(item).strip() for item in raw_commands)
    if not commands or any(not COMMAND_RE.fullmatch(item) for item in commands):
        raise ConfigError(f"provider {name}.commands must contain executable basenames")
    for field in ("read_roots", "write_roots"):
        raw_roots = data.get(field, [])
        if not isinstance(raw_roots, list):
            raise ConfigError(f"provider {name}.{field} must be a list")
        roots = tuple(_declared_path(item, f"provider {name}.{field}[{i}]") for i, item in enumerate(raw_roots))
        for root in roots:
            allowed = (broker_dir / name).resolve(strict=False)
            try:
                root.relative_to(allowed)
            except ValueError as exc:
                raise ConfigError(f"provider {name}.{field} must stay within its broker subtree") from exc
        if field == "read_roots":
            read_roots = roots
        else:
            write_roots = roots
    raw_passthrough = data.get("env_passthrough", [])
    if not isinstance(raw_passthrough, list):
        raise ConfigError(f"provider {name}.env_passthrough must be a list")
    env_passthrough = tuple(str(item).strip() for item in raw_passthrough)
    if any(not ENV_NAME_RE.fullmatch(item) for item in env_passthrough):
        raise ConfigError(f"provider {name}.env_passthrough must contain environment variable names")
    if any(item.upper() in FORBIDDEN_PASSTHROUGH_NAMES for item in env_passthrough):
        raise ConfigError(f"provider {name}.env_passthrough cannot override the isolated process environment")
    if len(set(env_passthrough)) != len(env_passthrough):
        raise ConfigError(f"provider {name}.env_passthrough cannot contain duplicates")
    raw_env_paths = data.get("env_paths", [])
    if not isinstance(raw_env_paths, list):
        raise ConfigError(f"provider {name}.env_paths must be a list")
    env_paths = tuple(_env_path(item, f"provider {name}.env_paths[{i}]") for i, item in enumerate(raw_env_paths))
    if len({key for key, _ in env_paths}) != len(env_paths):
        raise ConfigError(f"provider {name}.env_paths cannot set the same variable more than once")
    for _, env_root in env_paths:
        allowed = (broker_dir / name).resolve(strict=False)
        try:
            env_root.relative_to(allowed)
        except ValueError as exc:
            raise ConfigError(f"provider {name}.env_paths must stay within its broker subtree") from exc
    return CredentialProvider(name, commands, read_roots, write_roots, env_passthrough, env_paths)


class CredentialProviderRegistry:
    def __init__(self, registry_dir: Path, broker_dir: Path) -> None:
        self.registry_dir = registry_dir
        self.broker_dir = broker_dir

    def snapshot(self) -> CredentialRegistrySnapshot:
        try:
            fragments = tuple(sorted(self.registry_dir.glob("*.toml"), key=lambda item: item.name))
            providers: list[CredentialProvider] = []
            names: set[str] = set()
            commands: dict[str, str] = {}
            digest = hashlib.sha256()
            for fragment in fragments:
                digest.update(fragment.read_bytes())
                provider = _parse_fragment(fragment, self.broker_dir)
                if provider.name in names:
                    raise ConfigError(f"duplicate credential provider name: {provider.name}")
                names.add(provider.name)
                for command in provider.commands:
                    if command in commands:
                        raise ConfigError(f'credential command "{command}" is already owned by provider "{commands[command]}"')
                    commands[command] = provider.name
                providers.append(provider)
            return CredentialRegistrySnapshot(tuple(providers), "healthy", fingerprint=digest.hexdigest())
        except (ConfigError, OSError) as exc:
            return CredentialRegistrySnapshot((), "invalid", str(exc)[:256])
