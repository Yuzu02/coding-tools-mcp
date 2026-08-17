from __future__ import annotations

import unittest

from coding_tools_mcp.extensions.contributions import (
    ComposedTool,
    ContributionError,
    ContributionRegistry,
    ServerMetadataContribution,
    ToolAnnotations,
    ToolContribution,
    compose_tools,
)


def core_handler(args: dict[str, object]) -> dict[str, object]:
    return {"source": "core", **args}


CORE = ComposedTool(
    name="core_tool",
    title="Core",
    description="core",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=ToolAnnotations(read_only=True, idempotent=True),
    handler=core_handler,
    origin="core",
)


class ToolContributionTests(unittest.TestCase):
    def test_new_tool_is_added_with_origin_metadata(self) -> None:
        registry = ContributionRegistry()
        registry.add_tool(
            "extra",
            ToolContribution(
                name="extra_tool",
                title="Extra",
                description="extra",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=lambda args: {"source": "extra"},
                text_renderer=lambda payload: f"extra:{payload['source']}",
            ),
        )

        tools = compose_tools({"core_tool": CORE}, registry, ("extra",))

        self.assertEqual(tools["extra_tool"].origin, "extra")
        self.assertEqual(tools["extra_tool"].handler({}), {"source": "extra"})
        self.assertIsNotNone(tools["extra_tool"].text_renderer)
        assert tools["extra_tool"].text_renderer is not None
        self.assertEqual(tools["extra_tool"].text_renderer({"source": "extra"}), "extra:extra")

    def test_extension_cannot_replace_core_tool(self) -> None:
        registry = ContributionRegistry()
        registry.add_tool(
            "extra",
            ToolContribution(
                name="core_tool",
                title="Replacement",
                description="replacement",
                input_schema={"type": "object"},
                handler=lambda args: {},
            ),
        )

        with self.assertRaisesRegex(ContributionError, "tool collision: core_tool"):
            compose_tools({"core_tool": CORE}, registry, ("extra",))

    def test_two_extensions_cannot_contribute_same_tool(self) -> None:
        registry = ContributionRegistry()
        contribution = ToolContribution(
            name="duplicate",
            title="Duplicate",
            description="duplicate",
            input_schema={"type": "object"},
            handler=lambda args: {},
        )
        registry.add_tool("a", contribution)

        with self.assertRaisesRegex(ContributionError, "tool already contributed: duplicate"):
            registry.add_tool("b", contribution)

    def test_registry_rejects_mutation_after_freeze(self) -> None:
        registry = ContributionRegistry()
        registry.freeze()

        with self.assertRaisesRegex(ContributionError, "contribution registry is frozen"):
            registry.add_metadata("a", ServerMetadataContribution(key="status", value="ok"))

    def test_composed_mapping_is_read_only(self) -> None:
        tools = compose_tools({"core_tool": CORE}, ContributionRegistry(), ())

        with self.assertRaises(TypeError):
            tools["other"] = CORE  # type: ignore[index]

    def test_metadata_is_namespaced_and_snapshot_is_detached(self) -> None:
        registry = ContributionRegistry()
        registry.add_metadata("a", ServerMetadataContribution(key="status", value="ready"))
        registry.add_metadata("b", ServerMetadataContribution(key="status", value="degraded"))

        snapshot = registry.metadata_snapshot()
        snapshot["a"]["status"] = "mutated"

        self.assertEqual(registry.metadata_snapshot()["a"]["status"], "ready")
        self.assertEqual(registry.metadata_snapshot()["b"]["status"], "degraded")

    def test_duplicate_metadata_key_in_same_namespace_is_rejected(self) -> None:
        registry = ContributionRegistry()
        registry.add_metadata("a", ServerMetadataContribution(key="status", value="ready"))

        with self.assertRaisesRegex(ContributionError, "duplicate metadata contribution: a.status"):
            registry.add_metadata("a", ServerMetadataContribution(key="status", value="again"))

    def test_invalid_composed_schema_is_rejected(self) -> None:
        invalid = ComposedTool(
            name="invalid",
            title="Invalid",
            description="invalid",
            input_schema={"type": "string"},
            handler=lambda args: {},
            annotations=ToolAnnotations(),
            origin="core",
        )

        with self.assertRaisesRegex(ContributionError, "tool input schema must be an object: invalid"):
            compose_tools({"invalid": invalid}, ContributionRegistry(), ())


if __name__ == "__main__":
    unittest.main()
