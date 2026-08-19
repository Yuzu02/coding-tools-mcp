from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from coding_tools_mcp.extensions.projects.registry import ProjectRegistry, RegisteredProject
from coding_tools_mcp.extensions.semantic.extension import SemanticConfig
from coding_tools_mcp.extensions.semantic.model import (
    FindDefinitionRequest,
    FindImplementationsRequest,
    FindReferencesRequest,
    FindSymbolRequest,
    GetDiagnosticsRequest,
    ListSymbolsRequest,
)
from coding_tools_mcp.extensions.semantic.serena import SerenaSemanticBackend, detect_serena


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "semantic"
SERENA_AVAILABLE = detect_serena().available


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
    def __init__(self, runtime_dirs: dict[str, tuple[Path, Path]]) -> None:
        self.runtime_dirs = runtime_dirs

    def require(self, project_id: str) -> FakeProjectRuntime:
        root, runtime_dir = self.runtime_dirs[project_id]
        return FakeProjectRuntime(FakeWorkspaceHandle(root=root, runtime_dir=runtime_dir))


@unittest.skipUnless(
    SERENA_AVAILABLE,
    "serena-agent==1.5.3 semantic extra is not installed",
)
class SerenaWorkerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state_root = self.root / "runtime-state"
        self.state_root.mkdir()

    def backend_for_fixture(
        self,
        language: str,
    ) -> tuple[RegisteredProject, SerenaSemanticBackend]:
        project_root = self.root / f"project-{language}"
        shutil.copytree(FIXTURES / language, project_root)
        project = RegisteredProject(
            project_id=language,
            root=project_root,
            markers=(),
            available=True,
        )
        registry = ProjectRegistry((project,))
        runtimes = FakeRuntimes(
            {project.project_id: (project.root, self.state_root / project.project_id)}
        )
        backend = SerenaSemanticBackend(
            SemanticConfig(
                semantic_start_timeout_seconds=60,
                semantic_request_timeout_seconds=60,
                allow_dependency_install=True,
            ),
            registry,
            runtimes,  # type: ignore[arg-type]
        )
        return project, backend

    def backend_for_nested_projects(
        self,
    ) -> tuple[RegisteredProject, RegisteredProject, SerenaSemanticBackend]:
        parent_root = self.root / "parent"
        child_root = parent_root / "packages" / "child"
        child_root.mkdir(parents=True)
        (parent_root / "parent.py").write_text(
            "def ParentOnlySymbol():\n    return 1\n",
            encoding="utf-8",
        )
        (child_root / "child.py").write_text(
            "def ChildOnlySymbol():\n    return 2\n",
            encoding="utf-8",
        )
        parent = RegisteredProject(
            project_id="parent",
            root=parent_root,
            markers=(),
            available=True,
        )
        child = RegisteredProject(
            project_id="child",
            root=child_root,
            markers=(),
            available=True,
        )
        registry = ProjectRegistry((parent, child))
        runtimes = FakeRuntimes(
            {
                "parent": (parent.root, self.state_root / "parent"),
                "child": (child.root, self.state_root / "child"),
            }
        )
        backend = SerenaSemanticBackend(
            SemanticConfig(
                semantic_start_timeout_seconds=60,
                semantic_request_timeout_seconds=60,
                allow_dependency_install=True,
            ),
            registry,
            runtimes,  # type: ignore[arg-type]
        )
        return parent, child, backend

    def test_python_list_symbols_and_find_symbol_are_normalized(self) -> None:
        project, backend = self.backend_for_fixture("python")
        self.addCleanup(backend.close)

        listed = backend.list_symbols(
            project,
            ListSymbolsRequest(path="sample.py", depth=1),
        )
        found = backend.find_symbol(
            project,
            FindSymbolRequest(query="Greeter/hello", path="sample.py"),
        )

        self.assertIn("Greeter", [symbol.name_path for symbol in listed.symbols])
        self.assertEqual([symbol.name_path for symbol in found.symbols], ["Greeter/hello"])
        self.assertEqual(found.symbols[0].path, "sample.py")

    def test_python_definition_and_references_use_one_based_public_positions(self) -> None:
        project, backend = self.backend_for_fixture("python")
        self.addCleanup(backend.close)

        definition = backend.find_definition(
            project,
            FindDefinitionRequest(path="sample.py", line=11, column=12),
        )
        self.assertTrue(definition.definitions)
        symbol = definition.definitions[0]
        self.assertIsNotNone(symbol.range)
        assert symbol.range is not None
        self.assertGreaterEqual(symbol.range.start.line, 1)
        self.assertGreaterEqual(symbol.range.start.column, 1)

        references = backend.find_references(
            project,
            FindReferencesRequest(path="sample.py", line=11, column=12),
        )

        self.assertTrue(references.references)
        self.assertTrue(all(item.range.start.line >= 1 for item in references.references))
        self.assertTrue(all(item.range.start.column >= 1 for item in references.references))

    def test_typescript_list_symbols_and_find_symbol_are_normalized(self) -> None:
        project, backend = self.backend_for_fixture("typescript")
        self.addCleanup(backend.close)

        listed = backend.list_symbols(
            project,
            ListSymbolsRequest(path="sample.ts", depth=1),
        )
        found = backend.find_symbol(
            project,
            FindSymbolRequest(query="Greeter/hello", path="sample.ts"),
        )

        self.assertIn("Greeter", [symbol.name_path for symbol in listed.symbols])
        self.assertEqual([symbol.name_path for symbol in found.symbols], ["Greeter/hello"])

    def test_typescript_find_implementations_uses_real_serena_lsp(self) -> None:
        project, backend = self.backend_for_fixture("typescript")
        self.addCleanup(backend.close)

        result = backend.find_implementations(
            project,
            FindImplementationsRequest(path="sample.ts", line=15, column=18),
        )

        self.assertIn("LoudSpeaker", [symbol.name_path for symbol in result.implementations])
        self.assertTrue(all(symbol.path == "sample.ts" for symbol in result.implementations))
        self.assertTrue(
            all(
                symbol.range is None or symbol.range.start.line >= 1
                for symbol in result.implementations
            )
        )

    def test_typescript_get_diagnostics_uses_real_serena_lsp_and_closed_severity(self) -> None:
        project, backend = self.backend_for_fixture("typescript")
        self.addCleanup(backend.close)

        result = backend.get_diagnostics(
            project,
            GetDiagnosticsRequest(path="diagnostics.ts", min_severity="hint"),
        )

        self.assertTrue(result.diagnostics, result)
        self.assertTrue(all(item.path == "diagnostics.ts" for item in result.diagnostics))
        self.assertTrue(
            all(
                item.severity in {"error", "warning", "information", "hint"}
                for item in result.diagnostics
            )
        )
        self.assertTrue(
            any("str" in item.message.lower() or "int" in item.message.lower() for item in result.diagnostics)
        )

    def test_parent_project_ignores_registered_nested_child_sources(self) -> None:
        parent, child, backend = self.backend_for_nested_projects()
        self.addCleanup(backend.close)

        found = backend.find_symbol(
            parent,
            FindSymbolRequest(query="ChildOnlySymbol"),
        )
        child_found = backend.find_symbol(
            child,
            FindSymbolRequest(query="ChildOnlySymbol"),
        )

        self.assertEqual(found.symbols, ())
        self.assertEqual(
            [symbol.name_path for symbol in child_found.symbols],
            ["ChildOnlySymbol"],
        )

    def test_worker_does_not_create_dot_serena_inside_fixture_project(self) -> None:
        project, backend = self.backend_for_fixture("python")
        self.addCleanup(backend.close)

        backend.list_symbols(project, ListSymbolsRequest(path="sample.py"))

        self.assertFalse((project.root / ".serena").exists())


if __name__ == "__main__":
    unittest.main()
