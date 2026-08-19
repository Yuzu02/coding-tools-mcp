from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions import CORE_CONFIG_SNAPSHOT, RuntimeConfig, builtin_extension_registry
from coding_tools_mcp.extensions.api import ExtensionContext
from coding_tools_mcp.extensions.config import ConfigError
from coding_tools_mcp.extensions.contributions import ContributionRegistry
from coding_tools_mcp.extensions.projects.registry import (
    PROJECT_REGISTRY,
    ProjectRegistry,
    RegisteredProject,
)
from coding_tools_mcp.extensions.projects.runtime import PROJECT_RUNTIMES
from coding_tools_mcp.extensions.semantic import SEMANTIC_BACKEND
from coding_tools_mcp.extensions.semantic.backend import (
    SEMANTIC_BACKEND_ERROR,
    SemanticBackendError,
)
from coding_tools_mcp.extensions.semantic.extension import SemanticExtension
from coding_tools_mcp.extensions.semantic.model import (
    FindDefinitionResult,
    FindImplementationsResult,
    FindReferencesResult,
    FindSymbolResult,
    GetDiagnosticsResult,
    ListSymbolsResult,
    SemanticDiagnostic,
    SemanticPosition,
    SemanticRange,
    SemanticReference,
    SemanticSymbol,
)
from coding_tools_mcp.extensions.services import ServiceRegistry
from coding_tools_mcp.host_config import build_developer_snapshot


@dataclass(frozen=True)
class FakeResolvedPath:
    display: str
    path: Path


class FakeProjectRuntimes:
    def __init__(self, registry: ProjectRegistry) -> None:
        self.registry = registry
        self.resolve_calls: list[tuple[str, str]] = []

    def resolve_existing(self, project_id: str, raw_path: str = ".") -> FakeResolvedPath:
        self.resolve_calls.append((project_id, raw_path))
        project = self.registry.require_available(project_id)
        candidate = (project.root / (raw_path or ".")).resolve(strict=True)
        try:
            relative = candidate.relative_to(project.root)
        except ValueError as exc:
            raise ToolFailure(
                "PATH_OUTSIDE_WORKSPACE",
                "Path escapes the configured project.",
                category="security",
            ) from exc
        display = "." if relative == Path(".") else relative.as_posix()
        return FakeResolvedPath(display=display, path=candidate)


class FakeBackend:
    backend_name = "fake"
    backend_version = "1"

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.availability_reason = None if available else "fake backend unavailable"
        self.calls: list[tuple[str, str, object]] = []
        self.closed = False
        self.fail_next: SemanticBackendError | None = None

    def _maybe_fail(self) -> None:
        if self.fail_next is None:
            return
        failure = self.fail_next
        self.fail_next = None
        raise failure

    def list_symbols(self, project, request):
        self._maybe_fail()
        self.calls.append(("list_symbols", project.project_id, request))
        return ListSymbolsResult(
            (
                SemanticSymbol(
                    name="Greeter",
                    name_path="Greeter",
                    kind="class",
                    path=request.path,
                    range=SemanticRange(SemanticPosition(1, 1), SemanticPosition(3, 1)),
                ),
            )
        )

    def find_symbol(self, project, request):
        self._maybe_fail()
        self.calls.append(("find_symbol", project.project_id, request))
        return FindSymbolResult(
            (
                SemanticSymbol.summary(
                    name=request.query.rsplit("/", 1)[-1],
                    name_path=request.query,
                    kind="method",
                    path=request.path or "sample.py",
                ),
            )
        )

    def find_definition(self, project, request):
        self._maybe_fail()
        self.calls.append(("find_definition", project.project_id, request))
        return FindDefinitionResult(
            (
                SemanticSymbol(
                    name="target",
                    name_path="target",
                    kind="function",
                    path=request.path,
                    range=SemanticRange(SemanticPosition(2, 1), SemanticPosition(2, 10)),
                ),
            )
        )

    def find_references(self, project, request):
        self._maybe_fail()
        self.calls.append(("find_references", project.project_id, request))
        return FindReferencesResult(
            (
                SemanticReference(
                    path=request.path,
                    range=SemanticRange(SemanticPosition(4, 3), SemanticPosition(4, 4)),
                ),
            )
        )

    def find_implementations(self, project, request):
        self._maybe_fail()
        self.calls.append(("find_implementations", project.project_id, request))
        return FindImplementationsResult(
            (
                SemanticSymbol(
                    name="Impl",
                    name_path="Impl",
                    kind="class",
                    path=request.path,
                    range=SemanticRange(SemanticPosition(5, 1), SemanticPosition(7, 1)),
                ),
            )
        )

    def get_diagnostics(self, project, request):
        self._maybe_fail()
        self.calls.append(("get_diagnostics", project.project_id, request))
        return GetDiagnosticsResult(
            (
                SemanticDiagnostic(
                    path=request.path,
                    range=SemanticRange(SemanticPosition(2, 1), SemanticPosition(2, 5)),
                    severity="warning",
                    message="example diagnostic",
                    code="W001",
                    source="fake",
                ),
            )
        )

    def close_project(self, project_id: str) -> None:
        self.calls.append(("close_project", project_id, None))

    def close(self) -> tuple[str, ...]:
        self.closed = True
        return ()


class SemanticExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "sample.py").write_text("def target():\n    pass\n", encoding="utf-8")
        self.registry = ProjectRegistry(
            (
                RegisteredProject(
                    project_id="alpha",
                    root=self.root,
                    markers=(),
                    available=True,
                ),
            )
        )
        self.runtimes = FakeProjectRuntimes(self.registry)

    def build_extension(self, backend: FakeBackend) -> tuple[SemanticExtension, ServiceRegistry, ContributionRegistry]:
        services = ServiceRegistry()
        services.provide(PROJECT_REGISTRY, self.registry)
        services.provide(PROJECT_RUNTIMES, self.runtimes)  # type: ignore[arg-type]
        snapshot = build_developer_snapshot(
            runtime_config=RuntimeConfig.defaults(
                enabled=("projects", "semantic"),
                settings={
                    "projects": {
                        "registry": {
                            "alpha": {"root": str(self.root)},
                        }
                    }
                },
            ),
            bootstrap_workspace=self.root,
        )
        services.provide(CORE_CONFIG_SNAPSHOT, snapshot)
        contributions = ContributionRegistry()
        extension = SemanticExtension(
            backend_factory=lambda config, registry, runtimes: backend,
        )
        extension.configure({})
        extension.register(
            ExtensionContext(
                services=services,
                contributions=contributions,
                extension_name="semantic",
            )
        )
        return extension, services, contributions

    def test_semantic_manifest_requires_projects(self) -> None:
        self.assertEqual(SemanticExtension.manifest.requires, ("projects",))

    def test_builtin_registry_knows_semantic_but_does_not_enable_it_by_default(self) -> None:
        registry = builtin_extension_registry()

        self.assertEqual(registry.default_enabled, ("projects",))
        self.assertIs(registry.extension_type("semantic"), SemanticExtension)

    def test_semantic_config_rejects_unknown_backend(self) -> None:
        extension = SemanticExtension(
            backend_factory=lambda config, registry, runtimes: FakeBackend(),
        )

        with self.assertRaisesRegex(ConfigError, "extensions.semantic.backend"):
            extension.configure({"backend": "unknown"})

    def test_semantic_config_rejects_out_of_range_limits(self) -> None:
        for key, value in (
            ("max_semantic_projects", 0),
            ("semantic_idle_timeout_seconds", 0),
            ("semantic_start_timeout_seconds", 601),
            ("semantic_request_timeout_seconds", 601),
        ):
            with self.subTest(key=key):
                extension = SemanticExtension(
                    backend_factory=lambda config, registry, runtimes: FakeBackend(),
                )
                with self.assertRaisesRegex(ConfigError, key):
                    extension.configure({key: value})

    def test_unavailable_backend_contributes_no_tools_and_bounded_metadata(self) -> None:
        backend = FakeBackend(available=False)
        _extension, services, contributions = self.build_extension(backend)

        self.assertEqual(contributions.tool_entries(), ())
        self.assertIs(services.require(SEMANTIC_BACKEND), backend)
        self.assertEqual(
            contributions.metadata_snapshot()["semantic"],
            {
                "backend": "fake",
                "backend_version": "1",
                "available": False,
                "reason": "fake backend unavailable",
            },
        )

    def test_available_backend_contributes_six_read_only_idempotent_tools(self) -> None:
        backend = FakeBackend()
        _extension, services, contributions = self.build_extension(backend)
        tools = {tool.name: tool for _owner, tool in contributions.tool_entries()}

        self.assertIs(services.require(SEMANTIC_BACKEND), backend)
        self.assertEqual(
            set(tools),
            {
                "list_symbols",
                "find_symbol",
                "find_definition",
                "find_references",
                "find_implementations",
                "get_diagnostics",
            },
        )
        for tool in tools.values():
            self.assertTrue(tool.annotations.read_only)
            self.assertTrue(tool.annotations.idempotent)
            self.assertFalse(tool.annotations.destructive)
            self.assertIn("project_id", tool.input_schema["required"])

    def test_tool_schemas_are_coding_tools_owned(self) -> None:
        backend = FakeBackend()
        _extension, _services, contributions = self.build_extension(backend)
        tools = {tool.name: tool for _owner, tool in contributions.tool_entries()}

        self.assertEqual(tools["list_symbols"].input_schema["required"], ["project_id", "path"])
        self.assertEqual(tools["find_symbol"].input_schema["required"], ["project_id", "query"])
        self.assertEqual(
            tools["find_definition"].input_schema["required"],
            ["project_id", "path", "line", "column"],
        )
        self.assertEqual(
            tools["find_references"].input_schema["required"],
            ["project_id", "path", "line", "column"],
        )
        self.assertEqual(
            tools["find_implementations"].input_schema["required"],
            ["project_id", "path", "line", "column"],
        )
        self.assertEqual(
            tools["get_diagnostics"].input_schema["required"],
            ["project_id", "path"],
        )
        self.assertNotIn("name_path_pattern", tools["find_symbol"].input_schema["properties"])

    def test_handlers_pass_canonical_project_relative_paths_to_backend(self) -> None:
        backend = FakeBackend()
        _extension, _services, contributions = self.build_extension(backend)
        tools = {tool.name: tool for _owner, tool in contributions.tool_entries()}

        listed = tools["list_symbols"].handler(
            {"project_id": "alpha", "path": "src/../src/sample.py"}
        )
        definition = tools["find_definition"].handler(
            {
                "project_id": "alpha",
                "path": "src/../src/sample.py",
                "line": 2,
                "column": 1,
            }
        )
        implementations = tools["find_implementations"].handler(
            {
                "project_id": "alpha",
                "path": "src/../src/sample.py",
                "line": 2,
                "column": 1,
            }
        )
        diagnostics = tools["get_diagnostics"].handler(
            {"project_id": "alpha", "path": "src/../src/sample.py"}
        )

        self.assertEqual(listed["symbols"][0]["path"], "src/sample.py")
        self.assertEqual(definition["definitions"][0]["path"], "src/sample.py")
        self.assertEqual(implementations["implementations"][0]["path"], "src/sample.py")
        self.assertEqual(diagnostics["diagnostics"][0]["path"], "src/sample.py")
        self.assertEqual(diagnostics["diagnostics"][0]["severity"], "warning")
        self.assertEqual(backend.calls[0][2].path, "src/sample.py")
        self.assertEqual(backend.calls[1][2].path, "src/sample.py")

    def test_project_capability_reduction_blocks_only_selected_project_before_backend(self) -> None:
        blocked = self.root / "blocked"
        allowed = self.root / "allowed"
        blocked.mkdir()
        allowed.mkdir()
        (blocked / ".coding-tools-mcp.toml").write_text(
            "\n".join(
                (
                    "project_config_version = 1",
                    "[capabilities]",
                    'disabled = ["semantic"]',
                    "",
                )
            ),
            encoding="utf-8",
        )
        registry = ProjectRegistry(
            (
                RegisteredProject("blocked", blocked, (), True),
                RegisteredProject("allowed", allowed, (), True),
            )
        )
        runtimes = FakeProjectRuntimes(registry)
        runtime_config = RuntimeConfig.defaults(
            enabled=("projects", "semantic"),
            settings={
                "projects": {
                    "registry": {
                        "blocked": {"root": str(blocked)},
                        "allowed": {"root": str(allowed)},
                    }
                }
            },
        )
        snapshot = build_developer_snapshot(
            runtime_config=runtime_config,
            bootstrap_workspace=self.root,
        )
        backend = FakeBackend()
        services = ServiceRegistry()
        services.provide(PROJECT_REGISTRY, registry)
        services.provide(PROJECT_RUNTIMES, runtimes)  # type: ignore[arg-type]
        services.provide(CORE_CONFIG_SNAPSHOT, snapshot)
        contributions = ContributionRegistry()
        extension = SemanticExtension(backend_factory=lambda config, registry, runtimes: backend)
        extension.configure({})
        extension.register(
            ExtensionContext(
                services=services,
                contributions=contributions,
                extension_name="semantic",
            )
        )
        tools = {tool.name: tool for _owner, tool in contributions.tool_entries()}

        self.assertEqual(
            set(tools),
            {
                "list_symbols",
                "find_symbol",
                "find_definition",
                "find_references",
                "find_implementations",
                "get_diagnostics",
            },
        )
        with self.assertRaises(ToolFailure) as raised:
            tools["find_symbol"].handler({"project_id": "blocked", "query": "target"})
        self.assertEqual(raised.exception.code, "PROJECT_CAPABILITY_DISABLED")
        self.assertEqual(raised.exception.category, "permission")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(
            raised.exception.details,
            {"project_id": "blocked", "capability": "semantic"},
        )
        self.assertEqual(backend.calls, [])

        payload = tools["find_symbol"].handler({"project_id": "allowed", "query": "target"})
        self.assertEqual(payload["project_id"], "allowed")
        self.assertEqual([call[:2] for call in backend.calls], [("find_symbol", "allowed")])

    def test_find_symbol_empty_path_does_not_require_a_filesystem_lookup(self) -> None:
        backend = FakeBackend()
        _extension, _services, contributions = self.build_extension(backend)
        tool = {tool.name: tool for _owner, tool in contributions.tool_entries()}["find_symbol"]

        payload = tool.handler({"project_id": "alpha", "query": "Greeter/hello"})

        self.assertEqual(payload["symbols"][0]["name_path"], "Greeter/hello")
        self.assertEqual(self.runtimes.resolve_calls, [])

    def test_backend_error_maps_to_semantic_tool_failure_without_unsafe_details(self) -> None:
        backend = FakeBackend()
        backend.fail_next = SemanticBackendError(
            SEMANTIC_BACKEND_ERROR,
            "backend failed",
            retryable=True,
            details={
                "language": "python",
                "operation": "find_symbol",
                "absolute_path": "/private/root/file.py",
                "traceback": "secret traceback",
            },
        )
        _extension, _services, contributions = self.build_extension(backend)
        tool = {tool.name: tool for _owner, tool in contributions.tool_entries()}["find_symbol"]

        with self.assertRaises(ToolFailure) as raised:
            tool.handler({"project_id": "alpha", "query": "target"})

        self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_ERROR)
        self.assertEqual(raised.exception.category, "semantic")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.details["language"], "python")
        self.assertEqual(raised.exception.details["operation"], "find_symbol")
        self.assertNotIn("absolute_path", raised.exception.details)
        self.assertNotIn("traceback", raised.exception.details)
        self.assertIn("search_text", raised.exception.details["retry_hint"])

    def test_stop_closes_backend(self) -> None:
        backend = FakeBackend()
        extension, _services, _contributions = self.build_extension(backend)

        extension.stop()

        self.assertTrue(backend.closed)


if __name__ == "__main__":
    unittest.main()
