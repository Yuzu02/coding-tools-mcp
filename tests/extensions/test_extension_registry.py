from __future__ import annotations

import unittest

from coding_tools_mcp.extensions.api import ExtensionManifest
from coding_tools_mcp.extensions.registry import ExtensionRegistry, ExtensionRegistryError


class Alpha:
    manifest = ExtensionManifest(name="alpha", description="alpha")

    def configure(self, config):
        self.config = config

    def register(self, context):
        self.context = context

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class AnotherAlpha(Alpha):
    manifest = ExtensionManifest(name="alpha", description="duplicate")


class ExtensionRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_manifest_names(self) -> None:
        with self.assertRaisesRegex(ExtensionRegistryError, "duplicate extension name: alpha"):
            ExtensionRegistry([Alpha, AnotherAlpha], default_enabled=())

    def test_registry_rejects_invalid_manifest_name(self) -> None:
        class InvalidName(Alpha):
            manifest = ExtensionManifest(name="not valid")

        with self.assertRaisesRegex(ExtensionRegistryError, "invalid extension name"):
            ExtensionRegistry([InvalidName], default_enabled=())

    def test_registry_rejects_invalid_dependency_name(self) -> None:
        class InvalidDependency(Alpha):
            manifest = ExtensionManifest(name="alpha", requires=("bad dependency",))

        with self.assertRaisesRegex(ExtensionRegistryError, "invalid extension name"):
            ExtensionRegistry([InvalidDependency], default_enabled=())

    def test_unknown_enabled_extension_is_configuration_error(self) -> None:
        registry = ExtensionRegistry([Alpha], default_enabled=())
        with self.assertRaisesRegex(ExtensionRegistryError, "unknown extension: missing"):
            registry.resolve_order(("missing",))


if __name__ == "__main__":
    unittest.main()
