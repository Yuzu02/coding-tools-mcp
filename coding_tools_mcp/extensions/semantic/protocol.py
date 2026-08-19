from __future__ import annotations

import json
from typing import Any, Mapping


WORKER_PROTOCOL_VERSION = 1
MAX_WORKER_MESSAGE_BYTES = 4 * 1024 * 1024
SEMANTIC_OPERATIONS = frozenset(
    {
        "list_symbols",
        "find_symbol",
        "find_definition",
        "find_references",
        "find_implementations",
        "get_diagnostics",
    }
)


class WorkerProtocolError(ValueError):
    pass


def _require_nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkerProtocolError(f"{path} must be a non-empty string")
    return value


def _require_object(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise WorkerProtocolError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise WorkerProtocolError(f"{path} keys must be strings")
    return value


def _validate_ready(message: Mapping[str, object]) -> None:
    _require_nonempty_string(message.get("project_id"), "ready.project_id")
    _require_nonempty_string(message.get("backend"), "ready.backend")
    _require_nonempty_string(message.get("backend_version"), "ready.backend_version")
    languages = message.get("languages")
    if not isinstance(languages, list) or any(not isinstance(item, str) or not item for item in languages):
        raise WorkerProtocolError("ready.languages must be a string list")


def _validate_request(message: Mapping[str, object]) -> None:
    _require_nonempty_string(message.get("id"), "request.id")
    operation = _require_nonempty_string(message.get("op"), "request.op")
    if operation not in SEMANTIC_OPERATIONS:
        raise WorkerProtocolError(f"request.op is unsupported: {operation}")
    _require_object(message.get("params"), "request.params")


def _validate_response(message: Mapping[str, object]) -> None:
    _require_nonempty_string(message.get("id"), "response.id")
    ok = message.get("ok")
    if type(ok) is not bool:
        raise WorkerProtocolError("response.ok must be boolean")
    if ok:
        _require_object(message.get("result"), "response.result")
        return

    error = _require_object(message.get("error"), "response.error")
    _require_nonempty_string(error.get("code"), "response.error.code")
    _require_nonempty_string(error.get("message"), "response.error.message")
    if type(error.get("retryable")) is not bool:
        raise WorkerProtocolError("response.error.retryable must be boolean")
    _require_object(error.get("details"), "response.error.details")


def _validate_message(message: object) -> dict[str, object]:
    normalized = _require_object(message, "message")
    protocol = normalized.get("protocol")
    if type(protocol) is not int or protocol != WORKER_PROTOCOL_VERSION:
        raise WorkerProtocolError(
            f"protocol must be {WORKER_PROTOCOL_VERSION}; got {protocol!r}"
        )

    message_type = normalized.get("type")
    if message_type == "ready":
        _validate_ready(normalized)
    elif message_type == "request":
        _validate_request(normalized)
    elif message_type == "response":
        _validate_response(normalized)
    else:
        raise WorkerProtocolError(f"unknown message type: {message_type!r}")
    return normalized


def encode_message(message: Mapping[str, Any]) -> bytes:
    normalized = _validate_message(dict(message))
    try:
        payload = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkerProtocolError(f"message is not JSON serializable: {exc}") from exc
    if len(payload) + 1 > MAX_WORKER_MESSAGE_BYTES:
        raise WorkerProtocolError(
            f"worker message exceeds {MAX_WORKER_MESSAGE_BYTES} bytes"
        )
    return payload + b"\n"


def decode_message(line: bytes) -> dict[str, object]:
    if len(line) > MAX_WORKER_MESSAGE_BYTES:
        raise WorkerProtocolError(
            f"worker message exceeds {MAX_WORKER_MESSAGE_BYTES} bytes"
        )
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkerProtocolError("worker message is not valid UTF-8") from exc
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkerProtocolError("worker message is not valid JSON") from exc
    return _validate_message(message)
