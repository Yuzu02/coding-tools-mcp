from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Mapping, cast

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.host_config import ConfigSnapshot
from coding_tools_mcp.operation_context import observe_backend

from ..api import ExtensionContext, ExtensionManifest
from ..config import ConfigError, scalar, table
from ..contributions import ToolAnnotations, ToolContribution
from ..projects.registry import PROJECT_REGISTRY, ProjectRegistry, ProjectRegistryError, RegisteredProject
from ..projects.runtime import PROJECT_RUNTIMES, ProjectRuntimeManager
from ..services import CORE_CONFIG_SNAPSHOT
from .backend import (
    SEMANTIC_BACKEND,
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_BACKEND_UNAVAILABLE,
    SEMANTIC_PROJECT_START_FAILED,
    SEMANTIC_TIMEOUT,
    SemanticBackend,
    SemanticBackendError,
)
from .model import (
    FindDefinitionRequest,
    FindImplementationsRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetDiagnosticsRequest,
    ListSymbolsRequest,
)


@dataclass(frozen=True)
class SemanticConfig:
    backend: str = "serena"
    max_semantic_projects: int = 4
    semantic_idle_timeout_seconds: int = 900
    semantic_start_timeout_seconds: int = 60
    semantic_request_timeout_seconds: int = 60
    allow_dependency_install: bool = False


SEMANTIC_CONFIG_SCHEMA = table(
    {
        "backend": scalar(str),
        "max_semantic_projects": scalar(int),
        "semantic_idle_timeout_seconds": scalar(int),
        "semantic_start_timeout_seconds": scalar(int),
        "semantic_request_timeout_seconds": scalar(int),
        "allow_dependency_install": scalar(bool),
    }
)


PROJECT_ID_SCHEMA = {"type": "string", "minLength": 1}
PATH_SCHEMA = {"type": "string", "minLength": 1}

LIST_SYMBOLS_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": PROJECT_ID_SCHEMA,
        "path": PATH_SCHEMA,
        "depth": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
    },
    "required": ["project_id", "path"],
    "additionalProperties": False,
}

FIND_SYMBOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": PROJECT_ID_SCHEMA,
        "query": {"type": "string", "minLength": 1},
        "path": {"type": "string", "default": ""},
        "include_body": {"type": "boolean", "default": False},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
    "required": ["project_id", "query"],
    "additionalProperties": False,
}

POSITION_SCHEMA_PROPERTIES = {
    "project_id": PROJECT_ID_SCHEMA,
    "path": PATH_SCHEMA,
    "line": {"type": "integer", "minimum": 1},
    "column": {"type": "integer", "minimum": 1},
}

FIND_DEFINITION_SCHEMA = {
    "type": "object",
    "properties": dict(POSITION_SCHEMA_PROPERTIES),
    "required": ["project_id", "path", "line", "column"],
    "additionalProperties": False,
}

FIND_REFERENCES_SCHEMA = {
    "type": "object",
    "properties": {
        **POSITION_SCHEMA_PROPERTIES,
        "include_declaration": {"type": "boolean", "default": False},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
    },
    "required": ["project_id", "path", "line", "column"],
    "additionalProperties": False,
}

FIND_IMPLEMENTATIONS_SCHEMA = {
    "type": "object",
    "properties": {
        **POSITION_SCHEMA_PROPERTIES,
        "max_results": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
    },
    "required": ["project_id", "path", "line", "column"],
    "additionalProperties": False,
}

GET_DIAGNOSTICS_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": PROJECT_ID_SCHEMA,
        "path": PATH_SCHEMA,
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "min_severity": {
            "type": "string",
            "enum": ["error", "warning", "information", "hint"],
            "default": "hint",
        },
        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
    },
    "required": ["project_id", "path"],
    "additionalProperties": False,
}


SemanticBackendFactory = Callable[
    [SemanticConfig, ProjectRegistry, ProjectRuntimeManager],
    SemanticBackend,
]


_SAFE_BACKEND_DETAIL_KEYS = frozenset({"language", "operation", "diagnostic"})
_RETRYABLE_BACKEND_CODES = frozenset(
    {
        SEMANTIC_BACKEND_UNAVAILABLE,
        SEMANTIC_PROJECT_START_FAILED,
        SEMANTIC_TIMEOUT,
        SEMANTIC_BACKEND_ERROR,
    }
)


