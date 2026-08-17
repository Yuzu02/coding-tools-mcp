from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
ContentBuilder = Callable[[dict[str, Any]], list[dict[str, Any]]]
HandlerWrapper = Callable[[ToolHandler], ToolHandler]
ToolTextRenderer = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ToolAnnotations:
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


@dataclass(frozen=True)
class ToolContribution:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    annotations: ToolAnnotations = ToolAnnotations()
    error_status: str | None = None
    content_builder: ContentBuilder | None = None
    text_renderer: ToolTextRenderer | None = None


@dataclass(frozen=True)
class SchemaPatch:
    properties: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDecorator:
    targets: tuple[str, ...]
    schema_patch: SchemaPatch
    wrap_handler: HandlerWrapper


@dataclass(frozen=True)
class ServerMetadataContribution:
    key: str
    value: object


@dataclass(frozen=True)
class ComposedTool:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    annotations: ToolAnnotations
    origin: str
    error_status: str | None = None
    content_builder: ContentBuilder | None = None
    text_renderer: ToolTextRenderer | None = None
    decorators: tuple[str, ...] = ()


class ContributionError(RuntimeError):
    """Raised when extension contributions cannot be composed safely."""


class ContributionRegistry:
    def __init__(self) -> None:
        self._tools: list[tuple[str, ToolContribution]] = []
        self._tool_names: set[str] = set()
        self._decorators: list[tuple[str, ToolDecorator]] = []
        self._metadata: dict[str, dict[str, object]] = {}
        self._frozen = False

    def _require_mutable(self) -> None:
        if self._frozen:
            raise ContributionError("contribution registry is frozen")

    def add_tool(self, extension: str, tool: ToolContribution) -> None:
        self._require_mutable()
        if tool.name in self._tool_names:
            raise ContributionError(f"tool already contributed: {tool.name}")
        self._tool_names.add(tool.name)
        self._tools.append((extension, tool))

    def add_decorator(self, extension: str, decorator: ToolDecorator) -> None:
        self._require_mutable()
        self._decorators.append((extension, decorator))

    def add_metadata(self, extension: str, contribution: ServerMetadataContribution) -> None:
        self._require_mutable()
        namespace = self._metadata.setdefault(extension, {})
        if contribution.key in namespace:
            raise ContributionError(f"duplicate metadata contribution: {extension}.{contribution.key}")
        namespace[contribution.key] = contribution.value

    def freeze(self) -> None:
        self._frozen = True

    def tool_entries(self) -> tuple[tuple[str, ToolContribution], ...]:
        return tuple(self._tools)

    def decorator_entries(self) -> tuple[tuple[str, ToolDecorator], ...]:
        return tuple(self._decorators)

    def metadata_snapshot(self) -> dict[str, dict[str, object]]:
        return {extension: dict(values) for extension, values in self._metadata.items()}


def _validate_composed_tool(tool: ComposedTool) -> None:
    schema = tool.input_schema
    if schema.get("type") != "object":
        raise ContributionError(f"tool input schema must be an object: {tool.name}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ContributionError(f"tool schema properties must be an object: {tool.name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise ContributionError(f"tool schema required must be a string list: {tool.name}")
    missing = [name for name in required if name not in properties]
    if missing:
        raise ContributionError(f"tool schema requires unknown properties: {tool.name}: {missing}")


def compose_tools(
    core_tools: Mapping[str, ComposedTool],
    contributions: ContributionRegistry,
    extension_order: Sequence[str],
) -> Mapping[str, ComposedTool]:
    tools = {
        name: replace(tool, input_schema=deepcopy(dict(tool.input_schema)))
        for name, tool in core_tools.items()
    }
    for extension, contribution in contributions.tool_entries():
        if contribution.name in tools:
            raise ContributionError(f"tool collision: {contribution.name}")
        tools[contribution.name] = ComposedTool(
            name=contribution.name,
            title=contribution.title,
            description=contribution.description,
            input_schema=deepcopy(dict(contribution.input_schema)),
            handler=contribution.handler,
            annotations=contribution.annotations,
            error_status=contribution.error_status,
            content_builder=contribution.content_builder,
            text_renderer=contribution.text_renderer,
            origin=extension,
        )

    rank = {name: index for index, name in enumerate(extension_order)}
    decorator_entries = contributions.decorator_entries()
    unknown_owners = [extension for extension, _decorator in decorator_entries if extension not in rank]
    if unknown_owners:
        raise ContributionError(f"decorator owner not in extension order: {unknown_owners[0]}")
    decorators = sorted(decorator_entries, key=lambda item: rank[item[0]])
    by_target: dict[str, list[tuple[str, ToolDecorator]]] = {}
    for extension, decorator in decorators:
        for target in decorator.targets:
            if target not in tools:
                raise ContributionError(f"unknown decorator target: {target}")
            by_target.setdefault(target, []).append((extension, decorator))

    for target, entries in by_target.items():
        tool = tools[target]
        schema = deepcopy(dict(tool.input_schema))
        raw_properties = schema.setdefault("properties", {})
        if not isinstance(raw_properties, dict):
            raise ContributionError(f"tool schema properties must be an object: {tool.name}")
        properties = raw_properties
        raw_required = schema.get("required", [])
        if not isinstance(raw_required, list) or any(not isinstance(name, str) for name in raw_required):
            raise ContributionError(f"tool schema required must be a string list: {tool.name}")
        required = list(raw_required)
        for _extension, decorator in entries:
            for name, property_schema in decorator.schema_patch.properties.items():
                if name in properties:
                    raise ContributionError(f"schema property collision: {target}.{name}")
                properties[name] = deepcopy(dict(property_schema))
            for name in decorator.schema_patch.required:
                if name not in properties:
                    raise ContributionError(f"required schema property missing: {target}.{name}")
                if name not in required:
                    required.append(name)
        if required:
            schema["required"] = required

        handler = tool.handler
        for _extension, decorator in reversed(entries):
            handler = decorator.wrap_handler(handler)
        tools[target] = replace(
            tool,
            input_schema=schema,
            handler=handler,
            decorators=tool.decorators + tuple(extension for extension, _decorator in entries),
        )

    for tool in tools.values():
        _validate_composed_tool(tool)
    return MappingProxyType(tools)
