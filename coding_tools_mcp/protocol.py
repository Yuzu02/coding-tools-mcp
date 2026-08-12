from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import JsonRpcError


# The two eras negotiate their version through different channels and must not
# borrow each other's values: legacy versions are agreed once by ``initialize``,
# modern versions travel in the ``_meta`` of every request.
LEGACY_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
LATEST_LEGACY_PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSIONS[0]
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
LEGACY_ERA = "legacy"
MODERN_ERA = "modern"

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"

UNSUPPORTED_PROTOCOL_VERSION = -32022
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021

KNOWN_METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "notifications/cancelled",
        "ping",
        "tools/list",
        "tools/call",
    }
)
MODERN_METHODS = frozenset(
    {
        "notifications/cancelled",
        "ping",
        "tools/list",
        "tools/call",
    }
)


@dataclass(frozen=True)
class RequestContext:
    """Per-request facts that transports hand to the runtime.

    One runtime serves concurrent clients, so a request carries its own
    context instead of parking it on runtime state. ``frozen`` freezes only
    the top level: ``client_info`` and ``client_capabilities`` hold deep copies
    of the validated ``_meta`` objects so a later mutation of the request body
    cannot reach into a context that has already been handed on.
    """

    era: str = LEGACY_ERA
    protocol_version: str = LATEST_LEGACY_PROTOCOL_VERSION
    client_info: Mapping[str, Any] | None = None
    client_capabilities: Mapping[str, Any] | None = None


