from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TextIO

from .operation_context import OperationContext, new_operation_context
from .protocol import (
    RequestContext,
    dispatch_rpc,
    invalid_request_response,
    jsonrpc_error,
    response_id,
)
from .telemetry import SessionTelemetry


class StdioRuntime(Protocol):
    telemetry: SessionTelemetry

    def initialize(
        self,
        client_info: dict[str, Any] | None = None,
        protocol_version: str = ...,
    ) -> dict[str, Any]: ...

    def initialize_result(self, protocol_version: str = ...) -> dict[str, Any]: ...

    def discover_payload(self) -> dict[str, Any]: ...

    def server_identity(self) -> dict[str, Any]: ...

    def list_tools(self) -> dict[str, Any]: ...

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RequestContext | None = None,
        operation_context: OperationContext | None = None,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


def serve_stdio(
    runtime: StdioRuntime,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    write_lock = threading.Lock()
    active_lock = threading.Lock()
    active: dict[str | int, OperationContext] = {}
    executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mcp-stdio")

    def write_response(response: dict[str, Any]) -> None:
        with write_lock:
            sink.write(json.dumps(response, separators=(",", ":")) + "\n")
            sink.flush()

    def register_operation(request: dict[str, Any]) -> OperationContext:
        request_id = response_id(request)
        operation = new_operation_context(request_id)
        if request_id is not None:
            with active_lock:
                active[request_id] = operation
        return operation

    def unregister_operation(operation: OperationContext) -> None:
        request_id = operation.request_id
        if request_id is None:
            return
        with active_lock:
            if active.get(request_id) is operation:
                active.pop(request_id, None)

    def cancel_request(request: dict[str, Any]) -> bool:
        if request.get("jsonrpc") != "2.0" or request.get("method") != "notifications/cancelled":
            return False
        params = request.get("params")
        if not isinstance(params, dict):
            return False
        request_id = params.get("requestId")
        if not (
            isinstance(request_id, str)
            or (isinstance(request_id, int) and not isinstance(request_id, bool))
        ):
            return False
        with active_lock:
            operation = active.get(request_id)
        if operation is not None:
            operation.cancellation.cancel()
        return True

    def dispatch_request(request: dict[str, Any], operation: OperationContext) -> None:
        response: dict[str, Any] | None
        try:
            response = dispatch_rpc(runtime, request, operation_context=operation)
        except Exception as exc:  # noqa: BLE001 - keep the stdio server alive
            response = jsonrpc_error(response_id(request), -32603, str(exc))
        finally:
            unregister_operation(operation)
        if response is not None and not operation.cancellation.cancelled:
            write_response(response)

    try:
        for line in source:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                # RecursionError included: a deeply nested document is a
                # document this server cannot parse, not a reason to end the
                # session.
                write_response(jsonrpc_error(None, -32700, "Parse error"))
                continue
            if not isinstance(request, dict):
                write_response(invalid_request_response())
                continue
            if cancel_request(request):
                continue
            if "id" not in request:
                # Non-cancellation notifications remain cheap and are served
                # synchronously; JSON-RPC notifications never get responses.
                try:
                    dispatch_rpc(runtime, request)
                except Exception:  # noqa: BLE001 - notification failures are silent
                    pass
                continue
            operation = register_operation(request)
            executor.submit(dispatch_request, request, operation)
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
        runtime.close()
    return 0
