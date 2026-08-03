from __future__ import annotations

import unittest
from dataclasses import dataclass

from coding_tools_mcp.server import build_parser, http_session_options_from_args
from coding_tools_mcp.transport_http import HTTPSessionManager, HTTPSessionOptions


@dataclass
class FakeRuntime:
    http_session_id: str
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RuntimeFactory:
    def __init__(self) -> None:
        self.created: list[FakeRuntime] = []

    def __call__(self) -> FakeRuntime:
        runtime = FakeRuntime(f"session-{len(self.created) + 1}")
        self.created.append(runtime)
        return runtime


class HTTPSessionManagerTests(unittest.TestCase):
    def test_ephemeral_cli_mode_uses_short_ttl_and_idle_eviction(self) -> None:
        args = build_parser().parse_args(["--http-session-mode", "ephemeral"])

        self.assertEqual(
            http_session_options_from_args(args),
            HTTPSessionOptions(
                max_sessions=128,
                idle_ttl_seconds=60.0,
                evict_idle_on_capacity=True,
            ),
        )

    def test_stateful_cli_mode_preserves_legacy_retention(self) -> None:
        args = build_parser().parse_args([])

        self.assertEqual(
            http_session_options_from_args(args),
            HTTPSessionOptions(
                max_sessions=128,
                idle_ttl_seconds=3600.0,
                evict_idle_on_capacity=False,
            ),
        )

    def test_ephemeral_capacity_evicts_oldest_idle_session(self) -> None:
        clock = FakeClock()
        factory = RuntimeFactory()
        manager = HTTPSessionManager(
            factory,
            max_sessions=2,
            idle_ttl_seconds=60,
            evict_idle_on_capacity=True,
            clock=clock,
        )

        first = manager.create()
        clock.advance(1)
        second = manager.create()
        clock.advance(1)
        third = manager.create()

        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self.assertFalse(third.closed)
        self.assertEqual(manager.stats()["sessions"], 2)
        self.assertEqual(manager.stats()["evicted"], 1)

    def test_active_sessions_are_not_pruned_or_evicted(self) -> None:
        clock = FakeClock()
        factory = RuntimeFactory()
        manager = HTTPSessionManager(
            factory,
            max_sessions=2,
            idle_ttl_seconds=1,
            evict_idle_on_capacity=True,
            clock=clock,
        )

        first = manager.create(active=True)
        clock.advance(2)
        second = manager.create(active=True)
        manager.prune()

        self.assertFalse(first.closed)
        self.assertFalse(second.closed)
        with self.assertRaisesRegex(RuntimeError, "maximum HTTP session count reached"):
            manager.create()

        manager.release(first.http_session_id)
        third = manager.create()
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self.assertFalse(third.closed)

    def test_idle_ttl_starts_after_active_request_releases(self) -> None:
        clock = FakeClock()
        factory = RuntimeFactory()
        manager = HTTPSessionManager(
            factory,
            max_sessions=4,
            idle_ttl_seconds=5,
            evict_idle_on_capacity=True,
            clock=clock,
        )

        runtime = manager.create(active=True)
        clock.advance(30)
        manager.prune()
        self.assertFalse(runtime.closed)

        manager.release(runtime.http_session_id)
        clock.advance(4)
        manager.prune()
        self.assertFalse(runtime.closed)

        clock.advance(2)
        manager.prune()
        self.assertTrue(runtime.closed)

    def test_ephemeral_mode_handles_more_than_capacity_sequential_sessions(self) -> None:
        clock = FakeClock()
        factory = RuntimeFactory()
        manager = HTTPSessionManager(
            factory,
            max_sessions=128,
            idle_ttl_seconds=60,
            evict_idle_on_capacity=True,
            clock=clock,
        )

        for _ in range(256):
            manager.create()
            clock.advance(0.01)

        stats = manager.stats()
        self.assertEqual(stats["sessions"], 128)
        self.assertEqual(stats["evicted"], 128)
        self.assertEqual(stats["rejected"], 0)

    def test_stateful_mode_keeps_capacity_rejection(self) -> None:
        manager = HTTPSessionManager(
            RuntimeFactory(),
            max_sessions=1,
            idle_ttl_seconds=3600,
            evict_idle_on_capacity=False,
            clock=FakeClock(),
        )
        manager.create()

        with self.assertRaisesRegex(RuntimeError, "maximum HTTP session count reached"):
            manager.create()

        self.assertEqual(manager.stats()["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
