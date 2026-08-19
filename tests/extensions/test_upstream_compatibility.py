from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = (ROOT / "coding_tools_mcp" / "server.py").read_text(encoding="utf-8")
TOOL_RESULTS = (ROOT / "coding_tools_mcp" / "tool_results.py").read_text(encoding="utf-8")
EXTENSION_HOST = (ROOT / "coding_tools_mcp" / "extensions" / "host.py").read_text(encoding="utf-8")


class UpstreamBridgeCompatibilityTests(unittest.TestCase):
    def test_mother_core_does_not_import_extension_private_packages(self) -> None:
        for private_package in (
            "extensions.projects",
            "extensions.semantic",
            "extensions.work",
            "extensions.gateway",
        ):
            with self.subTest(private_package=private_package):
                self.assertNotIn(private_package, SERVER)

    def test_mother_core_uses_the_generic_composed_tool_bridge(self) -> None:
        self.assertIn("def core_tool_contracts(", SERVER)
        self.assertIn("compose_tools(core_tools, contributions, order)", EXTENSION_HOST)

    def test_projects_tools_are_not_authored_in_core_tool_registry(self) -> None:
        for name in ("list_projects", "resolve_project", "list_skills", "read_skill"):
            with self.subTest(name=name):
                self.assertNotRegex(SERVER, rf'\n\s+"{name}": ToolSpec\(')

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
                    set(TOOL_REGISTRY)
                    | {
                        "list_projects",
                        "resolve_project",
                        "list_skills",
                        "read_skill",
                        "project_context",
                        "doctor",
                    },
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
