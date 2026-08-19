from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from serena.config.serena_config import (  # type: ignore[import-not-found,import-untyped]
    LanguageBackend,
    ProjectConfig,
    RegisteredProject as SerenaRegisteredProject,
    SerenaConfig,
)
from serena.symbol import (  # type: ignore[import-not-found,import-untyped]
    LanguageServerSymbol,
    LanguageServerSymbolRetriever,
    ReferenceInLanguageServerSymbol,
)

from .backend import (
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_FILE_UNSUPPORTED,
    SEMANTIC_LANGUAGE_UNSUPPORTED,
    SEMANTIC_SYMBOL_NOT_FOUND,
)
from .protocol import WorkerProtocolError, decode_message, encode_message
from .serena import SUPPORTED_SERENA_VERSION


MAX_SYMBOL_BODY_BYTES = 32 * 1024
_EXTERNAL_PATH_PREFIXES = ("external://", "file://", "<")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass(frozen=True)
class WorkerSemanticError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, object] | None = None

    def __str__(self) -> str:
        return self.message


@dataclass
class _NodeBudget:
    maximum: int
    used: int = 0
    truncated: bool = False

    def claim(self) -> bool:
        if self.used >= self.maximum:
            self.truncated = True
            return False
        self.used += 1
        return True


@dataclass
class _WorkerRuntime:
    project_root: Path
    excluded_roots: tuple[Path, ...]
    project: Any
    retriever: LanguageServerSymbolRetriever | None
    languages: tuple[str, ...]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Private Serena semantic worker")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--excluded-root", action="append", default=[])
    return parser.parse_args(argv)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_excluded_roots(project_root: Path, roots: list[str]) -> tuple[Path, ...]:
    excluded: list[Path] = []
    for raw in roots:
        try:
            resolved = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved == project_root or not _is_relative_to(resolved, project_root):
            continue
        if resolved not in excluded:
            excluded.append(resolved)
    return tuple(sorted(excluded, key=lambda path: str(path)))


def _path_patterns(project_root: Path, path: Path) -> list[str]:
    relative = path.relative_to(project_root).as_posix()
    return [relative, f"{relative}/**"]


def _unsafe_symlink_patterns(
    project_root: Path,
    excluded_roots: tuple[Path, ...],
) -> list[str]:
    patterns: list[str] = []
    for directory, dirs, files in os.walk(project_root, followlinks=False):
        base = Path(directory)
        for name in (*dirs, *files):
            candidate = base / name
            if not candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                patterns.extend(_path_patterns(project_root, candidate))
                continue
            unsafe = not _is_relative_to(resolved, project_root) or any(
                resolved == excluded or _is_relative_to(resolved, excluded)
                for excluded in excluded_roots
            )
            if unsafe:
                patterns.extend(_path_patterns(project_root, candidate))
    return patterns


def _ignored_patterns(
    project_root: Path,
    excluded_roots: tuple[Path, ...],
) -> list[str]:
    patterns: list[str] = []
    for root in excluded_roots:
        patterns.extend(_path_patterns(project_root, root))
    patterns.extend(_unsafe_symlink_patterns(project_root, excluded_roots))
    return list(dict.fromkeys(patterns))


def _safe_relative_path(
    project_root: Path,
    excluded_roots: tuple[Path, ...],
    relative_path: object,
) -> str | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    normalized = relative_path.replace("\\", "/")
    lowered = normalized.lower()
    if lowered.startswith(_EXTERNAL_PATH_PREFIXES):
        return None
    if normalized.startswith("/") or _WINDOWS_ABSOLUTE_PATH_RE.match(normalized):
        return None
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return None
    candidate = project_root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _is_relative_to(resolved, project_root):
        return None
    if any(resolved == excluded or _is_relative_to(resolved, excluded) for excluded in excluded_roots):
        return None
    return resolved.relative_to(project_root).as_posix()


def _position(line0: int, column0: int) -> dict[str, int]:
    return {"line": line0 + 1, "column": column0 + 1}


