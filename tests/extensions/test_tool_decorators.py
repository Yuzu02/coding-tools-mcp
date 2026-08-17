from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from coding_tools_mcp.extensions.contributions import (
    ComposedTool,
    ContributionError,
    ContributionRegistry,
    SchemaPatch,
    ToolAnnotations,
    ToolDecorator,
    ToolHandler,
    compose_tools,
)


CORE = ComposedTool(
    name="core_tool",
    title="Core",
    description="core",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=ToolAnnotations(read_only=True, idempotent=True),
    handler=lambda args: {"source": "core", **args},
    origin="core",
)


def recording_wrapper(label: str, events: list[str]):
    def wrap(next_handler: ToolHandler) -> ToolHandler:
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            events.append(label)
            return next_handler(args)

        return handler

    return wrap


class ToolDecoratorTests(unittest.TestCase):
    def test_decorator_adds_required_schema_property_and_wraps_handler(self) -> None:
        events: list[str] = []
        registry = ContributionRegistry()
        registry.add_decorator(
            "projects",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(
                    properties={"project_id": {"type": "string", "minLength": 1}},
                    required=("project_id",),
                ),
                wrap_handler=recording_wrapper("projects", events),
            ),
        )

        tool = compose_tools({"core_tool": CORE}, registry, ("projects",))["core_tool"]

        self.assertIn("project_id", tool.input_schema["properties"])
        self.assertIn("project_id", tool.input_schema["required"])
        tool.handler({"project_id": "app"})
        self.assertEqual(events, ["projects"])

    def test_unknown_decorator_target_fails_composition(self) -> None:
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(
                targets=("missing",),
                schema_patch=SchemaPatch(),
                wrap_handler=lambda handler: handler,
            ),
        )

        with self.assertRaisesRegex(ContributionError, "unknown decorator target: missing"):
            compose_tools({"core_tool": CORE}, registry, ("a",))

    def test_decorator_cannot_replace_existing_schema_property(self) -> None:
        core = replace(
            CORE,
            input_schema={
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(properties={"project_id": {"type": "integer"}}),
                wrap_handler=lambda handler: handler,
            ),
        )

        with self.assertRaisesRegex(ContributionError, "schema property collision: core_tool.project_id"):
            compose_tools({"core_tool": core}, registry, ("a",))

    def test_two_decorators_cannot_add_same_property(self) -> None:
        registry = ContributionRegistry()
        for extension in ("a", "b"):
            registry.add_decorator(
                extension,
                ToolDecorator(
                    targets=("core_tool",),
                    schema_patch=SchemaPatch(properties={"project_id": {"type": "string"}}),
                    wrap_handler=lambda handler: handler,
                ),
            )

        with self.assertRaisesRegex(ContributionError, "schema property collision: core_tool.project_id"):
            compose_tools({"core_tool": CORE}, registry, ("a", "b"))

    def test_decorator_execution_order_matches_extension_order(self) -> None:
        events: list[str] = []
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(),
                wrap_handler=recording_wrapper("a", events),
            ),
        )
        registry.add_decorator(
            "b",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(),
                wrap_handler=recording_wrapper("b", events),
            ),
        )
        core = replace(CORE, handler=lambda args: events.append("core") or {"ok": True})

        compose_tools({"core_tool": core}, registry, ("a", "b"))["core_tool"].handler({})

        self.assertEqual(events, ["a", "b", "core"])

    def test_required_name_must_exist_after_schema_patch(self) -> None:
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(required=("project_id",)),
                wrap_handler=lambda handler: handler,
            ),
        )

        with self.assertRaisesRegex(ContributionError, "required schema property missing: core_tool.project_id"):
            compose_tools({"core_tool": CORE}, registry, ("a",))


if __name__ == "__main__":
    unittest.main()
