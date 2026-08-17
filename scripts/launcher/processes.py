"""Cross-platform child-process supervision for the services launcher."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class ProcessError(RuntimeError):
    """Raised when child startup, readiness, or cleanup fails."""


@dataclass
class ManagedProcess:
    """A child process plus the binary log handles owned by the launcher."""

    name: str
    process: subprocess.Popen[bytes]
    stdout_path: Path
    stderr_path: Path
    stdout_handle: BinaryIO
    stderr_handle: BinaryIO


def start_process(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> ManagedProcess:
    """Start one child in a new process group with direct binary log capture."""

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = stdout_path.open("wb")
    stderr_handle = stderr_path.open("wb")
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": dict(environment),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(list(argv), **kwargs)  # type: ignore[arg-type]
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    return ManagedProcess(
        name=name,
        process=process,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
    )


def wait_for_tcp(
    process: ManagedProcess,
    host: str,
    port: int,
    *,
    timeout: float,
    poll_interval: float,
) -> None:
    """Wait until a TCP endpoint accepts a connection or the child exits."""

    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        exit_code = process.process.poll()
        if exit_code is not None:
            raise ProcessError(
                f"{process.name} exited before readiness with code {exit_code}"
            )
        try:
            with socket.create_connection(
                (host, port),
                timeout=max(0.1, min(0.5, poll_interval * 2)),
            ):
                return
        except OSError as exc:
            last_error = exc
        time.sleep(poll_interval)
    detail = f": {last_error}" if last_error else ""
    raise ProcessError(
        f"timed out waiting for {process.name} on {host}:{port}{detail}"
    )


def supervise(
    processes: Sequence[ManagedProcess],
    *,
    poll_interval: float,
    stop_requested: Callable[[], bool],
) -> tuple[str, int] | None:
    """Return the first child exit, or ``None`` when an external stop is requested."""

    while True:
        if stop_requested():
            return None
        for managed in processes:
            exit_code = managed.process.poll()
            if exit_code is not None:
                return managed.name, exit_code
        time.sleep(poll_interval)


def _wait_for_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _terminate_windows(process: subprocess.Popen[bytes], timeout: float, force: bool) -> None:
    if not force:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            try:
                process.terminate()
            except OSError:
                pass
        if _wait_for_exit(process, timeout):
            return
    subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _wait_for_exit(process, timeout)


def _terminate_posix(process: subprocess.Popen[bytes], timeout: float, force: bool) -> None:
    try:
        process_group = os.getpgid(process.pid)
    except OSError:
        return
    if not force:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
        if _wait_for_exit(process, timeout):
            return
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    _wait_for_exit(process, timeout)


def terminate_process_tree(
    managed: ManagedProcess,
    *,
    timeout: float,
    force: bool = False,
) -> None:
    """Terminate a child and its descendants using the platform process group."""

    if managed.process.poll() is not None:
        return
    if os.name == "nt":
        _terminate_windows(managed.process, timeout, force)
    else:
        _terminate_posix(managed.process, timeout, force)


def close_process(managed: ManagedProcess) -> None:
    """Flush and close the launcher-owned child log handles."""

    for handle in (managed.stdout_handle, managed.stderr_handle):
        if handle.closed:
            continue
        try:
            handle.flush()
        finally:
            handle.close()


def normalized_child_exit(exit_code: int) -> int:
    """Treat an unexpected clean child exit as launcher failure."""

    return 1 if exit_code == 0 else exit_code
