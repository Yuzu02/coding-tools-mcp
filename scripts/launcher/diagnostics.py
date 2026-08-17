"""Run-scoped diagnostics for the multiplatform services launcher."""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


OMITTED_CONFIGURATION_KEYS = {
    "argv",
    "command",
    "environment",
    "process_environment",
    "raw_argv",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RunArtifacts:
    """Filesystem paths owned by one launcher invocation."""

    run_directory: Path
    manifest: Path
    mcp_stdout: Path
    mcp_stderr: Path
    tunnel_stdout: Path
    tunnel_stderr: Path
    tunnel_health_url: Path
    tunnel_status: Path
    tunnel_health: Path
    tunnel_events: Path
    diagnostics_errors: Path


def allocate_run_artifacts(
    logs_root: Path,
    *,
    now: datetime | None = None,
) -> RunArtifacts:
    """Allocate a unique private run directory and all standard artifact paths."""

    root = logs_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    current = now or _utc_now()
    base = current.strftime("%Y%m%d-%H%M%S")
    run_directory: Path | None = None
    for attempt in range(1000):
        name = base if attempt == 0 else f"{base}-{attempt:02d}"
        candidate = root / name
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            continue
        run_directory = candidate
        break
    if run_directory is None:
        raise RuntimeError(f"could not allocate a run directory below {root}")

    return RunArtifacts(
        run_directory=run_directory,
        manifest=run_directory / "run.json",
        mcp_stdout=run_directory / "coding-tools-mcp.stdout.log",
        mcp_stderr=run_directory / "coding-tools-mcp.stderr.log",
        tunnel_stdout=run_directory / "tunnel-client.stdout.log",
        tunnel_stderr=run_directory / "tunnel-client.stderr.log",
        tunnel_health_url=run_directory / "tunnel-health.url",
        tunnel_status=run_directory / "tunnel-status.json",
        tunnel_health=run_directory / "tunnel-health.json",
        tunnel_events=run_directory / "tunnel-events.tar.gz",
        diagnostics_errors=run_directory / "diagnostics-errors.json",
    )


def atomic_write_json(path: Path, value: object) -> None:
    """Write one UTF-8 JSON document atomically in the destination directory."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    serialized = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        temporary.write_text(serialized, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _redact_text(value: str, secret_values: tuple[str, ...]) -> str:
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _sanitize(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item, secret_values)
            for key, item in value.items()
            if str(key).lower() not in OMITTED_CONFIGURATION_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, secret_values) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value), secret_values)


@dataclass
class RunManifest:
    """Mutable launcher lifecycle record persisted after every state change."""

    artifacts: RunArtifacts
    payload: dict[str, Any]
    secret_values: tuple[str, ...]

    @classmethod
    def start(
        cls,
        artifacts: RunArtifacts,
        configuration: dict[str, object],
        *,
        secret_values: tuple[str, ...] = (),
    ) -> RunManifest:
        manifest = cls(
            artifacts=artifacts,
            secret_values=tuple(secret for secret in secret_values if secret),
            payload={
                "schemaVersion": 1,
                "startedAt": _timestamp(),
                "completedAt": None,
                "state": "starting",
                "exitCode": None,
                "runDirectory": str(artifacts.run_directory),
                "configuration": _sanitize(configuration, secret_values),
                "processes": {},
                "readiness": {},
                "failure": None,
                "errors": [],
                "artifacts": {
                    "mcpStdout": str(artifacts.mcp_stdout),
                    "mcpStderr": str(artifacts.mcp_stderr),
                    "tunnelStdout": str(artifacts.tunnel_stdout),
                    "tunnelStderr": str(artifacts.tunnel_stderr),
                    "tunnelHealthUrl": str(artifacts.tunnel_health_url),
                },
            },
        )
        manifest._write()
        return manifest

    def _write(self) -> None:
        atomic_write_json(self.artifacts.manifest, self.payload)

    def transition(self, state: str) -> None:
        self.payload["state"] = state
        self._write()

    def record_process(self, name: str, pid: int) -> None:
        processes = self.payload.setdefault("processes", {})
        process = processes.setdefault(name, {})
        process["pid"] = pid
        process.setdefault("exitCode", None)
        self._write()

    def record_ready(self, name: str, *, at: datetime | None = None) -> None:
        ready_at = _timestamp(at)
        processes = self.payload.setdefault("processes", {})
        process = processes.setdefault(name, {})
        process["readyAt"] = ready_at
        readiness = self.payload.setdefault("readiness", {})
        readiness[name] = ready_at
        self._write()

    def record_exit(self, name: str, exit_code: int) -> None:
        processes = self.payload.setdefault("processes", {})
        process = processes.setdefault(name, {})
        process["exitCode"] = exit_code
        process["exitedAt"] = _timestamp()
        self._write()

    def record_tools(self, versions: dict[str, str]) -> None:
        self.payload["tools"] = _sanitize(versions, self.secret_values)
        self._write()

    def record_failure(self, stage: str, message: str) -> None:
        failure = {
            "at": _timestamp(),
            "stage": stage,
            "message": _redact_text(message, self.secret_values),
        }
        if self.payload.get("failure") is None:
            self.payload["failure"] = failure
        errors = self.payload.setdefault("errors", [])
        errors.append(failure)
        self._write()

    def record_diagnostic_errors(self, errors: list[str]) -> None:
        sanitized = [
            {
                "at": _timestamp(),
                "message": _redact_text(message, self.secret_values),
            }
            for message in errors
        ]
        atomic_write_json(self.artifacts.diagnostics_errors, {"errors": sanitized})
        self.payload.setdefault("diagnosticErrors", []).extend(sanitized)
        self._write()

    def finish(self, exit_code: int, *, interrupted: bool = False) -> None:
        self.payload["completedAt"] = _timestamp()
        self.payload["exitCode"] = exit_code
        self.payload["state"] = "stopped" if exit_code in {0, 130} else "failed"
        if interrupted:
            self.payload["interrupted"] = True
        self._write()


def bounded_log_tail(
    path: Path,
    *,
    max_bytes: int = 8192,
    max_lines: int = 40,
) -> str:
    """Return a bounded UTF-8 replacement-decoded tail of a binary log file."""

    if max_bytes <= 0 or max_lines <= 0 or not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        data = stream.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-max_lines:])


def download_http_artifact(
    url: str,
    destination: Path,
    *,
    timeout: float = 10.0,
) -> None:
    """Stream an HTTP response to a temporary file and atomically install it."""

    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                while chunk := response.read(64 * 1024):
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
