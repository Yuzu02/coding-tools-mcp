from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from scripts.launcher.app import LauncherDependencies, run_services
from scripts.launcher.config import ServiceConfig, resolve_config
from scripts.launcher.processes import ManagedProcess
from scripts.launcher.tunnel import TunnelRuntime


ROOT = Path(__file__).resolve().parents[1]


def _make_repository(root: Path) -> Path:
    repository = root / "coding-tools-mcp"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return repository


@contextmanager
def configured(extra: list[str]) -> Iterator[ServiceConfig]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repository = _make_repository(root)
        workspace = root / "workspace"
        workspace.mkdir()
        arguments = [
            "--workspace",
            str(workspace),
            "--mcp-repository",
            str(repository),
            "--logs-root",
            str(root / "logs"),
            "--no-env-file",
        ]
        arguments.extend(extra)
        yield resolve_config(arguments, environ={}, repo_root=repository)


class FakePopen:
    def __init__(self, *, pid: int, exit_code: int | None = None) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.returncode = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class FakeDependencies:
    def __init__(
        self,
        *,
        child_exit: tuple[str, int] | None = None,
    ) -> None:
        self.commands: list[list[str]] = []
        self.started: list[str] = []
        self.start_kwargs: dict[str, dict[str, object]] = {}
        self.terminated: list[str] = []
        self.closed: list[str] = []
        self.events: list[str] = []
        self.doctor_calls = 0
        self.capture_calls = 0
        self.child_exit = child_exit
        self.processes: dict[str, ManagedProcess] = {}

    def command_runner(
        self,
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        copied = list(argv)
        self.commands.append(copied)
        stdout = "--enable-view-image\n"
        if len(copied) == 2 and copied[1] == "--version":
            stdout = f"{Path(copied[0]).name} 1.0\n"
        return subprocess.CompletedProcess(copied, 0, stdout, "")

    def process_starter(self, name: str, _argv: list[str], **kwargs: object) -> ManagedProcess:
        self.started.append(name)
        self.start_kwargs[name] = dict(kwargs)
        self.events.append(f"start:{name}")
        process = ManagedProcess(
            name=name,
            process=FakePopen(pid=100 + len(self.started)),  # type: ignore[arg-type]
            stdout_path=Path(kwargs["stdout_path"]),
            stderr_path=Path(kwargs["stderr_path"]),
            stdout_handle=io.BytesIO(),
            stderr_handle=io.BytesIO(),
        )
        self.processes[name] = process
        return process

    def tcp_waiter(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("ready:mcp")
        return

    def tunnel_preparer(
        self,
        config: ServiceConfig,
        artifacts: object,
        **_kwargs: object,
    ) -> TunnelRuntime | None:
        if config.tunnel.mode == "disabled":
            return None
        health_url = getattr(artifacts, "tunnel_health_url")
        return TunnelRuntime(
            client=config.tunnel_client,
            run_args=[config.tunnel_client, "run", "--profile", "dev"],
            doctor_args=[config.tunnel_client, "doctor", "--profile", "dev", "--json"],
            health_url_file=health_url,
        )

    def tunnel_doctor(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.doctor_calls += 1
        self.events.append("doctor")
        return {"ok": True}

    def tunnel_waiter(self, *_args: object, **_kwargs: object) -> str:
        self.events.append("ready:tunnel")
        return "http://127.0.0.1:8080"

    def supervisor(self, *_args: object, **_kwargs: object) -> tuple[str, int] | None:
        return self.child_exit

    def terminator(self, process: ManagedProcess, **_kwargs: object) -> None:
        self.terminated.append(process.name)
        fake = process.process
        if fake.poll() is None:
            fake.exit_code = -15
            fake.returncode = -15

    def closer(self, process: ManagedProcess) -> None:
        self.closed.append(process.name)
        process.stdout_handle.close()
        process.stderr_handle.close()

    def capture(self, *_args: object, **_kwargs: object) -> list[str]:
        self.capture_calls += 1
        return []

    def dependencies(self) -> LauncherDependencies:
        return LauncherDependencies(
            command_runner=self.command_runner,
            process_starter=self.process_starter,
            tcp_waiter=self.tcp_waiter,
            tunnel_preparer=self.tunnel_preparer,
            tunnel_doctor=self.tunnel_doctor,
            tunnel_waiter=self.tunnel_waiter,
            supervisor=self.supervisor,
            terminator=self.terminator,
            closer=self.closer,
            tunnel_diagnostic_capture=self.capture,
            port_in_use=lambda _host, _port: False,
            emit=lambda _message: None,
        )


class LauncherIntegrationTests(unittest.TestCase):
    def test_cli_help_uses_process_arguments_without_requiring_workspace(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "start_services.py"), "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--workspace", completed.stdout)

    def test_dry_run_does_not_sync_or_start_children(self) -> None:
        fake = FakeDependencies()
        with configured(["--dry-run", "--no-tunnel"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 0)
            self.assertEqual(fake.commands, [])
            self.assertEqual(fake.started, [])

    def test_sync_only_runs_uv_sync_and_exits(self) -> None:
        fake = FakeDependencies()
        with configured(["--sync-only", "--no-tunnel"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 0)
            self.assertEqual(fake.commands[0][:3], ["uv", "sync", "--project"])
            self.assertEqual(fake.started, [])

    def test_doctor_only_starts_mcp_before_validation_and_cleans_it_up(self) -> None:
        fake = FakeDependencies()
        with configured(["--doctor-only", "--tunnel-profile", "dev", "--no-sync"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 0)
            self.assertEqual(fake.doctor_calls, 1)
            self.assertEqual(fake.started, ["mcp"])
            self.assertEqual(fake.events, ["start:mcp", "ready:mcp", "doctor"])
            self.assertEqual(fake.terminated, ["mcp"])

    def test_tunnel_doctor_runs_after_mcp_readiness_before_tunnel_start(self) -> None:
        fake = FakeDependencies(child_exit=("tunnel", 0))
        with configured(["--tunnel-profile", "dev", "--no-sync"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 1)
            self.assertEqual(
                fake.events[:4],
                ["start:mcp", "ready:mcp", "doctor", "start:tunnel"],
            )

    def test_tunnel_exit_stops_tunnel_then_mcp_and_returns_failure(self) -> None:
        fake = FakeDependencies(child_exit=("tunnel", 0))
        with configured(["--tunnel-profile", "dev", "--no-sync"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 1)
            self.assertEqual(fake.started, ["mcp", "tunnel"])
            self.assertEqual(fake.terminated, ["tunnel", "mcp"])
            self.assertEqual(fake.capture_calls, 1)

    def test_no_tunnel_starts_only_mcp_and_clean_stop_returns_zero(self) -> None:
        fake = FakeDependencies(child_exit=None)
        with configured(["--no-tunnel", "--no-sync"]) as config:
            code = run_services(config, fake.dependencies())

            self.assertEqual(code, 0)
            self.assertEqual(fake.started, ["mcp"])
            self.assertEqual(fake.terminated, ["mcp"])
            self.assertEqual(fake.start_kwargs["mcp"]["cwd"], config.mcp_repository)

    def test_run_manifest_finishes_with_child_failure(self) -> None:
        fake = FakeDependencies(child_exit=("mcp", 9))
        with configured(["--no-tunnel", "--no-sync"]) as config:
            code = run_services(config, fake.dependencies())
            manifests = list(config.logs_root.glob("*/run.json"))

            self.assertEqual(code, 9)
            self.assertEqual(len(manifests), 1)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "failed")
            self.assertEqual(payload["exitCode"], 9)
            self.assertEqual(payload["failure"]["stage"], "runtime")


if __name__ == "__main__":
    unittest.main()
