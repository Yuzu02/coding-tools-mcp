"""Application state machine for the multiplatform services launcher."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import ServiceConfig, scrub_mcp_environment
from .diagnostics import RunManifest, allocate_run_artifacts, bounded_log_tail
from .processes import (
    ManagedProcess,
    ProcessError,
    close_process,
    normalized_child_exit,
    start_process,
    supervise,
    terminate_process_tree,
    wait_for_tcp,
)
from .tunnel import (
    TunnelError,
    TunnelRuntime,
    capture_tunnel_diagnostics,
    prepare_tunnel,
    run_tunnel_doctor,
    wait_for_tunnel_ready,
)


class LauncherError(RuntimeError):
    """Raised when launcher validation or an external command fails."""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class LauncherDependencies:
    """Injectable boundaries used by the orchestration tests."""

    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    process_starter: Callable[..., ManagedProcess] = start_process
    tcp_waiter: Callable[..., None] = wait_for_tcp
    tunnel_preparer: Callable[..., TunnelRuntime | None] = prepare_tunnel
    tunnel_doctor: Callable[..., dict[str, object]] = run_tunnel_doctor
    tunnel_waiter: Callable[..., str] = wait_for_tunnel_ready
    supervisor: Callable[..., tuple[str, int] | None] = supervise
    terminator: Callable[..., None] = terminate_process_tree
    closer: Callable[[ManagedProcess], None] = close_process
    tunnel_diagnostic_capture: Callable[..., list[str]] = capture_tunnel_diagnostics
    port_in_use: Callable[[str, int], bool] = _default_port_in_use
    emit: Callable[[str], None] = print


@dataclass
class _StopState:
    requested: bool = False
    force: bool = False
    signal_count: int = 0

    def request(self) -> None:
        self.signal_count += 1
        self.requested = True
        if self.signal_count > 1:
            self.force = True


@contextmanager
def _signal_guard(state: _StopState) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[int, Any] = {}

    def handler(_signum: int, _frame: object) -> None:
        state.request()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, handler)
        except (ValueError, OSError, AttributeError):
            continue
    try:
        yield
    finally:
        for signal_number, prior in previous.items():
            try:
                signal.signal(signal_number, prior)
            except (ValueError, OSError, AttributeError):
                pass


def _run_external(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    stage: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(argv),
            cwd=str(cwd),
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise LauncherError(f"{stage} could not start {argv[0]}: {exc}") from exc
    if completed.returncode != 0:
        raise LauncherError(f"{stage} failed with exit code {completed.returncode}")
    return completed


def _validate_tools(
    config: ServiceConfig,
    runner: CommandRunner,
    mcp_environment: Mapping[str, str],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    uv_version = _run_external(
        runner,
        [config.uv, "--version"],
        cwd=config.mcp_repository,
        environment=mcp_environment,
        stage="uv version probe",
    )
    versions["uv"] = (uv_version.stdout or uv_version.stderr or "unknown").strip().splitlines()[0]

    help_args = [
        config.uv,
        "run",
        "--project",
        str(config.mcp_repository),
        "--locked",
        "python",
        "-m",
        "coding_tools_mcp",
        "--help",
    ]
    _run_external(
        runner,
        help_args,
        cwd=config.mcp_repository,
        environment=mcp_environment,
        stage="MCP capability probe",
    )
    versions["coding-tools-mcp"] = "checkout"

    if config.tunnel.mode != "disabled":
        tunnel_version = _run_external(
            runner,
            [config.tunnel_client, "--version"],
            cwd=config.workspace,
            environment=config.process_environment,
            stage="tunnel-client version probe",
        )
        versions["tunnel-client"] = (
            tunnel_version.stdout or tunnel_version.stderr or "unknown"
        ).strip().splitlines()[0]
    return versions


def _known_secret_values(config: ServiceConfig) -> tuple[str, ...]:
    names = {"CONTROL_PLANE_API_KEY", "OPENAI_API_KEY"}
    reference = config.tunnel.api_key_ref
    if reference and reference.startswith("env:"):
        names.add(reference.split(":", 1)[1])
    return tuple(
        value
        for name in names
        if (value := config.process_environment.get(name))
    )


def _record_child_exit(manifest: RunManifest, process: ManagedProcess) -> None:
    exit_code = process.process.poll()
    if exit_code is not None:
        manifest.record_exit(process.name, exit_code)


def _tail_hint(process: ManagedProcess) -> str:
    tail = bounded_log_tail(process.stderr_path, max_bytes=4096, max_lines=12)
    if not tail:
        return f"see {process.stderr_path}"
    return f"see {process.stderr_path}; tail: {tail}"


def run_services(
    config: ServiceConfig,
    dependencies: LauncherDependencies | None = None,
) -> int:
    """Run the configured services and return a deterministic launcher exit code."""

    deps = dependencies or LauncherDependencies()
    artifacts = allocate_run_artifacts(config.logs_root)
    manifest = RunManifest.start(
        artifacts,
        config.redacted_summary(),
        secret_values=_known_secret_values(config),
    )
    deps.emit(f"Diagnostics: {artifacts.run_directory}")

    stop_state = _StopState()
    exit_code = 1
    mcp_process: ManagedProcess | None = None
    tunnel_process: ManagedProcess | None = None
    tunnel_runtime: TunnelRuntime | None = None
    mcp_environment = scrub_mcp_environment(
        config.process_environment,
        config.tunnel.api_key_ref,
    )

    try:
        if config.dry_run:
            deps.emit(json.dumps(config.redacted_summary(), indent=2, sort_keys=True))
            exit_code = 0
            return exit_code

        if config.sync:
            manifest.transition("synchronizing")
            _run_external(
                deps.command_runner,
                config.sync_argv(),
                cwd=config.mcp_repository,
                environment=mcp_environment,
                stage="uv sync",
            )

        manifest.transition("validating")
        versions = _validate_tools(config, deps.command_runner, mcp_environment)
        manifest.record_tools(versions)

        if config.sync_only:
            exit_code = 0
            return exit_code

        tunnel_runtime = deps.tunnel_preparer(
            config,
            artifacts,
            runner=deps.command_runner,
        )

        if deps.port_in_use(config.host, config.port):
            raise LauncherError(
                f"port {config.host}:{config.port} is already accepting connections"
            )

        manifest.transition("starting-mcp")
        mcp_process = deps.process_starter(
            "mcp",
            config.mcp_argv(),
            cwd=config.mcp_repository,
            environment=mcp_environment,
            stdout_path=artifacts.mcp_stdout,
            stderr_path=artifacts.mcp_stderr,
        )
        manifest.record_process("mcp", mcp_process.process.pid)
        manifest.transition("waiting-for-mcp")
        deps.tcp_waiter(
            mcp_process,
            config.host,
            config.port,
            timeout=config.startup_timeout,
            poll_interval=config.poll_interval,
        )
        manifest.record_ready("mcp")
        processes = [mcp_process]

        if tunnel_runtime is not None:
            deps.tunnel_doctor(
                tunnel_runtime,
                environment=config.process_environment,
                runner=deps.command_runner,
            )
            if config.doctor_only:
                exit_code = 0
                return exit_code

            manifest.transition("starting-tunnel")
            tunnel_process = deps.process_starter(
                "tunnel",
                tunnel_runtime.run_args,
                cwd=config.workspace,
                environment=config.process_environment,
                stdout_path=artifacts.tunnel_stdout,
                stderr_path=artifacts.tunnel_stderr,
            )
            manifest.record_process("tunnel", tunnel_process.process.pid)
            manifest.transition("waiting-for-tunnel")
            deps.tunnel_waiter(
                tunnel_process.process,
                tunnel_runtime.health_url_file,
                timeout=config.startup_timeout,
                poll_interval=config.poll_interval,
            )
            manifest.record_ready("tunnel")
            processes.append(tunnel_process)

        manifest.transition("running")
        with _signal_guard(stop_state):
            result = deps.supervisor(
                processes,
                poll_interval=config.poll_interval,
                stop_requested=lambda: stop_state.requested,
            )
        if result is None:
            exit_code = 130 if stop_state.requested else 0
        else:
            child_name, child_exit = result
            manifest.record_failure(
                "runtime",
                f"{child_name} exited unexpectedly with code {child_exit}",
            )
            exit_code = normalized_child_exit(child_exit)
        return exit_code
    except KeyboardInterrupt:
        stop_state.request()
        exit_code = 130
        manifest.record_failure("interrupt", "launcher interrupted")
        return exit_code
    except (LauncherError, ProcessError, TunnelError, OSError) as exc:
        stage = str(manifest.payload.get("state") or "launcher")
        message = str(exc)
        if mcp_process is not None and mcp_process.process.poll() is not None:
            message = f"{message}; {_tail_hint(mcp_process)}"
        if tunnel_process is not None and tunnel_process.process.poll() is not None:
            message = f"{message}; {_tail_hint(tunnel_process)}"
        manifest.record_failure(stage, message)
        deps.emit(f"ERROR: {message}")
        exit_code = 1
        return exit_code
    finally:
        if tunnel_runtime is not None and tunnel_process is not None:
            manifest.transition("capturing-diagnostics")
            try:
                errors = deps.tunnel_diagnostic_capture(
                    tunnel_runtime,
                    artifacts,
                    environment=config.process_environment,
                    log_minutes=config.tunnel_log_minutes,
                    runner=deps.command_runner,
                )
                if errors:
                    manifest.record_diagnostic_errors(errors)
            except Exception as exc:  # cleanup must continue
                manifest.record_diagnostic_errors(
                    [f"unexpected diagnostic capture failure: {exc}"]
                )

        if tunnel_process is not None or mcp_process is not None:
            manifest.transition("stopping")
        for managed in (tunnel_process, mcp_process):
            if managed is None:
                continue
            try:
                deps.terminator(
                    managed,
                    timeout=config.shutdown_timeout,
                    force=stop_state.force,
                )
            except Exception as exc:  # cleanup must continue
                manifest.record_failure("cleanup", f"failed to stop {managed.name}: {exc}")
            _record_child_exit(manifest, managed)
            try:
                deps.closer(managed)
            except Exception as exc:  # cleanup must continue
                manifest.record_failure("cleanup", f"failed to close {managed.name}: {exc}")
        if tunnel_runtime is not None:
            tunnel_runtime.cleanup()
        manifest.finish(exit_code, interrupted=exit_code == 130)
