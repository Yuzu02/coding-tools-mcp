from __future__ import annotations

import unittest

from coding_tools_mcp.extensions import (
    CapabilityKey,
    ContributionError,
    ExtensionContext,
    ExtensionManifest,
    ExtensionRegistry,
    RuntimeConfig,
    ServiceRegistryError,
)
from coding_tools_mcp.extensions.host import ExtensionHost


def lifecycle_extension(
    name: str,
    events: list[str],
    *,
    requires: tuple[str, ...] = (),
    fail_prepare: bool = False,
    fail_register: bool = False,
    fail_start: bool = False,
    fail_stop: bool = False,
):
    class FakeExtension:
        manifest = ExtensionManifest(name=name, requires=requires)

        def configure(self, config):
            events.append(f"{name}.configure")

        def prepare(self):
            events.append(f"{name}.prepare")
            if fail_prepare:
                raise RuntimeError(f"{name} prepare failed")

        def register(self, context):
            events.append(f"{name}.register")
            if fail_register:
                raise RuntimeError(f"{name} register failed")

        def start(self):
            events.append(f"{name}.start")
            if fail_start:
                raise RuntimeError(f"{name} start failed")

        def stop(self):
            events.append(f"{name}.stop")
            if fail_stop:
                raise RuntimeError(f"{name} stop failed")

    return FakeExtension


class ExtensionLifecycleTests(unittest.TestCase):
    def build_host(self, extension_types, enabled):
        registry = ExtensionRegistry(extension_types, default_enabled=())
        return ExtensionHost.build(
            registry=registry,
            config=RuntimeConfig.defaults(enabled=enabled),
            core_tools={},
        )

    def test_dependency_lifecycle_order_and_reverse_shutdown(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",))

        host = self.build_host([base, child], ("child", "base"))
        host.stop()

        self.assertEqual(
            events,
            [
                "base.configure",
                "child.configure",
                "base.prepare",
                "child.prepare",
                "base.register",
                "child.register",
                "base.start",
                "child.start",
                "child.stop",
                "base.stop",
            ],
        )

    def test_prepare_failure_registers_and_starts_nothing_and_cleans_up_in_reverse(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_prepare=True)

        with self.assertRaisesRegex(RuntimeError, "child prepare failed"):
            self.build_host([base, child], ("base", "child"))

        self.assertEqual(
            events,
            [
                "base.configure",
                "child.configure",
                "base.prepare",
                "child.prepare",
                "child.stop",
                "base.stop",
            ],
        )
        self.assertNotIn("base.register", events)
        self.assertNotIn("child.register", events)
        self.assertNotIn("base.start", events)
        self.assertNotIn("child.start", events)

    def test_registries_are_frozen_before_first_start_call(self) -> None:
        test_case = self

        class FreezeProbe:
            manifest = ExtensionManifest(name="probe")

            def configure(self, config):
                self.context: ExtensionContext | None = None

            def prepare(self):
                pass

            def register(self, context):
                self.context = context

            def start(self):
                assert self.context is not None
                with test_case.assertRaisesRegex(ServiceRegistryError, "service registry is frozen"):
                    self.context.services.provide(CapabilityKey[int]("late.service"), 1)
                with test_case.assertRaisesRegex(ContributionError, "contribution registry is frozen"):
                    self.context.add_metadata("late", True)

            def stop(self):
                pass

        host = self.build_host([FreezeProbe], ("probe",))
        host.stop()

    def test_registration_failure_starts_nothing(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_register=True)

        with self.assertRaisesRegex(RuntimeError, "child register failed"):
            self.build_host([base, child], ("base", "child"))

        self.assertNotIn("base.start", events)
        self.assertNotIn("child.start", events)

    def test_start_failure_stops_failing_extension_then_previously_started_extensions(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_start=True)

        with self.assertRaisesRegex(RuntimeError, "child start failed"):
            self.build_host([base, child], ("base", "child"))

        self.assertEqual(events[-2:], ["child.stop", "base.stop"])

    def test_stop_is_idempotent(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        host = self.build_host([base], ("base",))

        host.stop()
        host.stop()

        self.assertEqual(events.count("base.stop"), 1)

    def test_one_stop_failure_does_not_skip_remaining_extensions(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_stop=True)
        host = self.build_host([base, child], ("base", "child"))

        warnings = host.stop()

        self.assertIn("child stop failed", "\n".join(warnings))
        self.assertIn("base.stop", events)

    def test_seed_service_is_available_during_registration(self) -> None:
        key = CapabilityKey[str]("test.seed")
        seen: list[str] = []

        class Consumer:
            manifest = ExtensionManifest(name="consumer")

            def configure(self, config):
                pass

            def prepare(self):
                pass

            def register(self, context):
                seen.append(context.services.require(key))

            def start(self):
                pass

            def stop(self):
                pass

        registry = ExtensionRegistry([Consumer], default_enabled=())
        host = ExtensionHost.build(
            registry=registry,
            config=RuntimeConfig.defaults(enabled=("consumer",)),
            core_tools={},
            seed_services=((key, "seeded"),),
        )
        try:
            self.assertEqual(seen, ["seeded"])
        finally:
            host.stop()

    def test_metadata_contains_only_declared_bounded_contributions(self) -> None:
        class MetadataExtension:
            manifest = ExtensionManifest(name="meta")

            def configure(self, config):
                self.config = config

            def prepare(self):
                pass

            def register(self, context):
                context.add_metadata("health", "ready")

            def start(self):
                pass

            def stop(self):
                pass

        registry = ExtensionRegistry([MetadataExtension], default_enabled=())
        host = ExtensionHost.build(
            registry=registry,
            config=RuntimeConfig.defaults(
                enabled=("meta",),
                settings={"meta": {"secret": "must-not-leak"}},
            ),
            core_tools={},
        )
        try:
            metadata = host.metadata()
            self.assertEqual(metadata["enabled"], ["meta"])
            self.assertEqual(metadata["metadata"], {"meta": {"health": "ready"}})
            self.assertNotIn("must-not-leak", repr(metadata))
        finally:
            host.stop()


if __name__ == "__main__":
    unittest.main()
