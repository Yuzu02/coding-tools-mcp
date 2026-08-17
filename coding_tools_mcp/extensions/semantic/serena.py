from __future__ import annotations

import importlib.metadata
import itertools
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..projects.registry import ProjectRegistry, RegisteredProject
from ..projects.runtime import ProjectRuntimeManager
from .backend import (
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_BACKEND_UNAVAILABLE,
    SEMANTIC_PROJECT_START_FAILED,
    SEMANTIC_TIMEOUT,
    SemanticBackendError,
)
from .extension import SemanticConfig
from .model import (
    FindDefinitionRequest,
    FindDefinitionResult,
    FindReferencesRequest,
    FindReferencesResult,
    FindSymbolRequest,
    FindSymbolResult,
    ListSymbolsRequest,
    ListSymbolsResult,
    SemanticPosition,
    SemanticRange,
    SemanticReference,
    SemanticSymbol,
)
from .protocol import (
    MAX_WORKER_MESSAGE_BYTES,
    WORKER_PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_message,
    encode_message,
)


SERENA_DISTRIBUTION = "serena-agent"
SUPPORTED_SERENA_VERSION = "1.5.3"
STDERR_DIAGNOSTIC_BYTES = 16 * 1024

_WORKER_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
)


@dataclass(frozen=True)
class SerenaAvailability:
    available: bool
    version: str | None
    reason: str | None = None


