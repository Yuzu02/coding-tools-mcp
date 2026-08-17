from __future__ import annotations

import unittest

from coding_tools_mcp.extensions.services import CapabilityKey, ServiceRegistry, ServiceRegistryError


class ExtensionServiceTests(unittest.TestCase):
    def test_provide_then_require_returns_same_object(self) -> None:
        registry = ServiceRegistry()
        key = CapabilityKey[object]("test.value")
        value = object()

        registry.provide(key, value)

        self.assertIs(registry.require(key), value)

    def test_duplicate_provider_is_rejected(self) -> None:
        registry = ServiceRegistry()
        key = CapabilityKey[int]("test.value")
        registry.provide(key, 1)

        with self.assertRaisesRegex(ServiceRegistryError, "duplicate capability provider: test.value"):
            registry.provide(key, 2)

    def test_missing_required_capability_is_rejected(self) -> None:
        registry = ServiceRegistry()

        with self.assertRaisesRegex(ServiceRegistryError, "required capability unavailable: test.missing"):
            registry.require(CapabilityKey[object]("test.missing"))

    def test_registry_is_immutable_after_freeze(self) -> None:
        registry = ServiceRegistry()
        registry.freeze()

        with self.assertRaisesRegex(ServiceRegistryError, "service registry is frozen"):
            registry.provide(CapabilityKey[int]("test.value"), 1)

    def test_freeze_state_is_observable(self) -> None:
        registry = ServiceRegistry()
        self.assertFalse(registry.frozen)

        registry.freeze()

        self.assertTrue(registry.frozen)

    def test_capability_keys_with_same_name_are_equal(self) -> None:
        self.assertEqual(CapabilityKey[int]("test.value"), CapabilityKey[str]("test.value"))


if __name__ == "__main__":
    unittest.main()
