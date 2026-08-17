from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from .api import Extension
from .config import EXTENSION_NAME_RE, ConfigNode


class ExtensionRegistryError(ValueError):
    """Raised when the static extension registry or dependency graph is invalid."""


def _validate_extension_name(name: str) -> None:
    if EXTENSION_NAME_RE.fullmatch(name) is None:
        raise ExtensionRegistryError(f"invalid extension name: {name}")


def _validate_enabled_names(names: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        _validate_extension_name(name)
        if name in seen:
            raise ExtensionRegistryError(f"duplicate enabled extension: {name}")
        seen.add(name)
        result.append(name)
    return tuple(result)


class ExtensionRegistry:
    def __init__(
        self,
        extensions: Iterable[type[Extension]],
        *,
        default_enabled: Sequence[str],
    ) -> None:
        by_name: dict[str, type[Extension]] = {}
        for extension_type in extensions:
            manifest = extension_type.manifest
            _validate_extension_name(manifest.name)
            for dependency in manifest.requires:
                _validate_extension_name(dependency)
            if manifest.name in by_name:
                raise ExtensionRegistryError(f"duplicate extension name: {manifest.name}")
            by_name[manifest.name] = extension_type
        self._extensions = MappingProxyType(by_name)
        self._default_enabled = _validate_enabled_names(default_enabled)
        self.resolve_order(self._default_enabled)

    @property
    def default_enabled(self) -> tuple[str, ...]:
        return self._default_enabled

    def schemas(self) -> Mapping[str, ConfigNode]:
        return MappingProxyType(
            {name: extension_type.manifest.config_schema for name, extension_type in self._extensions.items()}
        )

    def extension_type(self, name: str) -> type[Extension]:
        try:
            return self._extensions[name]
        except KeyError as exc:
            raise ExtensionRegistryError(f"unknown extension: {name}") from exc

    def resolve_order(self, enabled: Sequence[str]) -> tuple[str, ...]:
        requested = _validate_enabled_names(enabled)
        requested_set = set(requested)
        indegree = {name: 0 for name in requested}
        dependents: dict[str, list[str]] = {name: [] for name in requested}
        for name in requested:
            manifest = self.extension_type(name).manifest
            for dependency in manifest.requires:
                if dependency not in self._extensions:
                    raise ExtensionRegistryError(f"{name} requires unknown extension {dependency}")
                if dependency not in requested_set:
                    raise ExtensionRegistryError(f"{name} requires enabled extension {dependency}")
                indegree[name] += 1
                dependents[dependency].append(name)

        position = {name: index for index, name in enumerate(requested)}
        ready = [name for name in requested if indegree[name] == 0]
        ordered: list[str] = []
        while ready:
            ready.sort(key=position.__getitem__)
            name = ready.pop(0)
            ordered.append(name)
            for dependent in dependents[name]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if len(ordered) != len(requested):
            blocked = [name for name in requested if indegree[name] > 0]
            raise ExtensionRegistryError(f"dependency cycle among: {', '.join(blocked)}")
        return tuple(ordered)
