from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from coding_tools_mcp.extensions import ExtensionRegistry, RuntimeConfig
from coding_tools_mcp.extensions.projects import ProjectsExtension
from coding_tools_mcp.extensions.projects.registry import RegisteredProject
from coding_tools_mcp.extensions.semantic.backend import (
    SemanticBackendError,
)
from coding_tools_mcp.extensions.semantic.extension import SemanticExtension
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
from coding_tools_mcp.extensions.semantic.serena import detect_serena
from coding_tools_mcp.server import Runtime
from tests.compliance.mcp_client import MCPClient, StdioMCPClient


SERENA_AVAILABLE = detect_serena().available
SEMANTIC_TOOLS = {
    "list_symbols",
    "find_symbol",
    "find_definition",
    "find_references",
}


class UnavailableBackend:
    backend_name = "serena"
    backend_version = None
    available = False
    availability_reason = "serena-agent is not installed"

    @staticmethod
    def _unavailable() -> None:
        raise SemanticBackendError(
            "SEMANTIC_BACKEND_UNAVAILABLE",
            "serena-agent is not installed",
            retryable=False,
        )

    def list_symbols(
        self,
        project: RegisteredProject,
        request: ListSymbolsRequest,
    ) -> ListSymbolsResult:
        self._unavailable()

    def find_symbol(
        self,
        project: RegisteredProject,
        request: FindSymbolRequest,
    ) -> FindSymbolResult:
        self._unavailable()

    def find_definition(
        self,
        project: RegisteredProject,
        request: FindDefinitionRequest,
    ) -> FindDefinitionResult:
        self._unavailable()

    def find_references(
        self,
        project: RegisteredProject,
        request: FindReferencesRequest,
    ) -> FindReferencesResult:
        self._unavailable()

    def close_project(self, project_id: str) -> None:
        return None

    def close(self) -> tuple[str, ...]:
        return ()


class UnavailableSemanticExtension(SemanticExtension):
    def __init__(self) -> None:
        super().__init__(
            backend_factory=lambda config, registry, runtimes: UnavailableBackend(),
        )


class SemanticUnavailableStartupTests(unittest.TestCase):
    def test_enabled_unavailable_backend_starts_without_semantic_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bootstrap = root / "bootstrap"
            project = root / "alpha"
            bootstrap.mkdir()
            project.mkdir()
            (bootstrap / "pyproject.toml").write_text(
                "[project]\nname='bootstrap'\nversion='0'\n",
                encoding="utf-8",
            )
            (project / "pyproject.toml").write_text(
                "[project]\nname='alpha'\nversion='0'\n",
                encoding="utf-8",
            )
            config = RuntimeConfig.defaults(
                enabled=("projects", "semantic"),
                settings={
                    "projects": {"registry": {"alpha": {"root": str(project)}}},
                    "semantic": {},
                },
            )
            registry = ExtensionRegistry(
                [ProjectsExtension, UnavailableSemanticExtension],
                default_enabled=("projects",),
            )
            runtime = Runtime(
                bootstrap,
                extension_config=config,
                extension_registry=registry,
                enable_view_image=True,
            )
            try:
                tools = {tool["name"] for tool in runtime.list_tools()["tools"]}
                metadata = runtime.server_info_payload()["extensions"]

                self.assertEqual(len(tools), 24)
                self.assertTrue(SEMANTIC_TOOLS.isdisjoint(tools))
                self.assertFalse(metadata["metadata"]["semantic"]["available"])
                self.assertEqual(
                    metadata["metadata"]["semantic"]["reason"],
                    "serena-agent is not installed",
                )
            finally:
                runtime.close()


