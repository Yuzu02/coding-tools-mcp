from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath


MAX_RECOVERY_REASON_CHARS = 512
_PATH_ARGUMENTS = frozenset({"path", "paths", "workdir", "cwd", "root", "workspace"})


def _looks_absolute_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _validate_logical_path_argument(name: str, value: object) -> None:
    if name not in _PATH_ARGUMENTS:
        return
    values: tuple[object, ...]
    if isinstance(value, (list, tuple)):
        values = tuple(value)
    else:
        values = (value,)
    for item in values:
        if isinstance(item, str) and _looks_absolute_path(item):
            raise ValueError(f"continuation {name} must not contain an absolute host path")


def continuation_call_tool(
    tool: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Build one logical tool continuation without host-derived addressing.

    Continuations are replay instructions, not authorization.  They carry only
    the logical state a remote client needs to make the next explicit call.
    """

    if not isinstance(tool, str) or not tool:
        raise ValueError("continuation tool must be a non-empty string")
    normalized: dict[str, object] = {}
    for name, value in arguments.items():
        if not isinstance(name, str):
            raise ValueError("continuation argument names must be strings")
        if name == "workdir" and value in {"", ".", None}:
            continue
        _validate_logical_path_argument(name, value)
        normalized[name] = value
    return {"tool": tool, "arguments": normalized}


def recovery_call_tool(
    tool: str,
    arguments: Mapping[str, object],
    reason: str,
) -> dict[str, object]:
    """Describe a safe next call after a failure without executing it."""

    call = continuation_call_tool(tool, arguments)
    bounded_reason = str(reason).strip()[:MAX_RECOVERY_REASON_CHARS]
    return {
        "kind": "call_tool",
        "tool": call["tool"],
        "arguments": call["arguments"],
        "reason": bounded_reason,
    }


__all__ = ["continuation_call_tool", "recovery_call_tool"]
