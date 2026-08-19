from __future__ import annotations

import json
import queue
import threading
import time
import unittest
from typing import Any

from coding_tools_mcp.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)
from coding_tools_mcp.transport_stdio import serve_stdio


MODERN_VERSION = MODERN_PROTOCOL_VERSIONS[0]


class QueueInput:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()

    def send(self, payload: dict[str, Any]) -> None:
        self.lines.put(json.dumps(payload, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self.lines.put(None)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        item = self.lines.get(timeout=5)
        if item is None:
            raise StopIteration
        return item


class QueueOutput:
    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()

    def write(self, value: str) -> int:
        self.lines.put(value)
        return len(value)

    def flush(self) -> None:
        return None

    def response(self, timeout: float = 2.0) -> dict[str, Any]:
        return json.loads(self.lines.get(timeout=timeout))


class BlockingRuntime:
    def __init__(self) -> None:
        class Telemetry:
            @staticmethod
            def record_request(era: str, method: str) -> None:
                return None

        self.telemetry = Telemetry()
        self.started = threading.Event()
        self.cancelled_seen = threading.Event()
        self.closed = False

    def server_identity(self) -> dict[str, str]:
        return {"name": "test", "title": "Test", "version": "0"}

    def list_tools(self) -> dict[str, object]:
        return {"tools": []}

    def discover_payload(self) -> dict[str, object]:
        return {"supportedVersions": [MODERN_VERSION], "capabilities": {}}

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context=None,
        operation_context=None,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> dict[str, Any]:
        self.started.set()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            cancellation = getattr(operation_context, "cancellation", None)
            if cancellation is not None and cancellation.cancelled:
                self.cancelled_seen.set()
                break
            time.sleep(0.01)
        return {
            "content": [{"type": "text", "text": "finished"}],
            "structuredContent": {"ok": True},
            "isError": False,
        }

    def close(self) -> None:
        self.closed = True


def modern_params(**values: object) -> dict[str, object]:
    return {
        **values,
        "_meta": {
            META_PROTOCOL_VERSION: MODERN_VERSION,
            META_CLIENT_CAPABILITIES: {},
        },
    }


class StdioCancellationTests(unittest.TestCase):
    def test_cancellation_interrupts_inflight_request_and_suppresses_its_response(self) -> None:
        source = QueueInput()
        sink = QueueOutput()
        runtime = BlockingRuntime()
        server = threading.Thread(
            target=serve_stdio,
            args=(runtime,),
            kwargs={"input_stream": source, "output_stream": sink},
            daemon=True,
        )
        server.start()

        source.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": modern_params(name="blocking", arguments={}),
            }
        )
        self.assertTrue(runtime.started.wait(timeout=1.0))
        source.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 1, "reason": "test cancellation"},
            }
        )
        self.assertTrue(runtime.cancelled_seen.wait(timeout=1.0))

        source.send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": modern_params(),
            }
        )
        response = sink.response()
        self.assertEqual(response.get("id"), 2, response)

        source.close()
        server.join(timeout=2.0)
        self.assertFalse(server.is_alive())
        self.assertTrue(runtime.closed)
        with self.assertRaises(queue.Empty):
            sink.response(timeout=0.1)


if __name__ == "__main__":
    unittest.main()
