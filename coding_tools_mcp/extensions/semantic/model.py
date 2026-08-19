from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticPosition:
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("semantic positions are one-based")

    def payload(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True)
class SemanticRange:
    start: SemanticPosition
    end: SemanticPosition

    def __post_init__(self) -> None:
        if (self.end.line, self.end.column) < (self.start.line, self.start.column):
            raise ValueError("semantic range end precedes start")

    def payload(self) -> dict[str, object]:
        return {"start": self.start.payload(), "end": self.end.payload()}


@dataclass(frozen=True)
class SemanticSymbol:
    name: str
    name_path: str
    kind: str
    path: str
    range: SemanticRange | None = None
    children: tuple[SemanticSymbol, ...] = ()
    body: str | None = None
    body_truncated: bool = False

    @classmethod
    def summary(
        cls,
        *,
        name: str,
        name_path: str,
        kind: str,
        path: str,
    ) -> SemanticSymbol:
        return cls(name=name, name_path=name_path, kind=kind, path=path)

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "name": self.name,
            "name_path": self.name_path,
            "kind": self.kind,
            "path": self.path,
            "children": [child.payload() for child in self.children],
        }
        if self.range is not None:
            value["range"] = self.range.payload()
        if self.body is not None:
            value["body"] = self.body
            value["body_truncated"] = self.body_truncated
        return value


@dataclass(frozen=True)
class SemanticReference:
    path: str
    range: SemanticRange
    containing_symbol: SemanticSymbol | None = None

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "path": self.path,
            "range": self.range.payload(),
        }
        if self.containing_symbol is not None:
            value["containing_symbol"] = self.containing_symbol.payload()
        return value


@dataclass(frozen=True)
class SemanticDiagnostic:
    path: str
    range: SemanticRange
    severity: str
    message: str
    code: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning", "information", "hint"}:
            raise ValueError("diagnostic severity is unsupported")

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "path": self.path,
            "range": self.range.payload(),
            "severity": self.severity,
            "message": self.message,
        }
        if self.code is not None:
            value["code"] = self.code
        if self.source is not None:
            value["source"] = self.source
        return value


@dataclass(frozen=True)
class ListSymbolsRequest:
    path: str
    depth: int = 1
    max_results: int = 500


@dataclass(frozen=True)
class FindSymbolRequest:
    query: str
    path: str = ""
    include_body: bool = False
    max_results: int = 50


@dataclass(frozen=True)
class FindDefinitionRequest:
    path: str
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("semantic positions are one-based")


@dataclass(frozen=True)
class FindReferencesRequest:
    path: str
    line: int
    column: int
    include_declaration: bool = False
    max_results: int = 500

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("semantic positions are one-based")


@dataclass(frozen=True)
class FindImplementationsRequest:
    path: str
    line: int
    column: int
    max_results: int = 200

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("semantic positions are one-based")


@dataclass(frozen=True)
class GetDiagnosticsRequest:
    path: str
    start_line: int | None = None
    end_line: int | None = None
    min_severity: str = "hint"
    max_results: int = 500

    def __post_init__(self) -> None:
        if self.start_line is not None and self.start_line < 1:
            raise ValueError("diagnostic line ranges are one-based")
        if self.end_line is not None and self.end_line < 1:
            raise ValueError("diagnostic line ranges are one-based")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("diagnostic end_line precedes start_line")
        if self.min_severity not in {"error", "warning", "information", "hint"}:
            raise ValueError("diagnostic severity is unsupported")


@dataclass(frozen=True)
class ListSymbolsResult:
    symbols: tuple[SemanticSymbol, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "symbols": [symbol.payload() for symbol in self.symbols],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FindSymbolResult:
    symbols: tuple[SemanticSymbol, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "symbols": [symbol.payload() for symbol in self.symbols],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FindDefinitionResult:
    definitions: tuple[SemanticSymbol, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "definitions": [symbol.payload() for symbol in self.definitions],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FindReferencesResult:
    references: tuple[SemanticReference, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "references": [reference.payload() for reference in self.references],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class FindImplementationsResult:
    implementations: tuple[SemanticSymbol, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "implementations": [symbol.payload() for symbol in self.implementations],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class GetDiagnosticsResult:
    diagnostics: tuple[SemanticDiagnostic, ...]
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "diagnostics": [diagnostic.payload() for diagnostic in self.diagnostics],
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }
