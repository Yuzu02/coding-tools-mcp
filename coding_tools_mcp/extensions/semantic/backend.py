from __future__ import annotations

from typing import Mapping, Protocol

from ..projects.registry import RegisteredProject
from ..services import CapabilityKey
from .model import (
    FindDefinitionRequest,
    FindDefinitionResult,
    FindImplementationsRequest,
    FindImplementationsResult,
    FindReferencesRequest,
    FindReferencesResult,
    FindSymbolRequest,
    FindSymbolResult,
    GetDiagnosticsRequest,
    GetDiagnosticsResult,
    ListSymbolsRequest,
    ListSymbolsResult,
)


SEMANTIC_BACKEND_UNAVAILABLE = "SEMANTIC_BACKEND_UNAVAILABLE"
SEMANTIC_PROJECT_START_FAILED = "SEMANTIC_PROJECT_START_FAILED"
SEMANTIC_LANGUAGE_UNSUPPORTED = "SEMANTIC_LANGUAGE_UNSUPPORTED"
SEMANTIC_FILE_UNSUPPORTED = "SEMANTIC_FILE_UNSUPPORTED"
SEMANTIC_SYMBOL_NOT_FOUND = "SEMANTIC_SYMBOL_NOT_FOUND"
SEMANTIC_POSITION_INVALID = "SEMANTIC_POSITION_INVALID"
SEMANTIC_TIMEOUT = "SEMANTIC_TIMEOUT"
SEMANTIC_BACKEND_ERROR = "SEMANTIC_BACKEND_ERROR"


class SemanticBackendError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})


class SemanticBackend(Protocol):
    backend_name: str
    backend_version: str | None
    available: bool
    availability_reason: str | None

    def list_symbols(
        self,
        project: RegisteredProject,
        request: ListSymbolsRequest,
    ) -> ListSymbolsResult:
        raise NotImplementedError

    def find_symbol(
        self,
        project: RegisteredProject,
        request: FindSymbolRequest,
    ) -> FindSymbolResult:
        raise NotImplementedError

    def find_definition(
        self,
        project: RegisteredProject,
        request: FindDefinitionRequest,
    ) -> FindDefinitionResult:
        raise NotImplementedError

    def find_references(
        self,
        project: RegisteredProject,
        request: FindReferencesRequest,
    ) -> FindReferencesResult:
        raise NotImplementedError

    def find_implementations(
        self,
        project: RegisteredProject,
        request: FindImplementationsRequest,
    ) -> FindImplementationsResult:
        raise NotImplementedError

    def get_diagnostics(
        self,
        project: RegisteredProject,
        request: GetDiagnosticsRequest,
    ) -> GetDiagnosticsResult:
        raise NotImplementedError

    def close_project(self, project_id: str) -> None:
        raise NotImplementedError

    def close(self) -> tuple[str, ...]:
        raise NotImplementedError


SEMANTIC_BACKEND = CapabilityKey[SemanticBackend]("semantic.backend")