def _default_backend_factory(
    config: SemanticConfig,
    registry: ProjectRegistry,
    runtimes: ProjectRuntimeManager,
) -> SemanticBackend:
    backend_factory = cast(
        SemanticBackendFactory,
        getattr(
            import_module("coding_tools_mcp.extensions.semantic.serena"),
            "SerenaSemanticBackend",
        ),
    )
    return backend_factory(config, registry, runtimes)


def _project_failure(exc: ProjectRegistryError) -> ToolFailure:
    return ToolFailure(
        exc.code,
        exc.message,
        category="not_found"
        if exc.code in {"PROJECT_NOT_FOUND", "PROJECT_UNAVAILABLE"}
        else "validation",
    )


def _bounded_backend_details(details: Mapping[str, object]) -> dict[str, object]:
    bounded: dict[str, object] = {}
    for key in _SAFE_BACKEND_DETAIL_KEYS:
        value = details.get(key)
        if isinstance(value, str):
            bounded[key] = value[:512]
        elif type(value) in {bool, int}:
            bounded[key] = value
    return bounded


def _format_symbol(item: Mapping[str, object]) -> str:
    kind = str(item.get("kind") or "symbol")
    name = str(item.get("name_path") or item.get("name") or "?")
    path = str(item.get("path") or "?")
    raw_range = item.get("range")
    if isinstance(raw_range, Mapping):
        raw_start = raw_range.get("start")
        if isinstance(raw_start, Mapping) and isinstance(raw_start.get("line"), int):
            return f"{kind} {name} — {path}:{raw_start['line']}"
    return f"{kind} {name} — {path}"


def _render_symbol_collection(payload: dict[str, Any], key: str) -> str:
    raw_items = payload.get(key)
    if not isinstance(raw_items, list) or not raw_items:
        return f"No {key.replace('_', ' ')} found."
    lines = [_format_symbol(item) for item in raw_items[:100] if isinstance(item, Mapping)]
    if len(raw_items) > 100:
        lines.append(f"… {len(raw_items) - 100} more results omitted from model text.")
    return "\n".join(lines)


def _render_references(payload: dict[str, Any]) -> str:
    raw_items = payload.get("references")
    if not isinstance(raw_items, list) or not raw_items:
        return "No references found."
    lines: list[str] = []
    for item in raw_items[:100]:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "?")
        raw_range = item.get("range")
        suffix = ""
        if isinstance(raw_range, Mapping):
            raw_start = raw_range.get("start")
            if isinstance(raw_start, Mapping):
                line = raw_start.get("line")
                column = raw_start.get("column")
                if isinstance(line, int) and isinstance(column, int):
                    suffix = f":{line}:{column}"
        raw_container = item.get("containing_symbol")
        container = ""
        if isinstance(raw_container, Mapping):
            name = raw_container.get("name_path") or raw_container.get("name")
            if isinstance(name, str) and name:
                container = f" — in {name}"
        lines.append(f"{path}{suffix}{container}")
    if len(raw_items) > 100:
        lines.append(f"… {len(raw_items) - 100} more references omitted from model text.")
    return "\n".join(lines)


def _render_diagnostics(payload: dict[str, Any]) -> str:
    raw_items = payload.get("diagnostics")
    if not isinstance(raw_items, list) or not raw_items:
        return "No diagnostics found."
    lines: list[str] = []
    for item in raw_items[:100]:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "?")
        severity = str(item.get("severity") or "unknown")
        message = str(item.get("message") or "")
        raw_range = item.get("range")
        suffix = ""
        if isinstance(raw_range, Mapping):
            raw_start = raw_range.get("start")
            if isinstance(raw_start, Mapping) and isinstance(raw_start.get("line"), int):
                suffix = f":{raw_start['line']}"
        lines.append(f"{severity} {path}{suffix} — {message}")
    if len(raw_items) > 100:
        lines.append(f"… {len(raw_items) - 100} more diagnostics omitted from model text.")
    return "\n".join(lines)


