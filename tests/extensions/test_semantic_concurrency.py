from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

from coding_tools_mcp.extensions import ExtensionRegistry, RuntimeConfig
from coding_tools_mcp.extensions.projects import ProjectsExtension
from coding_tools_mcp.extensions.projects.registry import ProjectRegistry, RegisteredProject
from coding_tools_mcp.extensions.semantic.backend import (
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_BACKEND_UNAVAILABLE,
    SemanticBackendError,
)
from coding_tools_mcp.extensions.semantic.extension import SemanticConfig, SemanticExtension
from coding_tools_mcp.extensions.semantic.model import (
    FindDefinitionRequest,
    FindDefinitionResult,
    FindReferencesRequest,
    FindReferencesResult,
    FindSymbolRequest,
    FindSymbolResult,
    ListSymbolsRequest,
    ListSymbolsResult,
)
from coding_tools_mcp.extensions.semantic.serena import (
    SUPPORTED_SERENA_VERSION,
    SerenaAvailability,
    SerenaSemanticBackend,
)
from coding_tools_mcp.server import Runtime


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass(frozen=True)
class FakeWorkspaceHandle:
    root: Path
    runtime_dir: Path

    @property
    def state_dir(self) -> Path:
        return self.runtime_dir

    @property
    def cache_dir(self) -> Path:
        return self.runtime_dir / "cache"


@dataclass(frozen=True)
class FakeProjectRuntime:
    workspace: FakeWorkspaceHandle


class FakeRuntimes:
    def __init__(self, projects: tuple[RegisteredProject, ...], runtime_root: Path) -> None:
        self._projects = {project.project_id: project for project in projects}
        self.runtime_root = runtime_root

    def require(self, project_id: str) -> FakeProjectRuntime:
        project = self._projects[project_id]
        return FakeProjectRuntime(
            FakeWorkspaceHandle(
                root=project.root,
                runtime_dir=self.runtime_root / project_id,
            )
        )


class FakeWorker:
    def __init__(self, factory: "FakeWorkerFactory", project_id: str) -> None:
        self.factory = factory
        self.project_id = project_id
        self.alive = True
        self.closed = False
        self.close_error: Exception | None = None
        self._request_lock = threading.Lock()

    def request(self, operation: str, params: dict[str, object]) -> dict[str, object]:
        with self._request_lock:
            self.factory.enter(self.project_id)
            try:
                block = self.factory.blocks.get(self.project_id)
                if block is not None:
                    block.wait(5)
                if self.project_id in self.factory.crash_projects:
                    self.factory.crash_projects.remove(self.project_id)
                    self.alive = False
                    raise SemanticBackendError(
                        SEMANTIC_BACKEND_ERROR,
                        f"{self.project_id} crashed",
                        retryable=True,
                    )
                if operation in {"list_symbols", "find_symbol"}:
                    return {"symbols": [], "truncated": False, "warnings": []}
                if operation == "find_definition":
                    return {"definitions": [], "truncated": False, "warnings": []}
                return {"references": [], "truncated": False, "warnings": []}
            finally:
                self.factory.leave(self.project_id)

    def close(self) -> None:
        self.closed = True
        self.alive = False
        if self.close_error is not None:
            raise self.close_error


class FakeWorkerFactory:
    def __init__(self) -> None:
        self.created_project_ids: list[str] = []
        self.workers: dict[str, FakeWorker] = {}
        self.all_workers: list[FakeWorker] = []
        self.blocks: dict[str, threading.Event] = {}
        self.entered: dict[str, threading.Event] = {}
        self.crash_projects: set[str] = set()
        self._metrics_lock = threading.Lock()
        self._in_flight: dict[str, int] = {}
        self.max_by_project: dict[str, int] = {}
        self.global_in_flight = 0
        self.max_global_in_flight = 0

    def __call__(self, **kwargs: object) -> FakeWorker:
        project = kwargs["project"]
        assert isinstance(project, RegisteredProject)
        worker = FakeWorker(self, project.project_id)
        self.created_project_ids.append(project.project_id)
        self.workers[project.project_id] = worker
        self.all_workers.append(worker)
        return worker

    def block_project(self, project_id: str) -> None:
        self.blocks[project_id] = threading.Event()
        self.entered[project_id] = threading.Event()

    def release_project(self, project_id: str) -> None:
        self.blocks[project_id].set()

    def wait_until_in_flight(self, project_id: str) -> None:
        self.assert_event(self.entered[project_id], project_id)

    @staticmethod
    def assert_event(event: threading.Event, project_id: str) -> None:
        if not event.wait(5):
            raise AssertionError(f"project never entered request body: {project_id}")

    def enter(self, project_id: str) -> None:
        with self._metrics_lock:
            current = self._in_flight.get(project_id, 0) + 1
            self._in_flight[project_id] = current
            self.max_by_project[project_id] = max(self.max_by_project.get(project_id, 0), current)
            self.global_in_flight += 1
            self.max_global_in_flight = max(self.max_global_in_flight, self.global_in_flight)
            event = self.entered.get(project_id)
            if event is not None:
                event.set()

    def leave(self, project_id: str) -> None:
        with self._metrics_lock:
            self._in_flight[project_id] -= 1
            self.global_in_flight -= 1

    def max_in_flight_for(self, project_id: str) -> int:
        with self._metrics_lock:
            return self.max_by_project.get(project_id, 0)


