from __future__ import annotations

import unittest

from coding_tools_mcp.extensions.api import ExtensionManifest
from coding_tools_mcp.extensions.registry import ExtensionRegistry, ExtensionRegistryError


def fake_extension(name: str, requires: tuple[str, ...] = ()):
    class FakeExtension:
        manifest = ExtensionManifest(name=name, requires=requires)

        def configure(self, config):
            self.config = config

        def prepare(self):
            pass

        def register(self, context):
            self.context = context

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

    return FakeExtension


class ExtensionDependencyTests(unittest.TestCase):
    def test_dependency_order_is_stable_for_independent_extensions(self) -> None:
        alpha = fake_extension("alpha")
        beta = fake_extension("beta")
        registry = ExtensionRegistry([alpha, beta], default_enabled=())

        self.assertEqual(registry.resolve_order(("beta", "alpha")), ("beta", "alpha"))

    def test_dependency_is_ordered_before_dependent_even_when_requested_later(self) -> None:
        projects = fake_extension("projects")
        semantic = fake_extension("semantic", ("projects",))
        registry = ExtensionRegistry([projects, semantic], default_enabled=())

        self.assertEqual(
            registry.resolve_order(("semantic", "projects")),
            ("projects", "semantic"),
        )

    def test_required_extension_must_be_explicitly_enabled(self) -> None:
        projects = fake_extension("projects")
        semantic = fake_extension("semantic", ("projects",))
        registry = ExtensionRegistry([projects, semantic], default_enabled=())

        with self.assertRaisesRegex(
            ExtensionRegistryError,
            "semantic requires enabled extension projects",
        ):
            registry.resolve_order(("semantic",))

    def test_required_extension_must_exist_in_registry(self) -> None:
        semantic = fake_extension("semantic", ("projects",))
        registry = ExtensionRegistry([semantic], default_enabled=())

        with self.assertRaisesRegex(
            ExtensionRegistryError,
            "semantic requires unknown extension projects",
        ):
            registry.resolve_order(("semantic",))

    def test_dependency_cycle_is_rejected_with_cycle_names(self) -> None:
        alpha = fake_extension("alpha", ("beta",))
        beta = fake_extension("beta", ("alpha",))
        registry = ExtensionRegistry([alpha, beta], default_enabled=())

        with self.assertRaisesRegex(ExtensionRegistryError, "dependency cycle among: alpha, beta"):
            registry.resolve_order(("alpha", "beta"))

    def test_duplicate_enabled_names_are_rejected(self) -> None:
        alpha = fake_extension("alpha")
        registry = ExtensionRegistry([alpha], default_enabled=())

        with self.assertRaisesRegex(ExtensionRegistryError, "duplicate enabled extension: alpha"):
            registry.resolve_order(("alpha", "alpha"))

    def test_default_enabled_configuration_is_validated_at_construction(self) -> None:
        alpha = fake_extension("alpha")

        with self.assertRaisesRegex(ExtensionRegistryError, "unknown extension: missing"):
            ExtensionRegistry([alpha], default_enabled=("missing",))


if __name__ == "__main__":
    unittest.main()
