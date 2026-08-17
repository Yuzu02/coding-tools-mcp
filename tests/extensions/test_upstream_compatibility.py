from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = (ROOT / "coding_tools_mcp" / "server.py").read_text(encoding="utf-8")
TOOL_RESULTS = (ROOT / "coding_tools_mcp" / "tool_results.py").read_text(encoding="utf-8")


class UpstreamBridgeCompatibilityTests(unittest.TestCase):
    def test_mother_core_does_not_import_extension_private_packages(self) -> None:
        self.assertNotIn("extensions.projects", SERVER)
        self.assertNotIn("extensions.semantic", SERVER)

    def test_projects_skill_tools_are_not_authored_in_core_tool_registry(self) -> None:
        self.assertNotRegex(SERVER, r'\n\s+"list_skills": ToolSpec\(')
        self.assertNotRegex(SERVER, r'\n\s+"read_skill": ToolSpec\(')

    def test_projects_skill_renderers_are_not_authored_in_core_tool_results(self) -> None:
        self.assertNotIn("def _render_list_skills", TOOL_RESULTS)
        self.assertNotIn("def _render_read_skill", TOOL_RESULTS)
        self.assertNotRegex(TOOL_RESULTS, r'"list_skills"\s*:\s*_render_list_skills')
        self.assertNotRegex(TOOL_RESULTS, r'"read_skill"\s*:\s*_render_read_skill')

    def test_core_registry_and_core_schema_names_match_before_composition(self) -> None:
        from coding_tools_mcp.server import TOOL_REGISTRY, input_schemas

        self.assertEqual(set(TOOL_REGISTRY), set(input_schemas()))

    def test_default_and_disabled_catalogs_have_expected_bridge_delta(self) -> None:
        from coding_tools_mcp.extensions import RuntimeConfig, builtin_extension_registry
        from coding_tools_mcp.server import TOOL_REGISTRY, Runtime

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            registry = builtin_extension_registry()

            default_runtime = Runtime(root, extension_registry=registry)
            try:
                self.assertEqual(
                    set(default_runtime.exposed_tool_names()),
                    set(TOOL_REGISTRY) | {"list_skills", "read_skill"},
                )
            finally:
                default_runtime.close()

            disabled_runtime = Runtime(
                root,
                extension_registry=registry,
                extension_config=RuntimeConfig.defaults(enabled=()),
            )
            try:
                self.assertEqual(set(disabled_runtime.exposed_tool_names()), set(TOOL_REGISTRY))
            finally:
                disabled_runtime.close()


if __name__ == "__main__":
    unittest.main()