class FailingBackend:
    backend_name = "failing"
    backend_version = "test"
    available = True
    availability_reason = None

    @staticmethod
    def _fail() -> None:
        raise SemanticBackendError(
            SEMANTIC_BACKEND_ERROR,
            "synthetic semantic failure",
            retryable=True,
        )

    def list_symbols(
        self,
        project: RegisteredProject,
        request: ListSymbolsRequest,
    ) -> ListSymbolsResult:
        self._fail()

    def find_symbol(
        self,
        project: RegisteredProject,
        request: FindSymbolRequest,
    ) -> FindSymbolResult:
        self._fail()

    def find_definition(
        self,
        project: RegisteredProject,
        request: FindDefinitionRequest,
    ) -> FindDefinitionResult:
        self._fail()

    def find_references(
        self,
        project: RegisteredProject,
        request: FindReferencesRequest,
    ) -> FindReferencesResult:
        self._fail()

    def close_project(self, project_id: str) -> None:
        return None

    def close(self) -> tuple[str, ...]:
        return ()


class FailingSemanticExtension(SemanticExtension):
    def __init__(self) -> None:
        super().__init__(backend_factory=lambda config, registry, runtimes: FailingBackend())


class SemanticConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir()
        projects: list[RegisteredProject] = []
        for project_id, filename in (("alpha", "a.py"), ("beta", "b.py"), ("gamma", "c.py")):
            project_root = self.root / project_id
            project_root.mkdir()
            (project_root / filename).write_text(f"# {project_id}\n", encoding="utf-8")
            projects.append(
                RegisteredProject(
                    project_id=project_id,
                    root=project_root,
                    markers=(),
                    available=True,
                )
            )
        self.alpha, self.beta, self.gamma = projects
        self.projects = tuple(projects)
        self.registry = ProjectRegistry(self.projects)
        self.runtimes = FakeRuntimes(self.projects, self.runtime_root)
        self.factory = FakeWorkerFactory()

    def backend(
        self,
        *,
        max_semantic_projects: int = 2,
        semantic_idle_timeout_seconds: int = 900,
        clock: FakeClock | None = None,
    ) -> SerenaSemanticBackend:
        return SerenaSemanticBackend(
            SemanticConfig(
                max_semantic_projects=max_semantic_projects,
                semantic_idle_timeout_seconds=semantic_idle_timeout_seconds,
            ),
            self.registry,
            self.runtimes,  # type: ignore[arg-type]
            availability=SerenaAvailability(True, SUPPORTED_SERENA_VERSION),
            worker_factory=self.factory,  # type: ignore[arg-type]
            clock=clock or FakeClock(),
        )

    def test_first_request_lazily_creates_only_selected_project_worker(self) -> None:
        backend = self.backend()
        self.addCleanup(backend.close)

        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(self.factory.created_project_ids, ["alpha"])

    def test_second_request_reuses_same_project_worker(self) -> None:
        backend = self.backend()
        self.addCleanup(backend.close)

        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        backend.find_symbol(self.alpha, FindSymbolRequest(query="A"))

        self.assertEqual(self.factory.created_project_ids, ["alpha"])

    def test_idle_timeout_reaps_expired_worker_on_next_bookkeeping_pass(self) -> None:
        clock = FakeClock()
        backend = self.backend(semantic_idle_timeout_seconds=10, clock=clock)
        self.addCleanup(backend.close)
        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        alpha_worker = self.factory.workers["alpha"]

        clock.advance(11)
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))

        self.assertTrue(alpha_worker.closed)
        self.assertEqual(backend.active_project_ids(), ("beta",))

    def test_limit_evicts_lru_idle_worker_before_starting_new_project(self) -> None:
        clock = FakeClock()
        backend = self.backend(max_semantic_projects=2, clock=clock)
        self.addCleanup(backend.close)
        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        clock.advance(1)
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
        alpha_worker = self.factory.workers["alpha"]
        clock.advance(1)

        backend.list_symbols(self.gamma, ListSymbolsRequest(path="c.py"))

        self.assertTrue(alpha_worker.closed)
        self.assertEqual(set(backend.active_project_ids()), {"beta", "gamma"})

    def test_limit_never_evicts_worker_with_in_flight_request(self) -> None:
        backend = self.backend(max_semantic_projects=1)
        self.addCleanup(backend.close)
        self.factory.block_project("alpha")
        errors: list[BaseException] = []
        thread = threading.Thread(
            target=lambda: self._capture(
                errors,
                lambda: backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py")),
            )
        )
        thread.start()
        self.factory.wait_until_in_flight("alpha")

        with self.assertRaises(SemanticBackendError) as raised:
            backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))

        self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_UNAVAILABLE)
        self.assertTrue(raised.exception.retryable)
        self.assertFalse(self.factory.workers["alpha"].closed)
        self.factory.release_project("alpha")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_close_project_only_closes_one_worker(self) -> None:
        backend = self.backend(max_semantic_projects=2)
        self.addCleanup(backend.close)
        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))

        backend.close_project("alpha")

        self.assertTrue(self.factory.workers["alpha"].closed)
        self.assertFalse(self.factory.workers["beta"].closed)
        self.assertEqual(backend.active_project_ids(), ("beta",))

    def test_close_attempts_every_worker_and_returns_bounded_warnings(self) -> None:
        backend = self.backend(max_semantic_projects=2)
        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
        self.factory.workers["alpha"].close_error = RuntimeError("alpha close failed")

        warnings = backend.close()

        self.assertIn("alpha close failed", "\n".join(warnings))
        self.assertTrue(self.factory.workers["beta"].closed)
        self.assertTrue(all(len(item) <= 1024 for item in warnings))

    def test_worker_crash_removes_only_failed_record_and_next_call_restarts(self) -> None:
        backend = self.backend(max_semantic_projects=2)
        self.addCleanup(backend.close)
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
        beta_worker = self.factory.workers["beta"]
        self.factory.crash_projects.add("alpha")

        with self.assertRaises(SemanticBackendError) as raised:
            backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_ERROR)
        self.assertEqual(backend.active_project_ids(), ("beta",))
        self.assertFalse(beta_worker.closed)

        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))

        self.assertEqual(self.factory.created_project_ids.count("alpha"), 2)
        self.assertEqual(set(backend.active_project_ids()), {"alpha", "beta"})

    def test_two_different_projects_can_be_in_flight_simultaneously(self) -> None:
        backend = self.backend(max_semantic_projects=2)
        self.addCleanup(backend.close)
        self.factory.block_project("alpha")
        self.factory.block_project("beta")
        errors: list[BaseException] = []
        threads = [
            threading.Thread(
                target=lambda project=project, path=path: self._capture(
                    errors,
                    lambda: backend.list_symbols(project, ListSymbolsRequest(path=path)),
                )
            )
            for project, path in ((self.alpha, "a.py"), (self.beta, "b.py"))
        ]
        for thread in threads:
            thread.start()

        self.factory.wait_until_in_flight("alpha")
        self.factory.wait_until_in_flight("beta")
        self.assertEqual(self.factory.max_global_in_flight, 2)
        self.factory.release_project("alpha")
        self.factory.release_project("beta")
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_two_same_project_requests_do_not_overlap_worker_request_body(self) -> None:
        backend = self.backend(max_semantic_projects=2)
        self.addCleanup(backend.close)
        self.factory.block_project("alpha")
        errors: list[BaseException] = []
        threads = [
            threading.Thread(
                target=lambda: self._capture(
                    errors,
                    lambda: backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py")),
                )
            )
            for _index in range(2)
        ]
        for thread in threads:
            thread.start()

        self.factory.wait_until_in_flight("alpha")
        time.sleep(0.05)
        self.assertEqual(self.factory.max_in_flight_for("alpha"), 1)
        self.factory.release_project("alpha")
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_semantic_failure_does_not_break_project_filesystem_or_git_tools(self) -> None:
        alpha_root = self.alpha.root
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=alpha_root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=alpha_root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=alpha_root,
            check=True,
        )
        subprocess.run(["git", "add", "a.py"], cwd=alpha_root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=alpha_root, check=True)
        config = RuntimeConfig.defaults(
            enabled=("projects", "semantic"),
            settings={
                "projects": {"registry": {"alpha": {"root": str(alpha_root)}}},
                "semantic": {},
            },
        )
        registry = ExtensionRegistry(
            [ProjectsExtension, FailingSemanticExtension],
            default_enabled=("projects",),
        )
        runtime = Runtime(
            self.root,
            extension_config=config,
            extension_registry=registry,
            permission_mode="dangerous",
            enable_view_image=False,
        )
        try:
            semantic = runtime.call_tool(
                "find_symbol",
                {"project_id": "alpha", "query": "Anything"},
            )
            read = runtime.call_tool(
                "read_file",
                {"project_id": "alpha", "path": "a.py"},
            )
            status = runtime.call_tool("git_status", {"project_id": "alpha"})

            self.assertTrue(semantic["isError"])
            self.assertEqual(
                semantic["structuredContent"]["error"]["code"],
                SEMANTIC_BACKEND_ERROR,
            )
            self.assertFalse(read["isError"])
            self.assertFalse(status["isError"])
        finally:
            runtime.close()

    @staticmethod
    def _capture(errors: list[BaseException], call: object) -> None:
        try:
            assert callable(call)
            call()
        except BaseException as exc:  # noqa: BLE001 - test threads must report failures
            errors.append(exc)


if __name__ == "__main__":
    unittest.main()
