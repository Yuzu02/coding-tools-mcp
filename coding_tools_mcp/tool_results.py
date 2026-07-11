from __future__ import annotations

from typing import Any


MODEL_TEXT_LIMIT_BYTES = 16_384


def make_tool_result(
    tool_name: str,
    payload: dict[str, Any],
    *,
    is_error: bool,
    content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an MCP result without mirroring structured JSON into model text."""

    result_content = list(content or [])
    text = render_tool_text(tool_name, payload, is_error=is_error)
    if text:
        result_content.append({"type": "text", "text": _bounded_model_text(text, "Tool result")})
    return {"content": result_content, "structuredContent": payload, "isError": is_error}


def render_tool_text(tool_name: str, payload: dict[str, Any], *, is_error: bool) -> str:
    if is_error or payload.get("ok") is False:
        return _render_error(payload)
    renderer = _RENDERERS.get(tool_name)
    if renderer is not None:
        return renderer(payload)
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    status = payload.get("status")
    return f"{tool_name}: {status or 'completed'}."


def _render_error(payload: dict[str, Any]) -> str:
    raw_error = payload.get("error")
    error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
    code = str(error.get("code") or "TOOL_ERROR")
    message = str(error.get("message") or "Tool call failed.")
    lines = [f"{code}: {message}"]
    raw_details = error.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    retry_hint = details.get("retry_hint")
    if isinstance(retry_hint, str) and retry_hint:
        lines.append(f"Retry: {retry_hint}")
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            suggestion = item.get("suggested_fix") or item.get("suggested_next_command")
            if isinstance(suggestion, str) and suggestion:
                lines.append(f"Suggested action: {suggestion}")
    return "\n".join(lines)


def _render_server_info(payload: dict[str, Any]) -> str:
    name = payload.get("name") or payload.get("server_name") or "coding-tools-mcp"
    version = payload.get("version") or "unknown"
    workspace = payload.get("workspace") or payload.get("workspace_root") or "."
    return f"{name} {version}\nWorkspace: {workspace}"


def _render_exec_environment(payload: dict[str, Any]) -> str:
    raw_landlock = payload.get("landlock")
    landlock: dict[str, Any] = raw_landlock if isinstance(raw_landlock, dict) else {}
    state = "available" if landlock.get("available") else "unavailable"
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    suffix = "\n" + "\n".join(str(item) for item in warnings) if warnings else ""
    return f"Execution environment checked. Landlock: {state}.{suffix}"


def _render_cwd(payload: dict[str, Any]) -> str:
    return f"Default working directory: {payload.get('default_cwd', '.')}"


def _render_read_file(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return _bounded_model_text(content, "File content")
    return ""


def _render_list(payload: dict[str, Any]) -> str:
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else payload.get("files")
    if not isinstance(entries, list) or not entries:
        return "No entries found."
    lines: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or item.get("name")
        kind = item.get("type")
        lines.append(f"{path}{' [' + str(kind) + ']' if kind else ''}")
    if payload.get("truncated"):
        lines.append("… results truncated")
    return _bounded_model_text("\n".join(lines), "Directory listing")


def _render_search(payload: dict[str, Any]) -> str:
    matches = payload.get("matches")
    if not isinstance(matches, list) or not matches:
        return "No matches found."
    lines: list[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        path = match.get("path", "")
        line = match.get("line", "")
        column = match.get("column", "")
        preview = match.get("preview", "")
        location = f"{path}:{line}" + (f":{column}" if column else "")
        lines.append(f"{location}: {preview}")
        before = match.get("before")
        after = match.get("after")
        if isinstance(before, list):
            lines.extend(f"  {value}" for value in before)
        if isinstance(after, list):
            lines.extend(f"  {value}" for value in after)
    if payload.get("truncated"):
        lines.append("… results truncated")
    return _bounded_model_text("\n".join(lines), "Search results")


def _render_patch(payload: dict[str, Any]) -> str:
    prefix = "Patch validated" if payload.get("dry_run") else "Patch applied"
    files = payload.get("affected_files")
    count = len(files) if isinstance(files, list) else 0
    changes = f" (+{payload.get('additions', 0)} -{payload.get('removals', 0)})"
    summary = str(payload.get("summary") or "").strip()
    return f"{prefix} to {count} file{'s' if count != 1 else ''}{changes}." + (
        f"\n{summary}" if summary else ""
    )


def _render_exec(payload: dict[str, Any]) -> str:
    sections: list[str] = []
    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    preview = payload.get("preview")
    if isinstance(stdout, str) and stdout:
        sections.append(stdout)
    if isinstance(stderr, str) and stderr:
        sections.append(f"stderr:\n{stderr}")
    if not sections and isinstance(preview, str) and preview:
        sections.append(preview)
    if not sections:
        summary = payload.get("summary")
        status = payload.get("status", "completed")
        exit_code = payload.get("exit_code")
        sections.append(str(summary or f"Command {status}; exit code {exit_code}."))
    if payload.get("truncated"):
        sections.append("Output truncated; use read_output with the returned output_ref to read more.")
    return _bounded_model_text("\n".join(sections), "Command output")


def _render_read_output(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    return _bounded_model_text(content, "Session output") if isinstance(content, str) else ""


def _render_kill(payload: dict[str, Any]) -> str:
    return f"Session {payload.get('session_id', '')}: {payload.get('status', 'completed')}."


def _render_git_status(payload: dict[str, Any]) -> str:
    if not payload.get("is_repo", True):
        return "Not a Git repository."
    branch = payload.get("branch") or "detached"
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    lines = [f"## {branch}"]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"{entry.get('index_status', ' ')}{entry.get('worktree_status', ' ')} {entry.get('path', '')}"
        )
    if not entries:
        lines.append("Working tree clean.")
    return _bounded_model_text("\n".join(lines), "Git status")


def _render_key(payload: dict[str, Any], key: str, empty: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) and value else empty


def _render_git_log(payload: dict[str, Any]) -> str:
    raw_commits = payload.get("commits")
    commits: list[Any] = raw_commits if isinstance(raw_commits, list) else []
    if not commits:
        return "No commits found."
    return _bounded_model_text(
        "\n".join(
            f"{item.get('short_hash', '')} {item.get('subject', '')}"
            for item in commits
            if isinstance(item, dict)
        ),
        "Git log",
    )


def _render_git_blame(payload: dict[str, Any]) -> str:
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return "No blame lines found."
    rendered: list[str] = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        rendered.append(
            f"{item.get('line_number', item.get('line', ''))} "
            f"{item.get('short_hash', item.get('commit', ''))} "
            f"{item.get('content', '')}"
        )
    return _bounded_model_text("\n".join(rendered), "Git blame")


def _bounded_model_text(value: str, label: str) -> str:
    """Keep model-facing content compact while structuredContent stays complete."""

    encoded = value.encode("utf-8")
    if len(encoded) <= MODEL_TEXT_LIMIT_BYTES:
        return value
    suffix = f"\n… {label} preview truncated; content continues in structuredContent."
    for _ in range(2):
        preview_budget = max(0, MODEL_TEXT_LIMIT_BYTES - len(suffix.encode("utf-8")))
        preview = encoded[:preview_budget].decode("utf-8", errors="ignore")
        omitted = len(encoded) - len(preview.encode("utf-8"))
        suffix = f"\n… {label} preview truncated; {omitted} bytes remain in structuredContent."
    preview_budget = max(0, MODEL_TEXT_LIMIT_BYTES - len(suffix.encode("utf-8")))
    preview = encoded[:preview_budget].decode("utf-8", errors="ignore")
    return preview + suffix


def _render_image(payload: dict[str, Any]) -> str:
    dimensions = ""
    if payload.get("width") and payload.get("height"):
        dimensions = f", {payload['width']}×{payload['height']}"
    return f"Image: {payload.get('path', '')} ({payload.get('mime_type', 'unknown')}{dimensions})"


_RENDERERS = {
    "server_info": _render_server_info,
    "check_exec_environment": _render_exec_environment,
    "get_default_cwd": _render_cwd,
    "set_default_cwd": _render_cwd,
    "read_file": _render_read_file,
    "list_dir": _render_list,
    "list_files": _render_list,
    "search_text": _render_search,
    "apply_patch": _render_patch,
    "exec_command": _render_exec,
    "write_stdin": _render_exec,
    "kill_session": _render_kill,
    "read_output": _render_read_output,
    "git_status": _render_git_status,
    "git_diff": lambda payload: _bounded_model_text(
        _render_key(payload, "diff", "No diff."), "Git diff"
    ),
    "git_log": _render_git_log,
    "git_show": lambda payload: _bounded_model_text(
        _render_key(payload, "content", "No output."), "Git show output"
    ),
    "git_blame": _render_git_blame,
    "request_permissions": lambda payload: f"Permission request: {payload.get('status', 'completed')}.",
    "view_image": _render_image,
}
