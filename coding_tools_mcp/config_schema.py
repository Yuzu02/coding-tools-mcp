from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, cast


_MISSING = object()


class ConfigError(ValueError):
    """Raised when a strict Coding Tools configuration is invalid."""


@dataclass(frozen=True)
class ConfigNode:
    kind: Literal["scalar", "list", "table", "map"]
    value_types: tuple[type[object], ...] = ()
    children: Mapping[str, "ConfigNode"] = field(default_factory=dict)
    item: "ConfigNode | None" = None


def scalar(*value_types: type[object]) -> ConfigNode:
    if not value_types:
        raise ValueError("scalar schema requires at least one Python type")
    return ConfigNode(kind="scalar", value_types=value_types)


def list_of(item: ConfigNode) -> ConfigNode:
    return ConfigNode(kind="list", item=item)


def table(children: Mapping[str, ConfigNode]) -> ConfigNode:
    return ConfigNode(kind="table", children=MappingProxyType(dict(children)))


def map_of(item: ConfigNode) -> ConfigNode:
    return ConfigNode(kind="map", item=item)


def freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return freeze_mapping(value)
    if isinstance(value, list):
        return tuple(freeze_value(item) for item in value)
    return value


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: freeze_value(item) for key, item in value.items()})


def _clone(value: object) -> object:
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value


def validate_node(value: object, schema: ConfigNode, path: str) -> None:
    if schema.kind == "scalar":
        if type(value) not in schema.value_types:
            expected = ", ".join(item.__name__ for item in schema.value_types)
            raise ConfigError(f"{path} must be one of: {expected}")
        return
    if schema.kind == "list":
        if not isinstance(value, list) or schema.item is None:
            raise ConfigError(f"{path} must be a list")
        for index, item in enumerate(value):
            validate_node(item, schema.item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a table")
    if schema.kind == "table":
        for key, item in value.items():
            child = schema.children.get(key)
            if child is None:
                raise ConfigError(f"unknown configuration key: {path}.{key}")
            validate_node(item, child, f"{path}.{key}")
        return
    if schema.kind == "map" and schema.item is not None:
        for key, item in value.items():
            validate_node(item, schema.item, f"{path}.{key}")
        return
    raise AssertionError(f"invalid config schema node: {schema.kind}")


def merge_node(base: object, overlay: object, schema: ConfigNode, path: str) -> object:
    if schema.kind in {"scalar", "list"}:
        validate_node(overlay, schema, path)
        return _clone(overlay)

    if schema.kind == "table":
        if not isinstance(overlay, dict):
            raise ConfigError(f"{path} must be a table")
        result = {} if base is _MISSING else dict(cast(Mapping[str, object], base))
        for key, value in overlay.items():
            child = schema.children.get(key)
            if child is None:
                raise ConfigError(f"unknown configuration key: {path}.{key}")
            previous = result.get(key, _MISSING)
            result[key] = merge_node(previous, value, child, f"{path}.{key}")
        return result

    if schema.kind == "map":
        if not isinstance(overlay, dict) or schema.item is None:
            raise ConfigError(f"{path} must be a table")
        result = {} if base is _MISSING else dict(cast(Mapping[str, object], base))
        for key, value in overlay.items():
            previous = result.get(key, _MISSING)
            result[key] = merge_node(previous, value, schema.item, f"{path}.{key}")
        return result

    raise AssertionError(f"unsupported config schema kind: {schema.kind}")


def read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc
    return cast(dict[str, object], value)
