"""OpenAI tunnel-client profile handling, readiness, and diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import ServiceConfig
from .diagnostics import (
    RunArtifacts,
    atomic_write_json,
    download_http_artifact,
)


class TunnelError(RuntimeError):
    """Raised when tunnel preparation, validation, or readiness fails."""


class PollableProcess(Protocol):
    def poll(self) -> int | None: ...


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass
class TunnelRuntime:
    """Resolved tunnel commands and temporary resources for one run."""

    client: str
    run_args: list[str]
    doctor_args: list[str]
    health_url_file: Path
    generated_directory: Path | None = None
    keep_generated_profile: bool = False
    allow_missing_oauth_metadata: bool = False

    def cleanup(self) -> None:
        if self.generated_directory is None or self.keep_generated_profile:
            return
        shutil.rmtree(self.generated_directory, ignore_errors=True)
        self.generated_directory = None


def _profile_arguments(config: ServiceConfig) -> list[str]:
    selection = config.tunnel
    if selection.mode == "profile":
        arguments = ["--profile", selection.profile or "coding-tools-dev"]
        if selection.profile_dir is not None:
            arguments.extend(("--profile-dir", str(selection.profile_dir)))
        return arguments
    if selection.mode == "profile-file":
        if selection.profile_file is None:
            raise TunnelError("profile-file mode has no resolved profile path")
        return ["--profile-file", str(selection.profile_file)]
    raise TunnelError(f"profile arguments are unavailable for mode {selection.mode}")


def _generated_profile_name(config: ServiceConfig) -> str:
    configured = config.tunnel.generated_profile_name
    if configured:
        return configured
    identifier = config.tunnel.tunnel_id or "tunnel"
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]
    return f"generated-{digest}"


def _run_command(
    runner: CommandRunner,
    argv: list[str],
    *,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=dict(environment),
        )
    except OSError as exc:
        raise TunnelError(f"could not execute {argv[0]}: {exc}") from exc


def _prepare_generated_profile(
    config: ServiceConfig,
    artifacts: RunArtifacts,
    runner: CommandRunner,
) -> tuple[Path, Path | None]:
    selection = config.tunnel
    tunnel_id = selection.tunnel_id
    api_key_ref = selection.api_key_ref
    if not tunnel_id or not api_key_ref:
        raise TunnelError("generated profile mode requires tunnel ID and API-key reference")

    profile_name = _generated_profile_name(config)
    destination = selection.write_profile
    generated_directory: Path | None
    staging_directory: Path | None = None

    if destination is None:
        generated_directory = Path(
            tempfile.mkdtemp(
                prefix="tunnel-profile-",
                dir=artifacts.run_directory,
            )
        )
        try:
            generated_directory.chmod(0o700)
        except OSError:
            pass
        profile_directory = generated_directory
        profile_path = profile_directory / f"{profile_name}.yaml"
    elif destination.suffix.lower() == ".yaml":
        destination.parent.mkdir(parents=True, exist_ok=True)
        profile_directory = destination.parent
        profile_name = destination.stem
        profile_path = destination
        generated_directory = None
    else:
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix="tunnel-profile-staging-",
                dir=artifacts.run_directory,
            )
        )
        profile_directory = staging_directory
        profile_path = destination
        generated_directory = None

    generated_source = profile_directory / f"{profile_name}.yaml"
    mcp_server_url = selection.mcp_server_url or f"http://{config.host}:{config.port}/mcp"
    init_args = [
        config.tunnel_client,
        "init",
        "--sample",
        "sample_mcp_remote_no_auth",
        "--profile",
        profile_name,
        "--profile-dir",
        str(profile_directory),
        "--tunnel-id",
        tunnel_id,
        "--mcp-server-url",
        mcp_server_url,
        "--control-plane-base-url",
        selection.control_plane_base_url,
        "--control-plane-api-key-ref",
        api_key_ref,
        "--health-listen-addr",
        config.tunnel_health_listen_addr,
    ]
    if selection.control_plane_url_path:
        init_args.extend(("--control-plane-url-path", selection.control_plane_url_path))
    if selection.open_web_ui:
        init_args.append("--open-web-ui")
    if selection.force_profile_write:
        init_args.append("--force")

    completed = _run_command(
        runner,
        init_args,
        environment=config.process_environment,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise TunnelError(f"tunnel-client init failed with code {completed.returncode}: {detail[:500]}")
    if not generated_source.is_file():
        raise TunnelError(
            f"tunnel-client init completed without creating profile: {generated_source}"
        )

    if destination is not None and generated_source != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and selection.force_profile_write:
            destination.unlink()
        os.replace(generated_source, destination)
        profile_path = destination
    if staging_directory is not None:
        shutil.rmtree(staging_directory, ignore_errors=True)

    return profile_path, generated_directory


def prepare_tunnel(
    config: ServiceConfig,
    artifacts: RunArtifacts,
    runner: CommandRunner = subprocess.run,
) -> TunnelRuntime | None:
    """Resolve existing profiles or materialize one generated profile."""

    if config.tunnel.mode == "disabled":
        return None

    generated_directory: Path | None = None
    if config.tunnel.mode == "generated":
        profile_path, generated_directory = _prepare_generated_profile(
            config,
            artifacts,
            runner,
        )
        profile_args = ["--profile-file", str(profile_path)]
    else:
        profile_args = _profile_arguments(config)

    health_url_file = config.tunnel_health_url_file or artifacts.tunnel_health_url
    health_url_file.parent.mkdir(parents=True, exist_ok=True)
    health_url_file.unlink(missing_ok=True)
    doctor_args = [config.tunnel_client, "doctor", *profile_args, "--json"]
    run_args = [
        config.tunnel_client,
        "run",
        *profile_args,
        "--health.listen-addr",
        config.tunnel_health_listen_addr,
        "--health.url-file",
        str(health_url_file),
    ]
    return TunnelRuntime(
        client=config.tunnel_client,
        run_args=run_args,
        doctor_args=doctor_args,
        health_url_file=health_url_file,
        generated_directory=generated_directory,
        keep_generated_profile=config.keep_generated_profile,
        allow_missing_oauth_metadata=(
            config.tunnel.mode == "generated"
            and config.tunnel.mcp_server_url is None
        ),
    )


def run_tunnel_doctor(
    runtime: TunnelRuntime,
    *,
    environment: Mapping[str, str],
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Run tunnel-client doctor and return its machine-readable result."""

    completed = _run_command(runner, runtime.doctor_args, environment=environment)
    try:
        parsed = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise TunnelError(
                f"tunnel doctor failed with code {completed.returncode}: {detail[:500]}"
            ) from exc
        raise TunnelError("tunnel doctor returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise TunnelError("tunnel doctor JSON result must be an object")
    if completed.returncode != 0:
        failed_checks = parsed.get("failed_checks")
        if not (
            runtime.allow_missing_oauth_metadata
            and isinstance(failed_checks, list)
            and set(failed_checks) == {"oauth_metadata"}
        ):
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise TunnelError(
                f"tunnel doctor failed with code {completed.returncode}: {detail[:500]}"
            )
    return parsed


def wait_for_tunnel_ready(
    process: PollableProcess,
    url_file: Path,
    *,
    timeout: float,
    poll_interval: float,
) -> str:
    """Wait for the health URL file and a successful tunnel `/readyz` response."""

    deadline = time.monotonic() + timeout
    last_error = "health URL file was not created"
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise TunnelError(
                f"tunnel-client exited before readiness with code {exit_code}"
            )
        if url_file.is_file():
            try:
                base_url = url_file.read_text(encoding="utf-8").strip().rstrip("/")
                if base_url:
                    with urllib.request.urlopen(  # noqa: S310
                        f"{base_url}/readyz",
                        timeout=max(0.2, min(2.0, poll_interval * 4)),
                    ) as response:
                        if 200 <= response.status < 300:
                            return base_url
                        last_error = f"/readyz returned HTTP {response.status}"
            except (OSError, UnicodeError, urllib.error.URLError) as exc:
                last_error = str(exc)
        time.sleep(poll_interval)
    raise TunnelError(f"timed out waiting for tunnel readiness: {last_error}")


def _load_health_base(runtime: TunnelRuntime) -> str:
    try:
        base_url = runtime.health_url_file.read_text(encoding="utf-8").strip().rstrip("/")
    except (OSError, UnicodeError) as exc:
        raise TunnelError(f"could not read tunnel health URL: {exc}") from exc
    if not base_url:
        raise TunnelError("tunnel health URL file is empty")
    return base_url


def capture_tunnel_diagnostics(
    runtime: TunnelRuntime,
    artifacts: RunArtifacts,
    *,
    environment: Mapping[str, str],
    log_minutes: int,
    runner: CommandRunner = subprocess.run,
) -> list[str]:
    """Capture independent tunnel diagnostics without aborting later attempts."""

    errors: list[str] = []
    try:
        base_url = _load_health_base(runtime)
    except TunnelError as exc:
        errors.append(str(exc))
        atomic_write_json(artifacts.diagnostics_errors, {"errors": errors})
        return errors

    try:
        download_http_artifact(
            f"{base_url}/api/status",
            artifacts.tunnel_status,
            timeout=10,
        )
    except Exception as exc:  # diagnostics are explicitly best effort
        errors.append(f"status capture failed: {exc}")

    try:
        health_args = [
            runtime.client,
            "health",
            "--url",
            base_url,
            "--require-control-plane-poll",
            "--json",
        ]
        completed = _run_command(runner, health_args, environment=environment)
        if completed.returncode != 0:
            raise TunnelError(f"health command exited with code {completed.returncode}")
        payload: Any = json.loads(completed.stdout or "{}")
        atomic_write_json(artifacts.tunnel_health, payload)
    except Exception as exc:  # diagnostics are explicitly best effort
        errors.append(f"health capture failed: {exc}")

    try:
        download_http_artifact(
            f"{base_url}/api/logs/export?minutes={log_minutes}",
            artifacts.tunnel_events,
            timeout=30,
        )
    except Exception as exc:  # diagnostics are explicitly best effort
        errors.append(f"event archive capture failed: {exc}")

    if errors:
        atomic_write_json(artifacts.diagnostics_errors, {"errors": errors})
    return errors