def _kind_name(value: object) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _truncate_utf8(value: str, maximum: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value, False
    return encoded[:maximum].decode("utf-8", errors="ignore"), True


def _symbol_range(symbol: LanguageServerSymbol) -> dict[str, object] | None:
    start = symbol.get_body_start_position()
    end = symbol.get_body_end_position()
    if start is None or end is None:
        location = symbol.location
        if location.line is None or location.column is None:
            return None
        start = type("Position", (), {"line": location.line, "col": location.column})()
        end = start
    end_line = end.line
    end_column = end.col
    if (end_line, end_column) < (start.line, start.col):
        end_line, end_column = start.line, start.col
    return {
        "start": _position(start.line, start.col),
        "end": _position(end_line, end_column),
    }


def _normalize_symbol(
    symbol: LanguageServerSymbol,
    *,
    project_root: Path,
    excluded_roots: tuple[Path, ...],
    include_body: bool,
    depth: int,
    budget: _NodeBudget,
) -> dict[str, object] | None:
    path = _safe_relative_path(project_root, excluded_roots, symbol.location.relative_path)
    if path is None or not budget.claim():
        return None
    payload: dict[str, object] = {
        "name": symbol.name,
        "name_path": symbol.get_name_path(),
        "kind": _kind_name(symbol.symbol_kind_name),
        "path": path,
        "children": [],
    }
    symbol_range = _symbol_range(symbol)
    if symbol_range is not None:
        payload["range"] = symbol_range
    if include_body:
        body = symbol.body
        if body is not None:
            bounded, truncated = _truncate_utf8(body, MAX_SYMBOL_BODY_BYTES)
            payload["body"] = bounded
            payload["body_truncated"] = truncated
    if depth > 0:
        children: list[dict[str, object]] = []
        for child in symbol.iter_children():
            normalized = _normalize_symbol(
                child,
                project_root=project_root,
                excluded_roots=excluded_roots,
                include_body=False,
                depth=depth - 1,
                budget=budget,
            )
            if normalized is not None:
                children.append(normalized)
            if budget.truncated:
                break
        payload["children"] = children
    return payload


def _require_supported_file(runtime: _WorkerRuntime, path: str) -> None:
    if not runtime.languages:
        raise WorkerSemanticError(
            SEMANTIC_LANGUAGE_UNSUPPORTED,
            "No supported semantic language was detected for this project.",
        )
    if runtime.retriever is None or not runtime.retriever.can_analyze_file(path):
        raise WorkerSemanticError(
            SEMANTIC_FILE_UNSUPPORTED,
            f"Semantic backend cannot analyze file: {path}",
        )


def _list_symbols(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    path = str(params.get("path", ""))
    depth = cast(int, params.get("depth", 1))
    maximum = cast(int, params.get("max_results", 500))
    _require_supported_file(runtime, path)
    assert runtime.retriever is not None
    overview = runtime.retriever.get_symbol_overview(path)
    budget = _NodeBudget(maximum)
    symbols: list[dict[str, object]] = []
    for values in overview.values():
        for symbol in values:
            normalized = _normalize_symbol(
                symbol,
                project_root=runtime.project_root,
                excluded_roots=runtime.excluded_roots,
                include_body=False,
                depth=depth,
                budget=budget,
            )
            if normalized is not None:
                symbols.append(normalized)
            if budget.truncated:
                break
        if budget.truncated:
            break
    return {"symbols": symbols, "truncated": budget.truncated, "warnings": []}


def _find_symbol(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    if not runtime.languages or runtime.retriever is None:
        raise WorkerSemanticError(
            SEMANTIC_LANGUAGE_UNSUPPORTED,
            "No supported semantic language was detected for this project.",
        )
    query = str(params.get("query", ""))
    path = str(params.get("path", ""))
    include_body = bool(params.get("include_body", False))
    maximum = cast(int, params.get("max_results", 50))
    if path:
        _require_supported_file(runtime, path)
    found = runtime.retriever.find(
        query,
        within_relative_path=path or None,
    )
    budget = _NodeBudget(maximum)
    symbols: list[dict[str, object]] = []
    for symbol in found:
        normalized = _normalize_symbol(
            symbol,
            project_root=runtime.project_root,
            excluded_roots=runtime.excluded_roots,
            include_body=include_body,
            depth=0,
            budget=budget,
        )
        if normalized is not None:
            symbols.append(normalized)
        if budget.truncated:
            break
    truncated = len(found) > len(symbols) or budget.truncated
    return {"symbols": symbols, "truncated": truncated, "warnings": []}


def _definition_symbol(
    runtime: _WorkerRuntime,
    path: str,
    line: int,
    column: int,
) -> LanguageServerSymbol:
    if line < 1 or column < 1:
        raise WorkerSemanticError(
            SEMANTIC_BACKEND_ERROR,
            "Semantic positions must be one-based positive integers.",
        )
    _require_supported_file(runtime, path)
    assert runtime.retriever is not None
    symbol = runtime.retriever.find_declaration(
        path,
        line - 1,
        column - 1,
        include_body=False,
    )
    if symbol is None:
        raise WorkerSemanticError(
            SEMANTIC_SYMBOL_NOT_FOUND,
            "No semantic definition was found at the requested position.",
        )
    return symbol


def _find_definition(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    path = str(params.get("path", ""))
    line = cast(int, params.get("line", 0))
    column = cast(int, params.get("column", 0))
    symbol = _definition_symbol(runtime, path, line, column)
    normalized = _normalize_symbol(
        symbol,
        project_root=runtime.project_root,
        excluded_roots=runtime.excluded_roots,
        include_body=False,
        depth=0,
        budget=_NodeBudget(1),
    )
    if normalized is None:
        raise WorkerSemanticError(
            SEMANTIC_SYMBOL_NOT_FOUND,
            "Semantic definition resolves outside the selected project.",
        )
    return {"definitions": [normalized], "truncated": False, "warnings": []}


def _reference_payload(
    runtime: _WorkerRuntime,
    reference: ReferenceInLanguageServerSymbol,
) -> dict[str, object] | None:
    path = _safe_relative_path(runtime.project_root, runtime.excluded_roots, reference.get_relative_path())
    if path is None:
        return None
    containing = _normalize_symbol(
        reference.symbol,
        project_root=runtime.project_root,
        excluded_roots=runtime.excluded_roots,
        include_body=False,
        depth=0,
        budget=_NodeBudget(1),
    )
    return {
        "path": path,
        "range": {
            "start": _position(reference.line, reference.character),
            "end": _position(reference.line, reference.character + 1),
        },
        **({"containing_symbol": containing} if containing is not None else {}),
    }


def _declaration_reference(
    runtime: _WorkerRuntime,
    symbol: LanguageServerSymbol,
) -> dict[str, object] | None:
    path = _safe_relative_path(runtime.project_root, runtime.excluded_roots, symbol.location.relative_path)
    location = symbol.location
    if path is None or location.line is None or location.column is None:
        return None
    return {
        "path": path,
        "range": {
            "start": _position(location.line, location.column),
            "end": _position(location.line, location.column + 1),
        },
    }


def _find_references(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    path = str(params.get("path", ""))
    line = cast(int, params.get("line", 0))
    column = cast(int, params.get("column", 0))
    include_declaration = bool(params.get("include_declaration", False))
    maximum = cast(int, params.get("max_results", 500))
    symbol = _definition_symbol(runtime, path, line, column)
    assert runtime.retriever is not None
    raw_references = runtime.retriever.find_referencing_symbols_by_location(symbol.location)
    references: list[dict[str, object]] = []
    if include_declaration:
        declaration = _declaration_reference(runtime, symbol)
        if declaration is not None:
            references.append(declaration)
    truncated = False
    for reference in raw_references:
        payload = _reference_payload(runtime, reference)
        if payload is None or payload in references:
            continue
        if len(references) >= maximum:
            truncated = True
            break
        references.append(payload)
    if len(references) < maximum and len(raw_references) > len(references):
        truncated = truncated or any(
            _reference_payload(runtime, reference) is None for reference in raw_references
        )
    return {"references": references, "truncated": truncated, "warnings": []}


def _find_implementations(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    path = str(params.get("path", ""))
    line = cast(int, params.get("line", 0))
    column = cast(int, params.get("column", 0))
    maximum = cast(int, params.get("max_results", 200))
    symbol = _definition_symbol(runtime, path, line, column)
    assert runtime.retriever is not None
    found = runtime.retriever.find_implementing_symbols_by_location(
        symbol.location,
        include_body=False,
    )
    budget = _NodeBudget(maximum)
    implementations: list[dict[str, object]] = []
    for implementation in found:
        normalized = _normalize_symbol(
            implementation,
            project_root=runtime.project_root,
            excluded_roots=runtime.excluded_roots,
            include_body=False,
            depth=0,
            budget=budget,
        )
        if normalized is not None:
            implementations.append(normalized)
        if budget.truncated:
            break
    return {
        "implementations": implementations,
        "truncated": len(found) > len(implementations) or budget.truncated,
        "warnings": [],
    }


_DIAGNOSTIC_SEVERITY_VALUES = {
    "error": 1,
    "warning": 2,
    "information": 3,
    "hint": 4,
}
_DIAGNOSTIC_SEVERITY_NAMES = {value: name for name, value in _DIAGNOSTIC_SEVERITY_VALUES.items()}


def _diagnostic_payload(runtime: _WorkerRuntime, path: str, diagnostic: object) -> dict[str, object] | None:
    safe_path = _safe_relative_path(runtime.project_root, runtime.excluded_roots, path)
    if safe_path is None:
        return None
    def field(value: object, name: str) -> object:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    diagnostic_range = field(diagnostic, "range")
    start = field(diagnostic_range, "start") if diagnostic_range is not None else None
    end = field(diagnostic_range, "end") if diagnostic_range is not None else None
    if start is None or end is None:
        return None
    start_line = field(start, "line")
    start_character = field(start, "character")
    end_line = field(end, "line")
    end_character = field(end, "character")
    if not (
        type(start_line) is int
        and type(start_character) is int
        and type(end_line) is int
        and type(end_character) is int
    ):
        return None
    start_line_int = cast(int, start_line)
    start_character_int = cast(int, start_character)
    end_line_int = cast(int, end_line)
    end_character_int = cast(int, end_character)
    severity_raw = field(diagnostic, "severity")
    severity_value = getattr(severity_raw, "value", severity_raw)
    if type(severity_value) is not int:
        return None
    severity = _DIAGNOSTIC_SEVERITY_NAMES.get(severity_value)
    if severity is None:
        return None
    message, _ = _truncate_utf8(str(field(diagnostic, "message") or ""), 4096)
    payload: dict[str, object] = {
        "path": safe_path,
        "range": {
            "start": _position(start_line_int, start_character_int),
            "end": _position(end_line_int, end_character_int),
        },
        "severity": severity,
        "message": message,
    }
    code = field(diagnostic, "code")
    if code is not None:
        payload["code"] = str(code)[:256]
    source = field(diagnostic, "source")
    if source:
        payload["source"] = str(source)[:256]
    return payload


def _get_diagnostics(runtime: _WorkerRuntime, params: dict[str, object]) -> dict[str, object]:
    path = str(params.get("path", ""))
    _require_supported_file(runtime, path)
    assert runtime.retriever is not None
    start_line = params.get("start_line")
    end_line = params.get("end_line")
    severity_name = str(params.get("min_severity", "hint"))
    maximum = cast(int, params.get("max_results", 500))
    severity = _DIAGNOSTIC_SEVERITY_VALUES.get(severity_name)
    if severity is None:
        raise WorkerSemanticError(
            SEMANTIC_BACKEND_ERROR,
            f"Unsupported diagnostic severity: {severity_name}",
        )
    raw = runtime.retriever.get_file_diagnostics(
        path,
        start_line=(int(start_line) - 1 if isinstance(start_line, int) else 0),
        end_line=(int(end_line) - 1 if isinstance(end_line, int) else -1),
        min_severity=severity,
    )
    diagnostics: list[dict[str, object]] = []
    truncated = False
    for diagnostic in raw:
        payload = _diagnostic_payload(runtime, path, diagnostic)
        if payload is None:
            continue
        if len(diagnostics) >= maximum:
            truncated = True
            break
        diagnostics.append(payload)
    return {"diagnostics": diagnostics, "truncated": truncated, "warnings": []}


def _dispatch(runtime: _WorkerRuntime, operation: str, params: dict[str, object]) -> dict[str, object]:
    if operation == "list_symbols":
        return _list_symbols(runtime, params)
    if operation == "find_symbol":
        return _find_symbol(runtime, params)
    if operation == "find_definition":
        return _find_definition(runtime, params)
    if operation == "find_references":
        return _find_references(runtime, params)
    if operation == "find_implementations":
        return _find_implementations(runtime, params)
    if operation == "get_diagnostics":
        return _get_diagnostics(runtime, params)
    raise WorkerSemanticError(
        SEMANTIC_BACKEND_ERROR,
        f"Unsupported semantic operation: {operation}",
    )


def _create_runtime(args: argparse.Namespace) -> _WorkerRuntime:
    project_root = Path(args.project_root).expanduser().resolve(strict=True)
    if not project_root.is_dir():
        raise ValueError("project root must be a directory")
    state_dir = Path(args.state_dir).expanduser().resolve(strict=False)
    state_dir.mkdir(parents=True, exist_ok=True)
    excluded_roots = _canonical_excluded_roots(project_root, list(args.excluded_root))
    ignored_paths = _ignored_patterns(project_root, excluded_roots)

    config = SerenaConfig(
        gui_log_window=False,
        web_dashboard=False,
        language_backend=LanguageBackend.LSP,
        project_serena_folder_location=str(state_dir / "project-state"),
    )
    project_config = ProjectConfig.autogenerate(
        project_root,
        config,
        project_name=args.project_id,
        save_to_disk=False,
        interactive=False,
    )
    project_config.ignored_paths.extend(ignored_paths)
    registered = SerenaRegisteredProject(str(project_root), project_config)
    project = registered.get_project_instance(config)
    languages = tuple(language.value for language in project_config.languages)
    if not languages:
        return _WorkerRuntime(project_root, excluded_roots, project, None, languages)
    project.create_language_server_manager()
    retriever = LanguageServerSymbolRetriever(project)
    return _WorkerRuntime(project_root, excluded_roots, project, retriever, languages)


def _write_protocol_message(stream: TextIO, message: dict[str, object]) -> None:
    payload = encode_message(message)
    stream.buffer.write(payload)
    stream.buffer.flush()


def _error_response(request_id: str, exc: WorkerSemanticError) -> dict[str, object]:
    return {
        "type": "response",
        "protocol": 1,
        "id": request_id,
        "ok": False,
        "error": {
            "code": exc.code,
            "message": exc.message[:1024],
            "retryable": exc.retryable,
            "details": dict(exc.details or {}),
        },
    }


def run_worker(args: argparse.Namespace) -> int:
    protocol_stdout = sys.stdout
    runtime: _WorkerRuntime | None = None
    try:
        with contextlib.redirect_stdout(sys.stderr):
            runtime = _create_runtime(args)
        _write_protocol_message(
            protocol_stdout,
            {
                "type": "ready",
                "protocol": 1,
                "project_id": args.project_id,
                "backend": "serena",
                "backend_version": SUPPORTED_SERENA_VERSION,
                "languages": list(runtime.languages),
            },
        )
        for raw_line in sys.stdin.buffer:
            try:
                request = decode_message(raw_line)
                if request.get("type") != "request":
                    raise WorkerProtocolError("worker accepts request messages only")
                request_id = str(request["id"])
                operation = str(request["op"])
                params = request["params"]
                assert isinstance(params, dict)
                with contextlib.redirect_stdout(sys.stderr):
                    result = _dispatch(runtime, operation, params)
                response: dict[str, object] = {
                    "type": "response",
                    "protocol": 1,
                    "id": request_id,
                    "ok": True,
                    "result": result,
                }
            except WorkerSemanticError as exc:
                response = _error_response(locals().get("request_id", "invalid"), exc)
            except WorkerProtocolError as exc:
                response = _error_response(
                    locals().get("request_id", "invalid"),
                    WorkerSemanticError(
                        SEMANTIC_BACKEND_ERROR,
                        f"Invalid worker protocol request: {exc}",
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - external backend failures stay structured
                print(
                    f"semantic worker operation failed: {type(exc).__name__}: {str(exc)[:512]}",
                    file=sys.stderr,
                )
                response = _error_response(
                    locals().get("request_id", "invalid"),
                    WorkerSemanticError(
                        SEMANTIC_BACKEND_ERROR,
                        "Serena language-server operation failed.",
                        retryable=True,
                    ),
                )
            _write_protocol_message(protocol_stdout, response)
        return 0
    except Exception as exc:  # noqa: BLE001 - startup diagnostics go to stderr only
        print(
            f"semantic worker startup failed: {type(exc).__name__}: {str(exc)[:1024]}",
            file=sys.stderr,
        )
        return 1
    finally:
        if runtime is not None and runtime.project is not None:
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    runtime.project.shutdown(timeout=2.0)
            except Exception as exc:  # noqa: BLE001 - shutdown is best effort in child
                print(
                    f"semantic worker shutdown failed: {type(exc).__name__}: {str(exc)[:512]}",
                    file=sys.stderr,
                )


def main(argv: list[str] | None = None) -> int:
    return run_worker(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