class SemanticExtension:
    manifest = ExtensionManifest(
        name="semantic",
        requires=("projects",),
        description="Backend-neutral read-only semantic code navigation.",
        config_schema=SEMANTIC_CONFIG_SCHEMA,
    )

    def __init__(self, backend_factory: SemanticBackendFactory | None = None) -> None:
        self._backend_factory = backend_factory or _default_backend_factory
        self._config = SemanticConfig()
        self._registry: ProjectRegistry | None = None
        self._runtimes: ProjectRuntimeManager | None = None
        self._backend: SemanticBackend | None = None
        self._snapshot: ConfigSnapshot | None = None

    def configure(self, config: Mapping[str, object]) -> None:
        backend = config.get("backend", "serena")
        if backend != "serena":
            raise ConfigError("extensions.semantic.backend must be 'serena'")

        def integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
            value = config.get(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigError(
                    f"extensions.semantic.{name} must be an integer between {minimum} and {maximum}"
                )
            return value

        allow_dependency_install = config.get("allow_dependency_install", False)
        if type(allow_dependency_install) is not bool:
            raise ConfigError("extensions.semantic.allow_dependency_install must be boolean")

        self._config = SemanticConfig(
            backend="serena",
            max_semantic_projects=integer_setting("max_semantic_projects", 4, 1, 32),
            semantic_idle_timeout_seconds=integer_setting(
                "semantic_idle_timeout_seconds", 900, 1, 86_400
            ),
            semantic_start_timeout_seconds=integer_setting(
                "semantic_start_timeout_seconds", 60, 1, 600
            ),
            semantic_request_timeout_seconds=integer_setting(
                "semantic_request_timeout_seconds", 60, 1, 600
            ),
            allow_dependency_install=allow_dependency_install,
        )

    def prepare(self) -> None:
        return None

    def register(self, context: ExtensionContext) -> None:
        self._registry = context.services.require(PROJECT_REGISTRY)
        self._runtimes = context.services.require(PROJECT_RUNTIMES)
        self._snapshot = context.services.require(CORE_CONFIG_SNAPSHOT)
        self._backend = self._backend_factory(self._config, self._registry, self._runtimes)
        context.services.provide(SEMANTIC_BACKEND, self._backend)
        context.add_metadata("backend", self._backend.backend_name)
        context.add_metadata("backend_version", self._backend.backend_version)
        context.add_metadata("available", self._backend.available)
        if self._backend.availability_reason is not None:
            context.add_metadata("reason", self._backend.availability_reason[:512])
        if not self._backend.available:
            return

        annotations = ToolAnnotations(read_only=True, idempotent=True)
        context.add_tool(
            ToolContribution(
                name="list_symbols",
                title="List symbols",
                description="List semantic symbols in one project file.",
                input_schema=LIST_SYMBOLS_SCHEMA,
                handler=self._list_symbols,
                annotations=annotations,
                text_renderer=lambda payload: _render_symbol_collection(payload, "symbols"),
            )
        )
        context.add_tool(
            ToolContribution(
                name="find_symbol",
                title="Find symbol",
                description="Find semantic symbols by name path inside one project.",
                input_schema=FIND_SYMBOL_SCHEMA,
                handler=self._find_symbol,
                annotations=annotations,
                text_renderer=lambda payload: _render_symbol_collection(payload, "symbols"),
            )
        )
        context.add_tool(
            ToolContribution(
                name="find_definition",
                title="Find definition",
                description="Find the semantic definition at a one-based source position.",
                input_schema=FIND_DEFINITION_SCHEMA,
                handler=self._find_definition,
                annotations=annotations,
                text_renderer=lambda payload: _render_symbol_collection(payload, "definitions"),
            )
        )
        context.add_tool(
            ToolContribution(
                name="find_references",
                title="Find references",
                description="Find semantic references for the symbol at a one-based source position.",
                input_schema=FIND_REFERENCES_SCHEMA,
                handler=self._find_references,
                annotations=annotations,
                text_renderer=_render_references,
            )
        )
        context.add_tool(
            ToolContribution(
                name="find_implementations",
                title="Find implementations",
                description="Find implementations for the semantic symbol at a one-based source position.",
                input_schema=FIND_IMPLEMENTATIONS_SCHEMA,
                handler=self._find_implementations,
                annotations=annotations,
                text_renderer=lambda payload: _render_symbol_collection(payload, "implementations"),
            )
        )
        context.add_tool(
            ToolContribution(
                name="get_diagnostics",
                title="Get diagnostics",
                description="Return bounded semantic diagnostics for one project file.",
                input_schema=GET_DIAGNOSTICS_SCHEMA,
                handler=self._get_diagnostics,
                annotations=annotations,
                text_renderer=_render_diagnostics,
            )
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        if self._backend is None:
            return
        warnings = self._backend.close()
        if warnings:
            raise RuntimeError("; ".join(warnings[:4]))

    def _services(self) -> tuple[ProjectRegistry, ProjectRuntimeManager, SemanticBackend]:
        if self._registry is None or self._runtimes is None or self._backend is None:
            raise RuntimeError("semantic extension is not registered")
        observe_backend(self._backend.backend_name)
        return self._registry, self._runtimes, self._backend

    def _project(self, project_id: str) -> RegisteredProject:
        registry, _runtimes, _backend = self._services()
        try:
            project = registry.require_available(project_id)
        except ProjectRegistryError as exc:
            raise _project_failure(exc) from exc
        self._require_project_capability(project_id)
        return project

    def _require_project_capability(self, project_id: str) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            raise RuntimeError("semantic extension config snapshot is unavailable")
        effective = snapshot.projects.get(project_id)
        if effective is None:
            raise RuntimeError(f"project is missing from config snapshot: {project_id}")
        if "semantic" not in effective.enabled_capabilities:
            raise ToolFailure(
                "PROJECT_CAPABILITY_DISABLED",
                "Semantic navigation is disabled by project policy.",
                category="permission",
                retryable=False,
                details={"project_id": project_id, "capability": "semantic"},
            )

    def _canonical_path(self, project_id: str, raw_path: str, *, require_file: bool) -> str:
        _registry, runtimes, _backend = self._services()
        resolved = runtimes.resolve_existing(project_id, raw_path)
        if require_file and not resolved.path.is_file():
            raise ToolFailure("INVALID_ARGUMENT", "path must be a file.", category="validation")
        return resolved.display

    def _backend_failure(self, project_id: str, exc: SemanticBackendError) -> ToolFailure:
        _registry, _runtimes, backend = self._services()
        details: dict[str, object] = {
            "project_id": project_id,
            "backend": backend.backend_name,
            **_bounded_backend_details(exc.details),
        }
        if exc.code in _RETRYABLE_BACKEND_CODES:
            details.setdefault(
                "retry_hint",
                "Retry the semantic request once; if it remains unavailable, use search_text explicitly as a lexical fallback.",
            )
        return ToolFailure(
            exc.code,
            exc.message,
            category="semantic",
            retryable=exc.retryable,
            details=details,
        )

    def _list_symbols(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        path = self._canonical_path(project_id, str(args.get("path", "")), require_file=True)
        request = ListSymbolsRequest(
            path=path,
            depth=int(args.get("depth", 1)),
            max_results=int(args.get("max_results", 500)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.list_symbols(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}

    def _find_symbol(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        raw_path = str(args.get("path", ""))
        path = self._canonical_path(project_id, raw_path, require_file=False) if raw_path else ""
        request = FindSymbolRequest(
            query=str(args.get("query", "")),
            path=path,
            include_body=bool(args.get("include_body", False)),
            max_results=int(args.get("max_results", 50)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.find_symbol(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}

    def _find_definition(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        path = self._canonical_path(project_id, str(args.get("path", "")), require_file=True)
        request = FindDefinitionRequest(
            path=path,
            line=int(args.get("line", 0)),
            column=int(args.get("column", 0)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.find_definition(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}

    def _find_references(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        path = self._canonical_path(project_id, str(args.get("path", "")), require_file=True)
        request = FindReferencesRequest(
            path=path,
            line=int(args.get("line", 0)),
            column=int(args.get("column", 0)),
            include_declaration=bool(args.get("include_declaration", False)),
            max_results=int(args.get("max_results", 500)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.find_references(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}

    def _find_implementations(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        path = self._canonical_path(project_id, str(args.get("path", "")), require_file=True)
        request = FindImplementationsRequest(
            path=path,
            line=int(args.get("line", 0)),
            column=int(args.get("column", 0)),
            max_results=int(args.get("max_results", 200)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.find_implementations(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}

    def _get_diagnostics(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = str(args.get("project_id", ""))
        project = self._project(project_id)
        path = self._canonical_path(project_id, str(args.get("path", "")), require_file=True)
        request = GetDiagnosticsRequest(
            path=path,
            start_line=(int(args["start_line"]) if "start_line" in args else None),
            end_line=(int(args["end_line"]) if "end_line" in args else None),
            min_severity=str(args.get("min_severity", "hint")),
            max_results=int(args.get("max_results", 500)),
        )
        _registry, _runtimes, backend = self._services()
        try:
            result = backend.get_diagnostics(project, request)
        except SemanticBackendError as exc:
            raise self._backend_failure(project_id, exc) from exc
        return {"project_id": project_id, "backend": backend.backend_name, **result.payload()}
