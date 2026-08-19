from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from .errors import ToolFailure


REQUEST_ID_MAX_LENGTH = 128
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def validate_request_id(
    raw: object,
    *,
    field_name: str = "request_id",
    required: bool = False,
) -> str | None:
    """Validate the common idempotency-key vocabulary for mutating APIs."""

    if raw is None and not required:
        return None
    if not isinstance(raw, str) or not REQUEST_ID_RE.fullmatch(raw):
        raise ToolFailure(
            "INVALID_ARGUMENT",
            f"{field_name} must be 1-{REQUEST_ID_MAX_LENGTH} characters using letters, digits, '.', '_', ':', or '-'.",
            category="validation",
        )
    return raw


def request_fingerprint(payload: Mapping[str, object]) -> str:
    """Return a stable SHA-256 fingerprint for one idempotent request shape."""

    try:
        canonical = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolFailure(
            "INVALID_ARGUMENT",
            "request fingerprint input must be JSON serializable.",
            category="validation",
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "REQUEST_ID_MAX_LENGTH",
    "REQUEST_ID_RE",
    "request_fingerprint",
    "validate_request_id",
]
