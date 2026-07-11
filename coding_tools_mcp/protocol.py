from __future__ import annotations

from typing import Any

from .errors import JsonRpcError


PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION, "2025-06-18")


def invalid_request_response() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}


def response_id(request: dict[str, Any]) -> str | int | None:
    """Return a response-safe JSON-RPC id, using null for invalid or absent ids."""

    value = request.get("id")
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    return None


def validate_rpc_envelope(request: dict[str, Any]) -> None:
    if request.get("jsonrpc") != "2.0":
        raise JsonRpcError(-32600, "Invalid Request: jsonrpc must be 2.0", {"reason": "jsonrpc_version"})
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(-32600, "Invalid Request: method must be a string", {"reason": "method"})
    if "id" in request and not (
        request["id"] is None
        or isinstance(request["id"], str)
        or (isinstance(request["id"], int) and not isinstance(request["id"], bool))
    ):
        raise JsonRpcError(-32600, "Invalid Request: id must be string, integer, or null", {"reason": "id"})


def rpc_params(request: dict[str, Any]) -> dict[str, Any]:
    params = request.get("params", {})
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "MCP method params must be an object")
    return params


def validate_initialize_params(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion")
    if requested is None:
        return PROTOCOL_VERSION
    if not protocol_version_is_supported(requested):
        raise JsonRpcError(
            -32602,
            "Unsupported MCP protocol version",
            {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "received": requested},
        )
    return str(requested)


def validate_initialize_request(request: dict[str, Any]) -> None:
    if "id" not in request or request.get("id") is None:
        raise JsonRpcError(-32600, "initialize must be a JSON-RPC request with a non-null id")


def protocol_version_is_supported(version: Any) -> bool:
    return isinstance(version, str) and version in SUPPORTED_PROTOCOL_VERSIONS
