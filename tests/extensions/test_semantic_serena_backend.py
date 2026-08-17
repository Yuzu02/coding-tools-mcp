from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from coding_tools_mcp.extensions.projects.registry import ProjectRegistry, RegisteredProject
from coding_tools_mcp.extensions.semantic.backend import (
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_BACKEND_UNAVAILABLE,
    SEMANTIC_TIMEOUT,
    SemanticBackendError,
)
from coding_tools_mcp.extensions.semantic.extension import SemanticConfig
from coding_tools_mcp.extensions.semantic.model import ListSymbolsRequest
from coding_tools_mcp.extensions.semantic.serena import (
    SUPPORTED_SERENA_VERSION,
    SerenaAvailability,
    SerenaSemanticBackend,
    _SerenaWorker,
    detect_serena,
)


FAKE_WORKER = r'''
import argparse
import json
import os
import sys
import time


def emit(message):
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


parser = argparse.ArgumentParser()
parser.add_argument("--project-id", required=True)
parser.add_argument("--mode", required=True)
args = parser.parse_args()

ready_project = args.project_id + "-wrong" if args.mode == "bad_ready" else args.project_id
emit({
    "type": "ready",
    "protocol": 1,
    "project_id": ready_project,
    "backend": "serena",
    "backend_version": "1.5.3",
    "languages": ["python"],
})

for raw in sys.stdin.buffer:
    request = json.loads(raw.decode("utf-8"))
    if args.mode == "slow":
        time.sleep(2.5)
        continue
    if args.mode == "crash":
        os._exit(7)
    if args.mode == "stderr":
        sys.stderr.buffer.write(b"diagnostic:" + (b"x" * (64 * 1024)))
        sys.stderr.buffer.flush()
        emit({
            "type": "response",
            "protocol": 1,
            "id": request["id"],
            "ok": False,
            "error": {
                "code": "SEMANTIC_BACKEND_ERROR",
                "message": "fake backend failure",
                "retryable": True,
                "details": {},
            },
        })
        continue
    if args.mode == "stderr_invalid_utf8":
        sys.stderr.buffer.write(b"\xff" * (64 * 1024))
        sys.stderr.buffer.flush()
        emit({
            "type": "response",
            "protocol": 1,
            "id": request["id"],
            "ok": False,
            "error": {
                "code": "SEMANTIC_BACKEND_ERROR",
                "message": "fake backend failure",
                "retryable": True,
                "details": {},
            },
        })
        continue

    operation = request["op"]
    if operation in {"list_symbols", "find_symbol"}:
        result = {"symbols": [], "truncated": False, "warnings": []}
    elif operation == "find_definition":
        result = {"definitions": [], "truncated": False, "warnings": []}
    else:
        result = {"references": [], "truncated": False, "warnings": []}
    if args.mode == "env":
        result["environment"] = {
            "has_secret": "SECRET_SENTINEL" in os.environ,
            "home": os.environ.get("HOME"),
            "userprofile": os.environ.get("USERPROFILE"),
            "tmpdir": os.environ.get("TMPDIR"),
            "cache": os.environ.get("XDG_CACHE_HOME"),
            "serena_home": os.environ.get("SERENA_HOME"),
            "uv_offline": os.environ.get("UV_OFFLINE"),
            "npm_offline": os.environ.get("NPM_CONFIG_OFFLINE"),
        }
    emit({
        "type": "response",
        "protocol": 1,
        "id": request["id"],
        "ok": True,
        "result": result,
    })
'''


@dataclass(frozen=True)
class FakeWorkspaceHandle:
    root: Path
    runtime_dir: Path


@dataclass(frozen=True)
class FakeProjectRuntime:
    workspace: FakeWorkspaceHandle


class FakeRuntimes:
    def __init__(self, project: RegisteredProject, runtime_dir: Path) -> None:
        self.project = project
        self.runtime = FakeProjectRuntime(FakeWorkspaceHandle(project.root, runtime_dir))

    def require(self, project_id: str) -> FakeProjectRuntime:
        if project_id != self.project.project_id:
            raise AssertionError(f"unexpected project: {project_id}")
        return self.runtime


class SemanticSerenaBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        (self.project_root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.runtime_dir = self.root / "runtime"
        self.runtime_dir.mkdir()
        self.script = self.root / "fake_worker.py"
        self.script.write_text(textwrap.dedent(FAKE_WORKER), encoding="utf-8")
        self.alpha = RegisteredProject(
            project_id="alpha",
            root=self.project_root,
            markers=(),
            available=True,
        )
        self.registry = ProjectRegistry((self.alpha,))
        self.runtimes = FakeRuntimes(self.alpha, self.runtime_dir)

    def command(self, mode: str, project_id: str = "alpha") -> list[str]:
        return [
            sys.executable,
            str(self.script),
            "--project-id",
            project_id,
            "--mode",
            mode,
        ]

    def worker(
        self,
        mode: str,
        *,
        project_id: str = "alpha",
        request_timeout_seconds: int = 2,
        allow_dependency_install: bool = False,
    ) -> _SerenaWorker:
        source_env = dict(os.environ)
        source_env["SECRET_SENTINEL"] = "must-not-leak"
        return _SerenaWorker(
            project=self.alpha,
            state_dir=self.root / f"state-{mode}",
            excluded_roots=(),
            start_timeout_seconds=2,
            request_timeout_seconds=request_timeout_seconds,
            command=self.command(mode, project_id),
            environ=source_env,
            allow_dependency_install=allow_dependency_install,
        )

    def test_worker_waits_for_matching_ready_project_and_version(self) -> None:
        worker = self.worker("ready")
        self.addCleanup(worker.close)

        self.assertEqual(worker.backend_version, "1.5.3")
        self.assertEqual(worker.project_id, "alpha")
        self.assertEqual(worker.languages, ("python",))

    def test_bad_ready_is_project_start_failure(self) -> None:
        with self.assertRaises(SemanticBackendError) as raised:
            self.worker("bad_ready")

        self.assertEqual(raised.exception.code, "SEMANTIC_PROJECT_START_FAILED")
        self.assertTrue(raised.exception.retryable)

    def test_worker_round_trip_returns_result(self) -> None:
        worker = self.worker("ready")
        self.addCleanup(worker.close)

        result = worker.request(
            "list_symbols",
            {"path": "a.py", "depth": 1, "max_results": 10},
        )

        self.assertEqual(result, {"symbols": [], "truncated": False, "warnings": []})

    def test_request_timeout_terminates_worker_and_is_retryable(self) -> None:
        worker = self.worker("slow", request_timeout_seconds=1)
        self.addCleanup(worker.close)

        with self.assertRaises(SemanticBackendError) as raised:
            worker.request(
                "list_symbols",
                {"path": "a.py", "depth": 1, "max_results": 10},
            )

        self.assertEqual(raised.exception.code, SEMANTIC_TIMEOUT)
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(worker.alive)

    def test_stderr_diagnostics_are_bounded(self) -> None:
        worker = self.worker("stderr")
        self.addCleanup(worker.close)

        with self.assertRaises(SemanticBackendError) as raised:
            worker.request(
                "list_symbols",
                {"path": "a.py", "depth": 1, "max_results": 10},
            )

        diagnostic = str(raised.exception.details.get("diagnostic", ""))
        self.assertTrue(diagnostic.startswith("diagnostic:") or diagnostic.startswith("x"))
        self.assertLessEqual(len(diagnostic.encode("utf-8")), 16 * 1024)

    def test_invalid_utf8_stderr_diagnostic_remains_byte_bounded(self) -> None:
        worker = self.worker("stderr_invalid_utf8")
        self.addCleanup(worker.close)

        with self.assertRaises(SemanticBackendError) as raised:
            worker.request(
                "list_symbols",
                {"path": "a.py", "depth": 1, "max_results": 10},
            )

        diagnostic = str(raised.exception.details.get("diagnostic", ""))
        self.assertLessEqual(len(diagnostic.encode("utf-8")), 16 * 1024)

    def test_worker_environment_is_allowlisted_and_state_scoped(self) -> None:
        worker = self.worker("env")
        self.addCleanup(worker.close)

        result = worker.request(
            "list_symbols",
            {"path": "a.py", "depth": 1, "max_results": 10},
        )
        environment = result["environment"]

        self.assertIsInstance(environment, dict)
        self.assertFalse(environment["has_secret"])
        self.assertEqual(environment["home"], str(worker.state_dir / "home"))
        self.assertEqual(environment["userprofile"], str(worker.state_dir / "home"))
        self.assertEqual(environment["tmpdir"], str(worker.state_dir / "tmp"))
        self.assertEqual(environment["cache"], str(worker.state_dir / "cache"))
        self.assertEqual(environment["serena_home"], str(worker.state_dir / "serena-home"))
        self.assertEqual(environment["uv_offline"], "1")
        self.assertEqual(environment["npm_offline"], "true")

    def test_dependency_install_mode_omits_forced_offline_flags(self) -> None:
        worker = self.worker("env", allow_dependency_install=True)
        self.addCleanup(worker.close)

        result = worker.request(
            "list_symbols",
            {"path": "a.py", "depth": 1, "max_results": 10},
        )
        environment = result["environment"]

        self.assertIsNone(environment["uv_offline"])
        self.assertIsNone(environment["npm_offline"])

    def test_close_is_idempotent(self) -> None:
        worker = self.worker("ready")

        worker.close()
        worker.close()

        self.assertFalse(worker.alive)
        self.assertIsNotNone(worker._process.stdout)
        self.assertIsNotNone(worker._process.stderr)
        self.assertTrue(worker._process.stdout.closed)
        self.assertTrue(worker._process.stderr.closed)

    def test_worker_crash_is_backend_error_and_next_call_can_restart(self) -> None:
        modes = ["crash", "ready"]

        def worker_factory(**kwargs):
            mode = modes.pop(0)
            return _SerenaWorker(
                **kwargs,
                command=self.command(mode),
                environ=os.environ,
                allow_dependency_install=False,
            )

        backend = SerenaSemanticBackend(
            SemanticConfig(),
            self.registry,
            self.runtimes,  # type: ignore[arg-type]
            availability=SerenaAvailability(True, SUPPORTED_SERENA_VERSION),
            worker_factory=worker_factory,
        )
        self.addCleanup(backend.close)

        with self.assertRaises(SemanticBackendError) as raised:
            backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_ERROR)

        result = backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(result.symbols, ())
        self.assertEqual(modes, [])

    def test_backend_worker_state_is_project_runtime_scoped(self) -> None:
        seen: list[tuple[Path, tuple[Path, ...]]] = []

        def worker_factory(**kwargs):
            seen.append((kwargs["state_dir"], kwargs["excluded_roots"]))
            return _SerenaWorker(
                **kwargs,
                command=self.command("ready"),
                environ=os.environ,
                allow_dependency_install=False,
            )

        backend = SerenaSemanticBackend(
            SemanticConfig(),
            self.registry,
            self.runtimes,  # type: ignore[arg-type]
            availability=SerenaAvailability(True, SUPPORTED_SERENA_VERSION),
            worker_factory=worker_factory,
        )
        self.addCleanup(backend.close)

        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(
            seen,
            [(self.runtime_dir / "semantic" / "serena", ())],
        )

    def test_unavailable_backend_fails_without_starting_worker(self) -> None:
        called = False

        def worker_factory(**kwargs):
            nonlocal called
            called = True
            raise AssertionError(kwargs)

        backend = SerenaSemanticBackend(
            SemanticConfig(),
            self.registry,
            self.runtimes,  # type: ignore[arg-type]
            availability=SerenaAvailability(False, None, "serena-agent is not installed"),
            worker_factory=worker_factory,
        )

        with self.assertRaises(SemanticBackendError) as raised:
            backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_UNAVAILABLE)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(called)

    def test_detect_serena_requires_exact_supported_version(self) -> None:
        with patch(
            "coding_tools_mcp.extensions.semantic.serena.importlib.metadata.version",
            return_value=SUPPORTED_SERENA_VERSION,
        ):
            self.assertEqual(
                detect_serena(),
                SerenaAvailability(True, SUPPORTED_SERENA_VERSION),
            )
        with patch(
            "coding_tools_mcp.extensions.semantic.serena.importlib.metadata.version",
            return_value="1.5.4",
        ):
            self.assertEqual(
                detect_serena(),
                SerenaAvailability(False, "1.5.4", "unsupported Serena version: 1.5.4"),
            )


if __name__ == "__main__":
    unittest.main()
