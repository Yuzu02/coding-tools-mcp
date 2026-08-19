from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from typing import Any

from coding_tools_mcp.protocol import (
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSIONS,
)
from coding_tools_mcp.server import MCPHandler, MCP_ENDPOINT_PATH, RuntimeHTTPServer


MODERN_VERSION = MODERN_PROTOCOL_VERSIONS[0]


class BlockingHTTPRuntime:
    auth_token = None
    oauth_config = None

    def __init__(self) -> None:
        class Telemetry:
            @staticmethod
            def record_request(era: str, method: str) -> None:
                return None

        self.telemetry = Telemetry()
        self.started = threading.Event()
        self.cancelled_seen = threading.Event()
        self.closed = False

    def auth_enabled(self) -> bool:
        return False

    def server_identity(self) -> dict[str, str]:
        return {"name": "test", "title": "Test", "version": "0"}

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context=None,
        operation_context=None,
    ) -> dict[str, Any]:
        self.started.set()
        deadline = time.monotonic() + 2.0
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


class HTTPRequestCancellationTests(unittest.TestCase):
    def test_modern_client_disconnect_cancels_request_owned_work(self) -> None:
        runtime = BlockingHTTPRuntime()
        server = RuntimeHTTPServer(("127.0.0.1", 0), MCPHandler, runtime)  # type: ignore[arg-type]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "blocking",
                        "arguments": {},
                        "_meta": {
                            META_PROTOCOL_VERSION: MODERN_VERSION,
                            META_CLIENT_CAPABILITIES: {},
                        },
                    },
                },
                separators=(",", ":"),
            ).encode()
            sock = socket.create_connection(server.server_address, timeout=2)
            request = (
                f"POST {MCP_ENDPOINT_PATH} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{server.server_port}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"MCP-Protocol-Version: {MODERN_VERSION}\r\n"
                "Mcp-Method: tools/call\r\n"
                "Mcp-Name: blocking\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode() + body
            sock.sendall(request)
            self.assertTrue(runtime.started.wait(timeout=1.0))
            sock.close()

            self.assertTrue(runtime.cancelled_seen.wait(timeout=1.0))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