def jsonrpc_error(
    request_id: str | int | None, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """Build a JSON-RPC error response envelope."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def invalid_request_response() -> dict[str, Any]:
    return jsonrpc_error(None, -32600, "Invalid Request")


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
    """Negotiate the legacy handshake version, downgrading what we cannot speak.

    The handshake spec requires the server to answer a version it does not
    support with one it does, so every unsupported value — including the modern
    ``2026-07-28``, which is carried per request instead of negotiated — comes
    back as the newest legacy version rather than as an error.
    """

    requested = params.get("protocolVersion")
    if legacy_protocol_version_is_supported(requested):
        return str(requested)
    return LATEST_LEGACY_PROTOCOL_VERSION


def validate_initialize_request(request: dict[str, Any]) -> None:
    if "id" not in request or request.get("id") is None:
        raise JsonRpcError(-32600, "initialize must be a JSON-RPC request with a non-null id")


def legacy_protocol_version_is_supported(version: Any) -> bool:
    return isinstance(version, str) and version in LEGACY_PROTOCOL_VERSIONS


def request_era(method: str, params: Mapping[str, Any]) -> str:
    """Decide which protocol era a request belongs to.

    The only signal is a ``_meta`` carrying the modern protocol version key.
    ``initialize`` is the one exception and is always legacy: a client that
    sends a handshake is asking for one, whatever its ``_meta`` says. Legacy
    requests carry unrelated ``_meta`` entries such as ``progressToken``, so the
    key must be present, not merely the ``_meta`` object.
    """

    if method == "initialize":
        return LEGACY_ERA
    meta = params.get("_meta")
    if isinstance(meta, dict) and META_PROTOCOL_VERSION in meta:
        return MODERN_ERA
    return LEGACY_ERA


def modern_request_context(params: Mapping[str, Any]) -> RequestContext:
    """Validate a modern request's ``_meta`` and turn it into a context.

    Only called once :func:`request_era` has found the protocol version key, so
    ``_meta`` is known to be an object that carries it.
    """

    meta = params["_meta"]
    version = meta[META_PROTOCOL_VERSION]
    if not isinstance(version, str):
        raise JsonRpcError(
            -32602,
            f"{META_PROTOCOL_VERSION} must be a string",
            {"reason": "protocol_version"},
        )
    if version not in MODERN_PROTOCOL_VERSIONS:
        raise JsonRpcError(
            UNSUPPORTED_PROTOCOL_VERSION,
            f"Unsupported MCP protocol version in _meta: {version}",
            {"supported": list(MODERN_PROTOCOL_VERSIONS), "received": version},
        )
    capabilities = meta.get(META_CLIENT_CAPABILITIES)
    if not isinstance(capabilities, dict):
        raise JsonRpcError(
            -32602,
            f"{META_CLIENT_CAPABILITIES} is required and must be an object",
            {"reason": "client_capabilities"},
        )
    client_info: Mapping[str, Any] | None = None
    if META_CLIENT_INFO in meta:
        declared = meta[META_CLIENT_INFO]
        if not isinstance(declared, dict):
            raise JsonRpcError(
                -32602,
                f"{META_CLIENT_INFO} must be an object when present",
                {"reason": "client_info"},
            )
        client_info = copy.deepcopy(declared)
    return RequestContext(
        era=MODERN_ERA,
        protocol_version=version,
        client_info=client_info,
        client_capabilities=copy.deepcopy(capabilities),
    )


def dispatch_rpc(runtime: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one MCP JSON-RPC request against a runtime, shared by all transports.

    The era is decided first, from the request itself: a modern request states
    its protocol version per request and never touches the handshake state a
    legacy client builds up on ``runtime.initialized``. Transports add only
    their transport-specific framing (session headers, stream handling) around
    this. Returns None for notifications and requests without an id.
    """

    request_id = request.get("id")
    try:
        validate_rpc_envelope(request)
        method = request["method"]
        params = rpc_params(request)
        if request_era(method, params) == MODERN_ERA:
            context = modern_request_context(params)
            result = _dispatch_modern(runtime, method, params, context)
        else:
            context = RequestContext(era=LEGACY_ERA, protocol_version=runtime.protocol_version)
            result = _dispatch_legacy(runtime, request, method, params, context)
        if result is None or request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except JsonRpcError as exc:
        return jsonrpc_error(response_id(request), exc.code, exc.message, exc.data)


def _dispatch_modern(
    runtime: Any,
    method: str,
    params: dict[str, Any],
    context: RequestContext,
) -> dict[str, Any] | None:
    """Handle a request that carries its own protocol version.

    There is no handshake to be missing, so the initialized guard never applies
    here. Returns None for a notification.
    """

    if method not in MODERN_METHODS:
        raise JsonRpcError(-32601, f"Unknown method: {method}")
    if method == "notifications/cancelled":
        # Accepted and acknowledged by staying silent, as in the legacy era.
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return runtime.list_tools()
    return _call_tool(runtime, params, context)


def _dispatch_legacy(
    runtime: Any,
    request: dict[str, Any],
    method: str,
    params: dict[str, Any],
    context: RequestContext,
) -> dict[str, Any] | None:
    """Handle a request that negotiated its version through ``initialize``.

    Handshake state lives on ``runtime.initialized``. A method this server does
    not implement is rejected before that state is consulted, so a client
    probing for an unsupported method learns the method is unknown instead of
    being told to handshake first. Returns None for a notification.
    """

    if method not in KNOWN_METHODS:
        raise JsonRpcError(-32601, f"Unknown method: {method}")
    if not runtime.initialized and method not in {"initialize", "ping"}:
        raise JsonRpcError(-32002, "Server not initialized")
    if method == "initialize":
        validate_initialize_request(request)
        negotiated_version = validate_initialize_params(params)
        if runtime.initialized:
            # Some connectors send a second initialize on one persistent
            # STDIO process. Rejecting it fails their tool scan even though
            # the session is healthy, so replay the negotiated handshake
            # instead. The initializer is not run again, so no session
            # state is reset by a repeat.
            if negotiated_version != runtime.protocol_version:
                raise JsonRpcError(
                    -32600,
                    "Server is already initialized with a different protocol version",
                    {"expected": runtime.protocol_version, "received": negotiated_version},
                )
            return runtime.initialize_result()
        runtime.protocol_version = negotiated_version
        client_info = params.get("clientInfo")
        result = runtime.initialize(client_info if isinstance(client_info, dict) else None)
        runtime.initialized = True
        return result
    if method == "notifications/initialized":
        return None
    if method == "notifications/cancelled":
        # Accepted and acknowledged by staying silent. A command outlives
        # the request that started it and is shared with every other
        # client of this workspace, so cancelling a request must not kill
        # it; clients terminate a command with kill_command.
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return runtime.list_tools()
    if method == "tools/call":
        return _call_tool(runtime, params, context)
    # only reachable if KNOWN_METHODS gains a method without a branch here
    raise JsonRpcError(-32601, f"Unknown method: {method}")


def _call_tool(runtime: Any, params: dict[str, Any], context: RequestContext) -> dict[str, Any]:
    if not isinstance(params.get("name"), str):
        raise JsonRpcError(-32602, "tools/call requires a tool name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "tools/call arguments must be an object")
    return runtime.call_tool(params["name"], arguments, context=context)
