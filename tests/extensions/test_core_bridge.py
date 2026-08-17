from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.extensions import (
    ExtensionManifest,
    ExtensionRegistry,
    RuntimeConfig,
    ToolAnnotations,
    ToolContribution,
)
from coding_tools_mcp.server import Runtime


class EchoExtension:
    manifest = ExtensionManifest(name="echo")
    stops: list[str] = []

    def configure(self, config):
        self.config = config

    def register(self, context):
        context.add_tool(
            ToolContribution(
                name="extension_echo",
                title="Extension echo",
                description="Echo extension arguments.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=lambda args: {"value": args["value"]},
                annotations=ToolAnnotations(read_only=True, idempotent=True),
                text_renderer=lambda payload: f"echo:{payload['value']}",
            )
        )

    def start(self):
        pass

    def stop(self):
        self.stops.append("echo")


class CoreBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='fixture'\nversion='0'\n",
            encoding="utf-8",
        )
        self.registry = ExtensionRegistry([EchoExtension], default_enabled=())
        EchoExtension.stops.clear()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runtime(self, enabled: tuple[str, ...]) -> Runtime:
        return Runtime(
            self.root,
            extension_registry=self.registry,
            extension_config=RuntimeConfig.defaults(enabled=enabled),
        )

    def test_tools_list_contains_extension_contribution(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            self.assertIn("extension_echo", tools)
            self.assertTrue(tools["extension_echo"]["annotations"]["readOnlyHint"])
            self.assertEqual(tools["extension_echo"]["inputSchema"]["required"], ["value"])
        finally:
            runtime.close()

    def test_call_tool_dispatches_extension_handler_and_renderer(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            result = runtime.call_tool("extension_echo", {"value": "hello"})
            self.assertEqual(result["structuredContent"]["value"], "hello")
            self.assertFalse(result["isError"])
            text = "\n".join(
                str(item.get("text", ""))
                for item in result["content"]
                if item.get("type") == "text"
            )
            self.assertEqual(text, "echo:hello")
        finally:
            runtime.close()

    def test_extension_schema_is_enforced_by_existing_jsonrpc_validation_path(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            with self.assertRaisesRegex(JsonRpcError, "arguments.value is required"):
                runtime.call_tool("extension_echo", {})
        finally:
            runtime.close()

    def test_server_info_reports_bounded_extension_metadata(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            metadata = runtime.server_info_payload()["extensions"]
            self.assertEqual(metadata["enabled"], ["echo"])
            self.assertIn("extension_echo", metadata["contributions"]["tools"])
            self.assertNotIn("extension_settings", metadata)
        finally:
            runtime.close()

    def test_disabled_extension_contributes_nothing(self) -> None:
        runtime = self.runtime(())
        try:
            self.assertNotIn("extension_echo", runtime.exposed_tool_names())
        finally:
            runtime.close()

    def test_runtime_close_stops_extension_once(self) -> None:
        runtime = self.runtime(("echo",))

        runtime.close()
        runtime.close()

        self.assertEqual(EchoExtension.stops, ["echo"])


if __name__ == "__main__":
    unittest.main()
