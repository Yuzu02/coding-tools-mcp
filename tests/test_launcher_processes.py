from __future__ import annotations

import io
import os
import signal
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.launcher.processes import (
    ManagedProcess,
    ProcessError,
    close_process,
    normalized_child_exit,
    start_process,
    supervise,
    terminate_process_tree,
    wait_for_tcp,
)


class FakePopen:
    def __init__(self, exit_code: int | None = None, *, pid: int = 4321) -> None:
        self.exit_code = exit_code
        self.returncode = exit_code
        self.pid = pid
        self.signals: list[int] = []
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.exit_code

    def send_signal(self, signal_number: int) -> None:
        self.signals.append(signal_number)

    def terminate(self) -> None:
        self.signals.append(-1)

    def kill(self) -> None:
        self.signals.append(-9)


def fake_managed(exit_code: int | None = None, *, name: str = "child") -> ManagedProcess:
    return ManagedProcess(
        name=name,
        process=FakePopen(exit_code),  # type: ignore[arg-type]
        stdout_path=Path("stdout.log"),
        stderr_path=Path("stderr.log"),
        stdout_handle=io.BytesIO(),
        stderr_handle=io.BytesIO(),
    )


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class LauncherProcessTests(unittest.TestCase):
    def test_normalizes_unexpected_clean_child_exit(self) -> None:
        self.assertEqual(normalized_child_exit(0), 1)
        self.assertEqual(normalized_child_exit(7), 7)

    def test_wait_for_tcp_stops_when_child_exits(self) -> None:
        child = fake_managed(4)

        with self.assertRaisesRegex(ProcessError, "exited before readiness"):
            wait_for_tcp(
                child,
                "127.0.0.1",
                unused_port(),
                timeout=1,
                poll_interval=0.01,
            )

    def test_wait_for_tcp_accepts_listening_endpoint(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = int(listener.getsockname()[1])

            wait_for_tcp(
                fake_managed(),
                "127.0.0.1",
                port,
                timeout=1,
                poll_interval=0.01,
            )

    def test_start_process_uses_new_process_group_and_binary_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakePopen()
            with patch("scripts.launcher.processes.subprocess.Popen", return_value=fake) as popen:
                managed = start_process(
                    "child",
                    ["python", "-V"],
                    cwd=root,
                    environment={"PATH": "bin"},
                    stdout_path=root / "out.log",
                    stderr_path=root / "err.log",
                )

            kwargs = popen.call_args.kwargs
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            if os.name == "nt":
                self.assertTrue(
                    kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                self.assertTrue(kwargs["start_new_session"])
            self.assertFalse(managed.stdout_handle.closed)
            close_process(managed)
            self.assertTrue(managed.stdout_handle.closed)
            self.assertTrue(managed.stderr_handle.closed)

    def test_supervise_returns_first_exited_child(self) -> None:
        mcp = fake_managed(None, name="mcp")
        tunnel = fake_managed(0, name="tunnel")

        self.assertEqual(
            supervise(
                [mcp, tunnel],
                poll_interval=0.001,
                stop_requested=lambda: False,
            ),
            ("tunnel", 0),
        )

    def test_supervise_returns_none_when_stop_is_requested(self) -> None:
        self.assertIsNone(
            supervise(
                [fake_managed()],
                poll_interval=0.001,
                stop_requested=lambda: True,
            )
        )

    def test_terminate_process_tree_is_noop_for_exited_child(self) -> None:
        managed = fake_managed(0)
        fake = managed.process

        terminate_process_tree(managed, timeout=0.01)

        self.assertEqual(fake.signals, [])

    @unittest.skipUnless(os.name == "nt", "Windows process-group contract")
    def test_windows_cleanup_escalates_to_taskkill_after_timeout(self) -> None:
        managed = fake_managed()
        fake = managed.process
        with patch("scripts.launcher.processes.subprocess.run") as run:
            terminate_process_tree(managed, timeout=0.001)

        self.assertIn(signal.CTRL_BREAK_EVENT, fake.signals)
        run.assert_called_once()
        self.assertIn("taskkill", run.call_args.args[0][0].lower())
        self.assertIn("/T", run.call_args.args[0])
        self.assertIn("/F", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
