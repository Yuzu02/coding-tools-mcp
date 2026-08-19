from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .errors import JsonRpcError


REQUEST_STATE_VERSION = 1
REQUEST_STATE_TTL_SECONDS = 600
REQUEST_STATE_MAX_BYTES = 16 * 1024
REQUEST_STATE_PAYLOAD_MAX_BYTES = 8 * 1024
MRTR_MAX_ROUNDS = 8


@dataclass(frozen=True)
class ClientCapabilityView:
    elicitation_form: bool = False
    elicitation_url: bool = False
    sampling: bool = False
    roots: bool = False


@dataclass(frozen=True)
class MRTRRequestContext:
    input_responses: Mapping[str, Any]
    state: Mapping[str, Any]
    round: int


class InputRequired(dict[str, Any]):
    """Internal handler sentinel for a modern input_required result."""

    def __init__(
        self,
        *,
        input_requests: Mapping[str, Mapping[str, Any]] | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.input_requests = dict(input_requests or {})
        self.state = dict(state or {})
        if not self.input_requests and not self.state:
            raise ValueError("InputRequired needs input_requests or state")


_CURRENT_MRTR: contextvars.ContextVar[MRTRRequestContext | None] = contextvars.ContextVar(
    "coding_tools_mcp_mrtr_context",
    default=None,
)


def client_capability_view(raw: Mapping[str, Any]) -> ClientCapabilityView:
    elicitation = raw.get("elicitation")
    elicitation_map = elicitation if isinstance(elicitation, Mapping) else None
    return ClientCapabilityView(
        elicitation_form=elicitation_map is not None,
        elicitation_url=(
            isinstance(elicitation_map.get("url"), Mapping)
            if elicitation_map is not None
            else False
        ),
        sampling=isinstance(raw.get("sampling"), Mapping),
        roots=isinstance(raw.get("roots"), Mapping),
    )


@contextmanager
def bind_mrtr_context(context: MRTRRequestContext) -> Iterator[None]:
    token = _CURRENT_MRTR.set(context)
    try:
        yield
    finally:
        _CURRENT_MRTR.reset(token)


def current_mrtr_context() -> MRTRRequestContext:
    context = _CURRENT_MRTR.get()
    return context or MRTRRequestContext(input_responses={}, state={}, round=0)


def input_required(
    input_requests: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    state: Mapping[str, Any] | None = None,
) -> InputRequired:
    return InputRequired(input_requests=input_requests, state=state)


def arguments_fingerprint(arguments: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def seal_request_state(
    secret: bytes,
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    state: Mapping[str, Any],
    expected_responses: Mapping[str, str],
    round: int,
    now: float | None = None,
) -> str:
    if round < 1 or round > MRTR_MAX_ROUNDS:
        raise ValueError("MRTR round is outside the supported range")
    payload = {
        "v": REQUEST_STATE_VERSION,
        "tool": tool_name,
        "args": arguments_fingerprint(arguments),
        "round": round,
        "exp": int((now if now is not None else time.time()) + REQUEST_STATE_TTL_SECONDS),
        "state": dict(state),
        "expected": dict(expected_responses),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > REQUEST_STATE_PAYLOAD_MAX_BYTES:
        raise ValueError("MRTR request state payload is too large")
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    token = f"{_b64encode(encoded)}.{_b64encode(signature)}"
    if len(token.encode("utf-8")) > REQUEST_STATE_MAX_BYTES:
        raise ValueError("MRTR request state token is too large")
    return token


def unseal_request_state(
    secret: bytes,
    token: str,
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    now: float | None = None,
) -> tuple[dict[str, Any], int, dict[str, str]]:
    if len(token.encode("utf-8")) > REQUEST_STATE_MAX_BYTES or "." not in token:
        raise _invalid_request_state("requestState is malformed or oversized")
    payload_text, signature_text = token.split(".", 1)
    try:
        payload_bytes = _b64decode(payload_text)
        signature = _b64decode(signature_text)
    except (ValueError, TypeError) as exc:
        raise _invalid_request_state("requestState is not valid base64url") from exc
    expected = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise _invalid_request_state("requestState signature is invalid")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_request_state("requestState payload is invalid") from exc
    if not isinstance(payload, dict) or payload.get("v") != REQUEST_STATE_VERSION:
        raise _invalid_request_state("requestState version is unsupported")
    if payload.get("tool") != tool_name:
        raise _invalid_request_state("requestState belongs to a different tool")
    if payload.get("args") != arguments_fingerprint(arguments):
        raise _invalid_request_state("requestState arguments do not match this retry")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at < int(now if now is not None else time.time()):
        raise _invalid_request_state("requestState has expired")
    round_value = payload.get("round")
    if not isinstance(round_value, int) or round_value < 1 or round_value > MRTR_MAX_ROUNDS:
        raise _invalid_request_state("requestState round is invalid")
    state = payload.get("state", {})
    if not isinstance(state, dict):
        raise _invalid_request_state("requestState state payload is invalid")
    expected = payload.get("expected", {})
    if not isinstance(expected, dict) or any(
        not isinstance(key, str) or not isinstance(method, str)
        for key, method in expected.items()
    ):
        raise _invalid_request_state("requestState expected response map is invalid")
    return state, round_value, dict(expected)


def _invalid_request_state(message: str) -> JsonRpcError:
    return JsonRpcError(-32602, message, {"reason": "invalid_request_state"})


def required_capabilities(
    requests: Mapping[str, Mapping[str, Any]],
    capabilities: ClientCapabilityView,
) -> dict[str, Any]:
    required: dict[str, Any] = {}
    for key, request in requests.items():
        method = request.get("method")
        params = request.get("params")
        params_map = params if isinstance(params, Mapping) else {}
        if method == "elicitation/create":
            mode = params_map.get("mode", "form")
            if mode == "url":
                if not capabilities.elicitation_url:
                    required["elicitation"] = {"url": {}}
            elif not capabilities.elicitation_form:
                required["elicitation"] = {}
        elif method == "sampling/createMessage":
            if not capabilities.sampling:
                required["sampling"] = {}
        elif method == "roots/list":
            if not capabilities.roots:
                required["roots"] = {}
        else:
            raise JsonRpcError(
                -32602,
                f"Unsupported input request method for {key}: {method}",
                {"reason": "invalid_input_request"},
            )
    return required


def response_methods(requests: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    methods: dict[str, str] = {}
    for key, request in requests.items():
        method = request.get("method")
        if not isinstance(key, str) or not key or not isinstance(method, str):
            raise JsonRpcError(
                -32602,
                "inputRequests keys and methods must be non-empty strings",
                {"reason": "invalid_input_request"},
            )
        methods[key] = method
    return methods


def validate_input_responses(
    expected: Mapping[str, str],
    responses: Mapping[str, Any],
) -> None:
    if set(responses) != set(expected):
        raise JsonRpcError(
            -32602,
            "inputResponses keys do not match the preceding inputRequests",
            {"reason": "input_response_keys"},
        )
    for key, method in expected.items():
        response = responses.get(key)
        if not isinstance(response, Mapping):
            raise JsonRpcError(
                -32602,
                f"inputResponses.{key} must be an object",
                {"reason": "invalid_input_response"},
            )
        if method == "elicitation/create":
            action = response.get("action")
            if action not in {"accept", "decline", "cancel"}:
                raise JsonRpcError(
                    -32602,
                    f"inputResponses.{key}.action is invalid",
                    {"reason": "invalid_input_response"},
                )
            if action == "accept" and not isinstance(response.get("content"), Mapping):
                raise JsonRpcError(
                    -32602,
                    f"inputResponses.{key}.content is required for accepted elicitation",
                    {"reason": "invalid_input_response"},
                )
        elif method == "roots/list":
            if not isinstance(response.get("roots"), list):
                raise JsonRpcError(
                    -32602,
                    f"inputResponses.{key}.roots must be an array",
                    {"reason": "invalid_input_response"},
                )
        elif method == "sampling/createMessage":
            # Full content validation remains the sampling schema's concern;
            # the MRTR boundary only rejects non-object wire values here.
            continue
        else:
            raise JsonRpcError(
                -32602,
                f"Unsupported input response method for {key}: {method}",
                {"reason": "invalid_input_response"},
            )
