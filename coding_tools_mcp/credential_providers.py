from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

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
    command_owners: Mapping[str, str] = dataclass_field(default_factory=lambda: MappingProxyType({}))


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
    unknown = set(data) - {
        "name", "commands", "read_roots", "write_roots", "env_passthrough", "env_paths"
    }
    if unknown:
        raise ConfigError(f"unknown credential provider keys: {', '.join(sorted(unknown))}")
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
        self._generation: str | None = None
        self._snapshot: CredentialRegistrySnapshot | None = None

    def _directory_generation(self) -> tuple[tuple[str, int, int, int, int], ...]:
        entries: list[tuple[str, int, int, int, int]] = []
        for fragment in sorted(self.registry_dir.glob("*.toml"), key=lambda item: item.name):
            metadata = fragment.stat()
            if stat.S_ISREG(metadata.st_mode):
                entries.append(
                    (fragment.name, metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
                )
        return tuple(entries)

    @staticmethod
    def _generation_fingerprint(generation: tuple[tuple[str, int, int, int, int], ...]) -> str:
        return hashlib.sha256(repr(generation).encode("utf-8")).hexdigest()

    def _load_generation(
        self, generation: tuple[tuple[str, int, int, int, int], ...], generation_id: str
    ) -> CredentialRegistrySnapshot:
        try:
            providers: list[CredentialProvider] = []
            names: set[str] = set()
            commands: dict[str, str] = {}
            digest = hashlib.sha256()
            for filename, _, _, _, _ in generation:
                fragment = self.registry_dir / filename
                digest.update(fragment.read_bytes())
                provider = _parse_fragment(fragment, self.broker_dir)
                if provider.name in names:
                    raise ConfigError("duplicate credential provider name")
                names.add(provider.name)
                for command in provider.commands:
                    if command in commands:
                        raise ConfigError("duplicate credential command ownership")
                    commands[command] = provider.name
                providers.append(provider)
            return CredentialRegistrySnapshot(
                tuple(providers),
                "healthy",
                generation=generation_id,
                fingerprint=digest.hexdigest(),
                command_owners=MappingProxyType(dict(commands)),
            )
        except (ConfigError, OSError, UnicodeError):
            return CredentialRegistrySnapshot(
                (),
                "invalid",
                "invalid credential provider registry",
                generation_id,
                digest.hexdigest() if "digest" in locals() else hashlib.sha256().hexdigest(),
                MappingProxyType({}),
            )

    def snapshot(self) -> CredentialRegistrySnapshot:
        try:
            generation = self._directory_generation()
            generation_id = self._generation_fingerprint(generation)
            if generation_id != self._generation:
                self._snapshot = self._load_generation(generation, generation_id)
                self._generation = generation_id
            assert self._snapshot is not None
            return self._snapshot
        except OSError:
            generation_id = hashlib.sha256(b"registry directory unavailable").hexdigest()
            self._generation = generation_id
            self._snapshot = CredentialRegistrySnapshot(
                (), "invalid", "invalid credential provider registry", generation_id, hashlib.sha256().hexdigest()
            )
            return self._snapshot


def atomic_write_fragment(path: Path, text: str) -> None:
    """Publish a registry fragment atomically and durably within its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