@unittest.skipUnless(
    SERENA_AVAILABLE,
    "serena-agent==1.5.3 semantic extra is not installed",
)
class SemanticMCPIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.mkdir()
        (self.bootstrap / "pyproject.toml").write_text(
            "[project]\nname='bootstrap'\nversion='0'\n",
            encoding="utf-8",
        )
        self.alpha = self._project("alpha", "alpha_use")
        self.beta = self._project("beta", "beta_use")
        self.config_path = self.root / "semantic.toml"
        self.config_path.write_text(
            "\n".join(
                [
                    "config_version = 1",
                    "",
                    "[extensions]",
                    'enabled = ["projects", "semantic"]',
                    "",
                    "[extensions.projects]",
                    "",
                    "[extensions.projects.registry.alpha]",
                    f'root = "{self.alpha.as_posix()}"',
                    "",
                    "[extensions.projects.registry.beta]",
                    f'root = "{self.beta.as_posix()}"',
                    "",
                    "[extensions.semantic]",
                    'backend = "serena"',
                    "max_semantic_projects = 2",
                    "semantic_idle_timeout_seconds = 900",
                    "semantic_start_timeout_seconds = 60",
                    "semantic_request_timeout_seconds = 60",
                    "allow_dependency_install = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.extra_args = ["--config", str(self.config_path)]

    def _project(self, project_id: str, caller: str) -> Path:
        project = self.root / project_id
        project.mkdir()
        (project / "pyproject.toml").write_text(
            f"[project]\nname='{project_id}'\nversion='0'\n",
            encoding="utf-8",
        )
        (project / f"{project_id}.py").write_text(
            "def SharedName(value: int) -> int:\n"
            "    return value + 1\n\n\n"
            f"def {caller}() -> int:\n"
            "    return SharedName(1)\n",
            encoding="utf-8",
        )
        return project

    def test_stdio_semantic_catalog_has_28_tools(self) -> None:
        with StdioMCPClient(self.bootstrap, extra_args=self.extra_args) as client:
            tools = client.rpc("tools/list")["tools"]
            names = {tool["name"] for tool in tools}
            self.assertEqual(len(tools), 28)
            self.assertTrue(SEMANTIC_TOOLS <= names)

    def test_semantic_schemas_are_coding_tools_owned(self) -> None:
        with StdioMCPClient(self.bootstrap, extra_args=self.extra_args) as client:
            tools = {tool["name"]: tool for tool in client.rpc("tools/list")["tools"]}
            for name in SEMANTIC_TOOLS:
                with self.subTest(name=name):
                    schema = tools[name]["inputSchema"]
                    self.assertIn("project_id", schema["required"])
                    self.assertNotIn("name_path_pattern", schema.get("properties", {}))

    def test_two_projects_with_same_symbol_do_not_cross_contaminate(self) -> None:
        with MCPClient(self.bootstrap, extra_args=self.extra_args) as owner:
            def find(project_id: str) -> dict[str, object]:
                with MCPClient(self.bootstrap, url=owner.url) as client:
                    return client.call_tool(
                        "find_symbol",
                        {"project_id": project_id, "query": "SharedName"},
                    )["structuredContent"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                alpha, beta = pool.map(find, ("alpha", "beta"))

            self.assertEqual({item["path"] for item in alpha["symbols"]}, {"alpha.py"})
            self.assertEqual({item["path"] for item in beta["symbols"]}, {"beta.py"})

    def test_definition_and_references_remain_project_stateless(self) -> None:
        with MCPClient(self.bootstrap, extra_args=self.extra_args) as owner:
            position = {"project_id": "alpha", "path": "alpha.py", "line": 6, "column": 12}

            def alpha_definition() -> dict[str, object]:
                with MCPClient(self.bootstrap, url=owner.url) as client:
                    return client.call_tool("find_definition", position)["structuredContent"]

            def beta_symbol() -> dict[str, object]:
                with MCPClient(self.bootstrap, url=owner.url) as client:
                    return client.call_tool(
                        "find_symbol",
                        {"project_id": "beta", "query": "SharedName"},
                    )["structuredContent"]

            with ThreadPoolExecutor(max_workers=2) as pool:
                definition_future = pool.submit(alpha_definition)
                beta_future = pool.submit(beta_symbol)
                definition = definition_future.result()
                self.assertTrue(beta_future.result()["symbols"])

            with MCPClient(self.bootstrap, url=owner.url) as client:
                references = client.call_tool("find_references", position)["structuredContent"]

            self.assertTrue(definition["definitions"])
            self.assertTrue(references["references"])
            self.assertTrue(all(item["path"] == "alpha.py" for item in references["references"]))

    def test_semantic_tool_error_does_not_mutate_catalog_or_break_filesystem(self) -> None:
        with StdioMCPClient(self.bootstrap, extra_args=self.extra_args) as client:
            before = {tool["name"] for tool in client.rpc("tools/list")["tools"]}
            bad = client.call_tool(
                "find_definition",
                {"project_id": "alpha", "path": "missing.py", "line": 1, "column": 1},
            )
            read = client.call_tool(
                "read_file",
                {"project_id": "alpha", "path": "alpha.py"},
            )
            after = {tool["name"] for tool in client.rpc("tools/list")["tools"]}

            self.assertTrue(bad["isError"])
            self.assertEqual(before, after)
            self.assertFalse(read["isError"])


if __name__ == "__main__":
    unittest.main()
