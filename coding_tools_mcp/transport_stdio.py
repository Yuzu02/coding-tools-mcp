from __future__ import annotations

import json
import sys
from typing import Any, Protocol, TextIO

from .errors import JsonRpcError
from .protocol import (
    invalid_request_response,
    response_id,
    rpc_params,
    validate_initialize_params,
    validate_initialize_request,
    validate_rpc_envelope,
)


class StdioRuntime(Protocol):
    protocol_version: str

    def initialize(self) -> dict[str, Any]: ...

    def list_tools(self) -> dict[str, Any]: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str | int | None = None,
    ) -> dict[str, Any]: ...

    def cancel_request(self, request_id: str | int) -> None: ...

    def close(self) -> None: ...


class StdioDispatcher:
    def __init__(self, runtime: StdioRuntime) -> None:
        self.runtime = runtime
        self.initialized = False

    def handle_rpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        try:
            validate_rpc_envelope(request)
            method = request["method"]
            params = rpc_params(request)
            if not self.initialized and method not in {"initialize", "ping"}:
                raise JsonRpcError(-32002, "Server not initialized")
            if method == "initialize":
                if self.initialized:
                    raise JsonRpcError(-32600, "Server is already initialized")
                validate_initialize_request(request)
                self.runtime.protocol_version = validate_initialize_params(params)
                result = self.runtime.initialize()
                self.initialized = True
            elif method == "notifications/initialized":
                return None
            elif method == "notifications/cancelled":
                cancelled_request_id = params.get("requestId")
                if isinstance(cancelled_request_id, (str, int)) and not isinstance(cancelled_request_id, bool):
                    self.runtime.cancel_request(cancelled_request_id)
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self.runtime.list_tools()
            elif method == "tools/call":
                if not isinstance(params.get("name"), str):
                    raise JsonRpcError(-32602, "tools/call requires a tool name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise JsonRpcError(-32602, "tools/call arguments must be an object")
                result = self.runtime.call_tool(params["name"], arguments, request_id=request_id)
            else:
                raise JsonRpcError(-32601, f"Unknown method: {method}")
            if request_id is None:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcError as exc:
            error: dict[str, Any] = {"code": exc.code, "message": exc.message}
            if exc.data is not None:
                error["data"] = exc.data
            return {"jsonrpc": "2.0", "id": response_id(request), "error": error}


def serve_stdio(
    runtime: StdioRuntime,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    dispatcher = StdioDispatcher(runtime)
    try:
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            else:
                try:
                    response = (
                        dispatcher.handle_rpc(request)
                        if isinstance(request, dict)
                        else invalid_request_response()
                    )
                except Exception as exc:  # noqa: BLE001 - keep the stdio server alive
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": str(exc)},
                    }
            if response is not None:
                sink.write(
                    json.dumps(response, separators=(",", ":")) + "\n"
                )
                sink.flush()
    finally:
        runtime.close()
    return 0