def detect_serena() -> SerenaAvailability:
    try:
        version = importlib.metadata.version(SERENA_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return SerenaAvailability(False, None, "serena-agent is not installed")
    if version != SUPPORTED_SERENA_VERSION:
        return SerenaAvailability(
            False,
            version,
            f"unsupported Serena version: {version}",
        )
    return SerenaAvailability(True, version)


def _worker_environment(
    state_dir: Path,
    *,
    environ: Mapping[str, str] | None,
    allow_dependency_install: bool,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    env = {
        name: value
        for name in _WORKER_ENV_ALLOWLIST
        if isinstance((value := source.get(name)), str) and value
    }

    worker_home = state_dir / "home"
    worker_tmp = state_dir / "tmp"
    worker_cache = state_dir / "cache"
    serena_home = state_dir / "serena-home"
    for path in (state_dir, worker_home, worker_tmp, worker_cache, serena_home):
        path.mkdir(parents=True, exist_ok=True)

    env.update(
        {
            "HOME": str(worker_home),
            "USERPROFILE": str(worker_home),
            "TMPDIR": str(worker_tmp),
            "TEMP": str(worker_tmp),
            "TMP": str(worker_tmp),
            "XDG_CACHE_HOME": str(worker_cache),
            "SERENA_HOME": str(serena_home),
            "PYTHONUNBUFFERED": "1",
        }
    )
    if not allow_dependency_install:
        env["UV_OFFLINE"] = "1"
        env["NPM_CONFIG_OFFLINE"] = "true"
    return env


@dataclass(frozen=True)
class _WorkerExited:
    returncode: int | None


_WorkerEvent = dict[str, object] | WorkerProtocolError | _WorkerExited


class _SerenaWorker:
    def __init__(
        self,
        *,
        project: RegisteredProject,
        state_dir: Path,
        excluded_roots: tuple[Path, ...],
        start_timeout_seconds: int,
        request_timeout_seconds: int,
        command: Sequence[str] | None = None,
        environ: Mapping[str, str] | None = None,
        allow_dependency_install: bool = False,
    ) -> None:
        self.project = project
        self.project_id = project.project_id
        self.state_dir = state_dir
        self.excluded_roots = excluded_roots
        self.start_timeout_seconds = start_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.command = tuple(command) if command is not None else None
        self.environ = _worker_environment(
            state_dir,
            environ=environ,
            allow_dependency_install=allow_dependency_install,
        )
        self.backend_version: str | None = None
        self.languages: tuple[str, ...] = ()
        self._messages: queue.Queue[_WorkerEvent] = queue.Queue()
        self._request_ids = itertools.count(1)
        self._request_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._stderr_tail = b""
        self._closed = False

        process_command = list(self.command or self._production_command())
        self._process = subprocess.Popen(
            process_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=self.environ,
        )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name=f"semantic-stdout-{self.project_id}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"semantic-stderr-{self.project_id}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        try:
            self._wait_ready()
        except Exception:
            self.close()
            raise

    def _production_command(self) -> tuple[str, ...]:
        command: list[str] = [
            sys.executable,
            "-m",
            "coding_tools_mcp.extensions.semantic.serena_worker",
            "--project-id",
            self.project.project_id,
            "--project-root",
            str(self.project.root),
            "--state-dir",
            str(self.state_dir),
        ]
        for root in self.excluded_roots:
            command.extend(("--excluded-root", str(root)))
        return tuple(command)

    @property
    def alive(self) -> bool:
        return not self._closed and self._process.poll() is None

    def _append_stderr(self, chunk: bytes) -> None:
        with self._stderr_lock:
            self._stderr_tail = (self._stderr_tail + chunk)[-STDERR_DIAGNOSTIC_BYTES:]

    def _stderr_diagnostic(self) -> str:
        with self._stderr_lock:
            value = self._stderr_tail
        diagnostic = value.decode("utf-8", errors="replace")
        encoded = diagnostic.encode("utf-8")
        if len(encoded) <= STDERR_DIAGNOSTIC_BYTES:
            return diagnostic
        return encoded[-STDERR_DIAGNOSTIC_BYTES:].decode("utf-8", errors="ignore")

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                self._append_stderr(chunk)
        except (OSError, ValueError):
            return

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:
            self._messages.put(WorkerProtocolError("worker stdout is unavailable"))
            return
        try:
            while True:
                line = stream.readline(MAX_WORKER_MESSAGE_BYTES + 1)
                if not line:
                    self._messages.put(_WorkerExited(self._process.poll()))
                    return
                try:
                    message = decode_message(line)
                except WorkerProtocolError as exc:
                    self._messages.put(exc)
                    return
                self._messages.put(message)
        except (OSError, ValueError) as exc:
            self._messages.put(WorkerProtocolError(f"worker stdout read failed: {exc}"))

    def _event(self, timeout: int) -> _WorkerEvent:
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def _details_with_diagnostic(self, details: Mapping[str, object] | None = None) -> dict[str, object]:
        bounded = dict(details or {})
        diagnostic = self._stderr_diagnostic()
        if diagnostic and "diagnostic" not in bounded:
            bounded["diagnostic"] = diagnostic
        return bounded

    def _start_failure(self, message: str, *, retryable: bool = True) -> SemanticBackendError:
        return SemanticBackendError(
            SEMANTIC_PROJECT_START_FAILED,
            message,
            retryable=retryable,
            details=self._details_with_diagnostic(),
        )

    def _wait_ready(self) -> None:
        try:
            event = self._event(self.start_timeout_seconds)
        except TimeoutError as exc:
            raise self._start_failure("semantic worker did not become ready before timeout") from exc

        if isinstance(event, WorkerProtocolError):
            raise self._start_failure(f"semantic worker ready message is invalid: {event}")
        if isinstance(event, _WorkerExited):
            raise self._start_failure(
                f"semantic worker exited before ready with code {event.returncode}"
            )
        if event.get("type") != "ready":
            raise self._start_failure("semantic worker did not send a ready message")

        project_id = event.get("project_id")
        backend = event.get("backend")
        version = event.get("backend_version")
        languages = event.get("languages")
        if project_id != self.project.project_id:
            raise self._start_failure("semantic worker ready project does not match requested project")
        if backend != "serena":
            raise self._start_failure("semantic worker ready backend is not serena")
        if version != SUPPORTED_SERENA_VERSION:
            raise self._start_failure(
                f"semantic worker ready version is unsupported: {version}"
            )
        if not isinstance(languages, list) or any(not isinstance(item, str) for item in languages):
            raise self._start_failure("semantic worker ready languages are invalid")
        self.backend_version = cast(str, version)
        self.languages = tuple(cast(list[str], languages))

    def _terminate_after_failure(self) -> None:
        self.close()

    def _transport_failure(self, message: str) -> SemanticBackendError:
        self._terminate_after_failure()
        return SemanticBackendError(
            SEMANTIC_BACKEND_ERROR,
            message,
            retryable=True,
            details=self._details_with_diagnostic(),
        )

    def request(self, operation: str, params: Mapping[str, object]) -> dict[str, object]:
        with self._request_lock:
            if not self.alive:
                raise SemanticBackendError(
                    SEMANTIC_BACKEND_ERROR,
                    "semantic worker is not running",
                    retryable=True,
                    details=self._details_with_diagnostic(),
                )

            request_id = f"r{next(self._request_ids)}"
            try:
                payload = encode_message(
                    {
                        "type": "request",
                        "protocol": WORKER_PROTOCOL_VERSION,
                        "id": request_id,
                        "op": operation,
                        "params": dict(params),
                    }
                )
            except WorkerProtocolError as exc:
                raise SemanticBackendError(
                    SEMANTIC_BACKEND_ERROR,
                    f"invalid semantic worker request: {exc}",
                    retryable=False,
                ) from exc

            stream = self._process.stdin
            if stream is None:
                raise self._transport_failure("semantic worker stdin is unavailable")
            try:
                stream.write(payload)
                stream.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise self._transport_failure("semantic worker request write failed") from exc

            try:
                event = self._event(self.request_timeout_seconds)
            except TimeoutError as exc:
                self._terminate_after_failure()
                raise SemanticBackendError(
                    SEMANTIC_TIMEOUT,
                    "semantic worker request timed out",
                    retryable=True,
                    details=self._details_with_diagnostic(),
                ) from exc

            if isinstance(event, WorkerProtocolError):
                raise self._transport_failure(f"semantic worker response is invalid: {event}")
            if isinstance(event, _WorkerExited):
                raise self._transport_failure(
                    f"semantic worker exited with code {event.returncode}"
                )
            if event.get("type") != "response" or event.get("id") != request_id:
                raise self._transport_failure("semantic worker returned a mismatched response")

            if event.get("ok") is True:
                result = event.get("result")
                if not isinstance(result, dict):
                    raise self._transport_failure("semantic worker result is not an object")
                return cast(dict[str, object], result)

            error = event.get("error")
            if not isinstance(error, dict):
                raise self._transport_failure("semantic worker error is not an object")
            code = error.get("code")
            message = error.get("message")
            retryable = error.get("retryable")
            details = error.get("details")
            if not isinstance(code, str) or not isinstance(message, str) or type(retryable) is not bool:
                raise self._transport_failure("semantic worker error fields are invalid")
            normalized_details = details if isinstance(details, dict) else {}
            raise SemanticBackendError(
                code,
                message,
                retryable=retryable,
                details=self._details_with_diagnostic(cast(dict[str, object], normalized_details)),
            )

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            stream = self._process.stdin
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            if self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    try:
                        self._process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
                except OSError:
                    pass
            for thread in (self._stdout_thread, self._stderr_thread):
                if thread is threading.current_thread():
                    continue
                thread.join(timeout=1.0)
            for output_stream in (self._process.stdout, self._process.stderr):
                if output_stream is None or output_stream.closed:
                    continue
                try:
                    output_stream.close()
                except (OSError, ValueError):
                    pass


WorkerFactory = Callable[..., _SerenaWorker]


def _protocol_error(message: str, *, operation: str) -> SemanticBackendError:
    return SemanticBackendError(
        SEMANTIC_BACKEND_ERROR,
        message,
        retryable=True,
        details={"operation": operation},
    )


def _require_mapping(value: object, *, path: str, operation: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _protocol_error(f"worker result {path} must be an object", operation=operation)
    return cast(Mapping[str, object], value)


def _position(value: object, *, path: str, operation: str) -> SemanticPosition:
    raw = _require_mapping(value, path=path, operation=operation)
    line = raw.get("line")
    column = raw.get("column")
    if type(line) is not int or type(column) is not int or line < 1 or column < 1:
        raise _protocol_error(f"worker result {path} has an invalid position", operation=operation)
    return SemanticPosition(line, column)


def _range(value: object, *, path: str, operation: str) -> SemanticRange:
    raw = _require_mapping(value, path=path, operation=operation)
    try:
        return SemanticRange(
            _position(raw.get("start"), path=f"{path}.start", operation=operation),
            _position(raw.get("end"), path=f"{path}.end", operation=operation),
        )
    except ValueError as exc:
        raise _protocol_error(f"worker result {path} has an invalid range", operation=operation) from exc


def _symbol(value: object, *, path: str, operation: str) -> SemanticSymbol:
    raw = _require_mapping(value, path=path, operation=operation)
    required_strings: dict[str, str] = {}
    for key in ("name", "name_path", "kind", "path"):
        item = raw.get(key)
        if not isinstance(item, str):
            raise _protocol_error(f"worker result {path}.{key} must be a string", operation=operation)
        required_strings[key] = item
    raw_children = raw.get("children", [])
    if not isinstance(raw_children, list):
        raise _protocol_error(f"worker result {path}.children must be an array", operation=operation)
    children = tuple(
        _symbol(item, path=f"{path}.children[{index}]", operation=operation)
        for index, item in enumerate(raw_children)
    )
    raw_range = raw.get("range")
    semantic_range = None if raw_range is None else _range(raw_range, path=f"{path}.range", operation=operation)
    body = raw.get("body")
    if body is not None and not isinstance(body, str):
        raise _protocol_error(f"worker result {path}.body must be a string", operation=operation)
    body_truncated = raw.get("body_truncated", False)
    if type(body_truncated) is not bool:
        raise _protocol_error(f"worker result {path}.body_truncated must be boolean", operation=operation)
    return SemanticSymbol(
        name=required_strings["name"],
        name_path=required_strings["name_path"],
        kind=required_strings["kind"],
        path=required_strings["path"],
        range=semantic_range,
        children=children,
        body=body,
        body_truncated=body_truncated,
    )


def _reference(value: object, *, path: str, operation: str) -> SemanticReference:
    raw = _require_mapping(value, path=path, operation=operation)
    source_path = raw.get("path")
    if not isinstance(source_path, str):
        raise _protocol_error(f"worker result {path}.path must be a string", operation=operation)
    containing = raw.get("containing_symbol")
    return SemanticReference(
        path=source_path,
        range=_range(raw.get("range"), path=f"{path}.range", operation=operation),
        containing_symbol=(
            None
            if containing is None
            else _symbol(containing, path=f"{path}.containing_symbol", operation=operation)
        ),
    )


def _result_common(
    raw: Mapping[str, object],
    *,
    operation: str,
) -> tuple[bool, tuple[str, ...]]:
    truncated = raw.get("truncated", False)
    if type(truncated) is not bool:
        raise _protocol_error("worker result truncated must be boolean", operation=operation)
    warnings = raw.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise _protocol_error("worker result warnings must be a string array", operation=operation)
    return truncated, tuple(item[:512] for item in cast(list[str], warnings[:32]))


class SerenaSemanticBackend:
    backend_name = "serena"

    def __init__(
        self,
        config: SemanticConfig,
        registry: ProjectRegistry,
        runtimes: ProjectRuntimeManager,
        *,
        availability: SerenaAvailability | None = None,
        worker_factory: WorkerFactory | None = None,
    ) -> None:
        detected = availability or detect_serena()
        self.available = detected.available
        self.backend_version = detected.version
        self.availability_reason = detected.reason
        self.config = config
        self.registry = registry
        self.runtimes = runtimes
        self._workers: dict[str, _SerenaWorker] = {}
        self._lock = threading.RLock()
        if worker_factory is None:
            self._worker_factory: WorkerFactory = lambda **kwargs: _SerenaWorker(
                **kwargs,
                allow_dependency_install=config.allow_dependency_install,
            )
        else:
            self._worker_factory = worker_factory

    def _ensure_available(self) -> None:
        if self.available:
            return
        raise SemanticBackendError(
            SEMANTIC_BACKEND_UNAVAILABLE,
            self.availability_reason or "Serena semantic backend is unavailable",
            retryable=False,
        )

    def _create_worker(self, project: RegisteredProject) -> _SerenaWorker:
        runtime = self.runtimes.require(project.project_id)
        state_dir = runtime.workspace.runtime_dir / "semantic" / "serena"
        return self._worker_factory(
            project=project,
            state_dir=state_dir,
            excluded_roots=self.registry.excluded_roots_for(project.project_id),
            start_timeout_seconds=self.config.semantic_start_timeout_seconds,
            request_timeout_seconds=self.config.semantic_request_timeout_seconds,
        )

    def _worker_for(self, project: RegisteredProject) -> _SerenaWorker:
        self._ensure_available()
        with self._lock:
            existing = self._workers.get(project.project_id)
            if existing is not None and existing.alive:
                return existing
            if existing is not None:
                self._workers.pop(project.project_id, None)
        if existing is not None:
            existing.close()

        created = self._create_worker(project)
        with self._lock:
            raced = self._workers.get(project.project_id)
            if raced is not None and raced.alive:
                created.close()
                return raced
            if raced is not None:
                raced.close()
            self._workers[project.project_id] = created
            return created

    def _discard_worker(self, project_id: str, worker: _SerenaWorker) -> None:
        with self._lock:
            if self._workers.get(project_id) is worker:
                self._workers.pop(project_id, None)
        worker.close()

    def _request(
        self,
        project: RegisteredProject,
        operation: str,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        worker = self._worker_for(project)
        try:
            return worker.request(operation, params)
        except SemanticBackendError:
            if not worker.alive:
                self._discard_worker(project.project_id, worker)
            raise

    def list_symbols(
        self,
        project: RegisteredProject,
        request: ListSymbolsRequest,
    ) -> ListSymbolsResult:
        operation = "list_symbols"
        raw = self._request(
            project,
            operation,
            {
                "path": request.path,
                "depth": request.depth,
                "max_results": request.max_results,
            },
        )
        raw_symbols = raw.get("symbols")
        if not isinstance(raw_symbols, list):
            raise _protocol_error("worker result symbols must be an array", operation=operation)
        truncated, warnings = _result_common(raw, operation=operation)
        return ListSymbolsResult(
            tuple(
                _symbol(item, path=f"symbols[{index}]", operation=operation)
                for index, item in enumerate(raw_symbols)
            ),
            truncated=truncated,
            warnings=warnings,
        )

    def find_symbol(
        self,
        project: RegisteredProject,
        request: FindSymbolRequest,
    ) -> FindSymbolResult:
        operation = "find_symbol"
        raw = self._request(
            project,
            operation,
            {
                "query": request.query,
                "path": request.path,
                "include_body": request.include_body,
                "max_results": request.max_results,
            },
        )
        raw_symbols = raw.get("symbols")
        if not isinstance(raw_symbols, list):
            raise _protocol_error("worker result symbols must be an array", operation=operation)
        truncated, warnings = _result_common(raw, operation=operation)
        return FindSymbolResult(
            tuple(
                _symbol(item, path=f"symbols[{index}]", operation=operation)
                for index, item in enumerate(raw_symbols)
            ),
            truncated=truncated,
            warnings=warnings,
        )

    def find_definition(
        self,
        project: RegisteredProject,
        request: FindDefinitionRequest,
    ) -> FindDefinitionResult:
        operation = "find_definition"
        raw = self._request(
            project,
            operation,
            {"path": request.path, "line": request.line, "column": request.column},
        )
        definitions = raw.get("definitions")
        if not isinstance(definitions, list):
            raise _protocol_error("worker result definitions must be an array", operation=operation)
        truncated, warnings = _result_common(raw, operation=operation)
        return FindDefinitionResult(
            tuple(
                _symbol(item, path=f"definitions[{index}]", operation=operation)
                for index, item in enumerate(definitions)
            ),
            truncated=truncated,
            warnings=warnings,
        )

    def find_references(
        self,
        project: RegisteredProject,
        request: FindReferencesRequest,
    ) -> FindReferencesResult:
        operation = "find_references"
        raw = self._request(
            project,
            operation,
            {
                "path": request.path,
                "line": request.line,
                "column": request.column,
                "include_declaration": request.include_declaration,
                "max_results": request.max_results,
            },
        )
        references = raw.get("references")
        if not isinstance(references, list):
            raise _protocol_error("worker result references must be an array", operation=operation)
        truncated, warnings = _result_common(raw, operation=operation)
        return FindReferencesResult(
            tuple(
                _reference(item, path=f"references[{index}]", operation=operation)
                for index, item in enumerate(references)
            ),
            truncated=truncated,
            warnings=warnings,
        )

    def close_project(self, project_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(project_id, None)
        if worker is not None:
            worker.close()

    def close(self) -> tuple[str, ...]:
        with self._lock:
            workers = tuple(self._workers.items())
            self._workers.clear()
        warnings: list[str] = []
        for project_id, worker in workers:
            try:
                worker.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must attempt every worker
                warnings.append(f"{project_id}: {str(exc)[:512]}")
        return tuple(warnings)


__all__ = [
    "SERENA_DISTRIBUTION",
    "SUPPORTED_SERENA_VERSION",
    "SerenaAvailability",
    "SerenaSemanticBackend",
    "detect_serena",
]
