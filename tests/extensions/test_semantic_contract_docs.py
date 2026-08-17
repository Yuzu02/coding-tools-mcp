from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions import ExtensionRegistry, RuntimeConfig
from coding_tools_mcp.extensions.projects import ProjectsExtension
from coding_tools_mcp.extensions.semantic.backend import (
    SEMANTIC_BACKEND_ERROR,
    SEMANTIC_BACKEND_UNAVAILABLE,
    SEMANTIC_FILE_UNSUPPORTED,
    SEMANTIC_LANGUAGE_UNSUPPORTED,
    SEMANTIC_POSITION_INVALID,
    SEMANTIC_PROJECT_START_FAILED,
    SEMANTIC_SYMBOL_NOT_FOUND,
    SEMANTIC_TIMEOUT,
)
from coding_tools_mcp.extensions.semantic.extension import SemanticExtension
from coding_tools_mcp.server import Runtime


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "runtime-contract-v0.4.md"
SEMANTIC_TOOL_NAMES = (
    "list_symbols",
    "find_symbol",
    "find_definition",
    "find_references",
)


class AvailableBackend:
    backend_name = "fake"
    backend_version = "1"
    available = True
    availability_reason = None

    def close(self) -> tuple[str, ...]:
        return ()


class DocumentSemanticExtension(SemanticExtension):
    def __init__(self) -> None:
        super().__init__(backend_factory=lambda config, registry, runtimes: AvailableBackend())  # type: ignore[arg-type]


class SemanticContractDocsTests(unittest.TestCase):
    def runtime(self, root: Path) -> Runtime:
        config = RuntimeConfig.defaults(
            enabled=("projects", "semantic"),
            settings={
                "projects": {"registry": {"alpha": {"root": str(root)}}},
                "semantic": {},
            },
        )
        return Runtime(
            root,
            extension_config=config,
            extension_registry=ExtensionRegistry(
                [ProjectsExtension, DocumentSemanticExtension],
                default_enabled=("projects",),
            ),
            enable_view_image=True,
        )

    def test_v04_contract_matches_live_optional_semantic_tools(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname='fixture'\nversion='0'\n",
                encoding="utf-8",
            )
            runtime = self.runtime(root)
            try:
                tools = {item["name"]: item for item in runtime.list_tools()["tools"]}
            finally:
                runtime.close()

        self.assertEqual(len(tools), 28)
        for name in SEMANTIC_TOOL_NAMES:
            tool = tools[name]
            schema = tool["inputSchema"]
            properties = ",".join(sorted(schema["properties"]))
            required = ",".join(sorted(schema.get("required", [])))
            annotations = tool["annotations"]
            contract_line = (
                f"`{name}` properties=`{properties}` required=`{required}` "
                f"readOnly={str(annotations['readOnlyHint']).lower()} "
                f"destructive={str(annotations['destructiveHint']).lower()} "
                f"idempotent={str(annotations['idempotentHint']).lower()} "
                f"openWorld={str(annotations['openWorldHint']).lower()}"
            )
            with self.subTest(tool=name):
                self.assertIn(contract_line, text)

        for code in (
            SEMANTIC_BACKEND_UNAVAILABLE,
            SEMANTIC_PROJECT_START_FAILED,
            SEMANTIC_LANGUAGE_UNSUPPORTED,
            SEMANTIC_FILE_UNSUPPORTED,
            SEMANTIC_SYMBOL_NOT_FOUND,
            SEMANTIC_POSITION_INVALID,
            SEMANTIC_TIMEOUT,
            SEMANTIC_BACKEND_ERROR,
        ):
            with self.subTest(error=code):
                self.assertIn(code, text)

        for statement in (
            "default projects-only composition: 24 tools",
            "projects + semantic with Serena 1.5.3 available at startup: 28 tools",
            "semantic enabled but Serena unavailable at startup: process starts without semantic tools",
            "runtime semantic worker failure: semantic tools remain in the frozen catalog",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, text)


if __name__ == "__main__":
    unittest.main()
