from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.extensions import (
    ExtensionManifest,
    ExtensionRegistry,
    RuntimeConfig,
    SchemaPatch,
    ToolAnnotations,
    ToolContribution,
    ToolDecorator,
)
from coding_tools_mcp.extensions.contributions import ToolHandler
from coding_tools_mcp.mrtr import current_mrtr_context, input_required
from coding_tools_mcp.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSIONS,
    dispatch_rpc,
)
from coding_tools_mcp.server import Runtime


class EchoExtension:
    manifest = ExtensionManifest(name="echo")
    stops: list[str] = []

    def configure(self, config):
        self.config = config

    def prepare(self):
        pass

    def register(self, context):
        def confirm(args: dict[str, Any]) -> dict[str, Any]:
            mrtr = current_mrtr_context()
            response = mrtr.input_responses.get("confirm")
            if not isinstance(response, dict):
                return input_required(
                    {
                        "confirm": {
                            "method": "elicitation/create",
                            "params": {
                                "mode": "form",
                                "message": "Confirm the test action",
                                "requestedSchema": {
                                    "type": "object",
                                    "properties": {"confirmed": {"type": "boolean"}},
                                    "required": ["confirmed"],
                                },
                            },
                        }
                    },
                    state={"value": args["value"]},
                )
            accepted = response.get("action") == "accept"
            content = response.get("content") if isinstance(response.get("content"), dict) else {}
            return {
                "value": str(mrtr.state.get("value", args["value"])),
                "confirmed": bool(accepted and content.get("confirmed") is True),
            }

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
                output_schema={
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "value": {"type": "string"},
                    },
                    "required": ["ok", "value"],
                    "additionalProperties": False,
                },
                handler=lambda args: {
                    "value": 7 if args["value"] == "bad-output" else args["value"]
                },
                annotations=ToolAnnotations(read_only=True, idempotent=True),
                text_renderer=lambda payload: f"echo:{payload['value']}",
            )
        )
        context.add_tool(
            ToolContribution(
                name="extension_confirm",
                title="Extension confirm",
                description="Exercise modern input-required flow.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=confirm,
                annotations=ToolAnnotations(read_only=True, idempotent=True),
            )
        )

    def start(self):
        pass

    def stop(self):
        self.stops.append("echo")


class DecoratorExtension:
    manifest = ExtensionManifest(name="decorator")

    def configure(self, config):
        pass

    def prepare(self):
        pass

    def register(self, context):
        def wrap(next_handler: ToolHandler) -> ToolHandler:
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                forwarded = dict(args)
                forwarded.pop("bridge_token", None)
                return next_handler(forwarded)

            return handler

        context.add_decorator(
            ToolDecorator(
                targets=("server_info",),
                schema_patch=SchemaPatch(
                    properties={"bridge_token": {"type": "string", "minLength": 1}},
                    required=("bridge_token",),
                ),
                wrap_handler=wrap,
            )
        )

    def start(self):
        pass

    def stop(self):
        pass


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

    def modern_tool_request(
        self,
        request_id: int,
        name: str,
        arguments: dict[str, Any],
        *,
        capabilities: dict[str, Any] | None = None,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": name,
            "arguments": arguments,
            "_meta": {
                META_PROTOCOL_VERSION: MODERN_PROTOCOL_VERSIONS[0],
                META_CLIENT_CAPABILITIES: capabilities or {},
            },
        }
        if input_responses is not None:
            params["inputResponses"] = input_responses
        if request_state is not None:
            params["requestState"] = request_state
        return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}

    def test_tools_list_contains_extension_contribution(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            self.assertIn("extension_echo", tools)
            self.assertTrue(tools["extension_echo"]["annotations"]["readOnlyHint"])
            self.assertEqual(tools["extension_echo"]["inputSchema"]["required"], ["value"])
            self.assertEqual(
                tools["extension_echo"]["outputSchema"]["required"],
                ["ok", "value"],
            )
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

    def test_declared_output_schema_is_enforced_without_leaking_invalid_structured_content(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            result = runtime.call_tool("extension_echo", {"value": "bad-output"})
            self.assertTrue(result["isError"], result)
            self.assertNotIn("structuredContent", result)
            text = "\n".join(
                str(item.get("text", ""))
                for item in result["content"]
                if item.get("type") == "text"
            )
            self.assertIn("OUTPUT_SCHEMA_VIOLATION", text)
        finally:
            runtime.close()

    def test_modern_tool_call_can_round_trip_input_required_state_without_session_state(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            first = dispatch_rpc(
                runtime,
                self.modern_tool_request(
                    1,
                    "extension_confirm",
                    {"value": "hello"},
                    capabilities={"elicitation": {}},
                ),
            )
            assert first is not None
            result = first["result"]
            self.assertEqual(result["resultType"], "input_required")
            self.assertIn("confirm", result["inputRequests"])
            state = result["requestState"]

            second = dispatch_rpc(
                runtime,
                self.modern_tool_request(
                    2,
                    "extension_confirm",
                    {"value": "hello"},
                    capabilities={"elicitation": {}},
                    input_responses={
                        "confirm": {"action": "accept", "content": {"confirmed": True}}
                    },
                    request_state=state,
                ),
            )
            assert second is not None
            second_result = second["result"]
            self.assertEqual(second_result["resultType"], "complete")
            self.assertTrue(second_result["structuredContent"]["confirmed"])
            self.assertEqual(second_result["structuredContent"]["value"], "hello")
        finally:
            runtime.close()

    def test_modern_input_required_rejects_missing_capability_and_tampered_state(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            missing = dispatch_rpc(
                runtime,
                self.modern_tool_request(1, "extension_confirm", {"value": "hello"}),
            )
            assert missing is not None
            self.assertEqual(missing["error"]["code"], -32021, missing)

            first = dispatch_rpc(
                runtime,
                self.modern_tool_request(
                    2,
                    "extension_confirm",
                    {"value": "hello"},
                    capabilities={"elicitation": {}},
                ),
            )
            assert first is not None
            state = first["result"]["requestState"]
            tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
            bad = dispatch_rpc(
                runtime,
                self.modern_tool_request(
                    3,
                    "extension_confirm",
                    {"value": "hello"},
                    capabilities={"elicitation": {}},
                    input_responses={"confirm": {"action": "decline"}},
                    request_state=tampered,
                ),
            )
            assert bad is not None
            self.assertEqual(bad["error"]["code"], -32602, bad)

            wrong_args = dispatch_rpc(
                runtime,
                self.modern_tool_request(
                    4,
                    "extension_confirm",
                    {"value": "different"},
                    capabilities={"elicitation": {}},
                    input_responses={"confirm": {"action": "decline"}},
                    request_state=state,
                ),
            )
            assert wrong_args is not None
            self.assertEqual(wrong_args["error"]["code"], -32602, wrong_args)
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

    def test_decorator_bridge_changes_both_schema_and_dispatch(self) -> None:
        registry = ExtensionRegistry([DecoratorExtension], default_enabled=())
        runtime = Runtime(
            self.root,
            extension_registry=registry,
            extension_config=RuntimeConfig.defaults(enabled=("decorator",)),
        )
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            schema = tools["server_info"]["inputSchema"]
            self.assertIn("bridge_token", schema["properties"])
            self.assertIn("bridge_token", schema["required"])

            with self.assertRaisesRegex(JsonRpcError, "arguments.bridge_token is required"):
                runtime.call_tool("server_info", {})

            result = runtime.call_tool("server_info", {"bridge_token": "ok"})
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["server"], "coding-tools-mcp")
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
