from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from scripts.launcher.config import ServiceConfig, resolve_config
from scripts.launcher.diagnostics import allocate_run_artifacts
from scripts.launcher.tunnel import (
    TunnelError,
    capture_tunnel_diagnostics,
    prepare_tunnel,
    run_tunnel_doctor,
    wait_for_tunnel_ready,
)


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
        profile = root / "profile.yaml"
        profile.write_text("config_version: 1\n", encoding="utf-8")
        arguments = [
            "--workspace",
            str(workspace),
            "--mcp-repository",
            str(repository),
            "--no-env-file",
        ]
        arguments.extend(
            str(profile)
            if item == "PROFILE_FILE"
            else str(root / "saved.yaml")
            if item == "PERSISTENT_PROFILE"
            else item
            for item in extra
        )
        yield resolve_config(arguments, environ={}, repo_root=repository)


class RecordingRunner:
    def __init__(self, *, returncode: int = 0, stdout: str = "{}") -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        copied = list(argv)
        self.calls.append((copied, dict(kwargs)))
        if len(copied) > 1 and copied[1] == "init" and self.returncode == 0:
            profile_dir = Path(copied[copied.index("--profile-dir") + 1])
            profile_name = copied[copied.index("--profile") + 1]
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / f"{profile_name}.yaml").write_text(
                "config_version: 1\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(copied, self.returncode, self.stdout, "failure")


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/readyz":
            payload = b"ready"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/api/status":
            payload = json.dumps({"status": "connected"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/api/logs/export"):
            payload = b"archive"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def ready_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


class FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class LauncherTunnelTests(unittest.TestCase):
    def test_profile_file_mode_passes_profile_file_to_doctor_and_run(self) -> None:
        with configured(["--tunnel-profile-file", "PROFILE_FILE"]) as config:
            with tempfile.TemporaryDirectory() as tmp:
                artifacts = allocate_run_artifacts(Path(tmp))
                runtime = prepare_tunnel(config, artifacts, runner=RecordingRunner())

                self.assertIsNotNone(runtime)
                assert runtime is not None
                self.assertIn("--profile-file", runtime.doctor_args)
                self.assertIn(str(config.tunnel.profile_file), runtime.doctor_args)
                self.assertIn("--profile-file", runtime.run_args)
                self.assertIn("--health.url-file", runtime.run_args)

    def test_named_profile_preserves_optional_profile_directory(self) -> None:
        with configured(
            ["--tunnel-profile", "dev", "--tunnel-profile-dir", ".profiles"]
        ) as config:
            with tempfile.TemporaryDirectory() as tmp:
                runtime = prepare_tunnel(
                    config,
                    allocate_run_artifacts(Path(tmp)),
                    runner=RecordingRunner(),
                )

                assert runtime is not None
                self.assertEqual(runtime.doctor_args[2:4], ["--profile", "dev"])
                self.assertIn("--profile-dir", runtime.doctor_args)

    def test_generated_mode_calls_init_and_cleans_temporary_profile(self) -> None:
        runner = RecordingRunner()
        with configured(
            [
                "--tunnel-id",
                "tunnel_example",
                "--control-plane-api-key-ref",
                "env:CONTROL_PLANE_API_KEY",
            ]
        ) as config:
            with tempfile.TemporaryDirectory() as tmp:
                artifacts = allocate_run_artifacts(Path(tmp))
                runtime = prepare_tunnel(config, artifacts, runner=runner)

                assert runtime is not None
                init = runner.calls[0][0]
                self.assertEqual(init[1], "init")
                self.assertEqual(
                    init[init.index("--sample") + 1],
                    "sample_mcp_remote_no_auth",
                )
                self.assertIn("env:CONTROL_PLANE_API_KEY", init)
                self.assertNotIn("secret", " ".join(init))
                self.assertTrue(runtime.allow_missing_oauth_metadata)
                self.assertIsNotNone(runtime.generated_directory)
                generated = runtime.generated_directory
                assert generated is not None
                self.assertTrue(generated.exists())
                runtime.cleanup()
                self.assertFalse(generated.exists())

    def test_persistent_generated_profile_is_retained(self) -> None:
        runner = RecordingRunner()
        with configured(
            [
                "--tunnel-id",
                "tunnel_example",
                "--write-tunnel-profile",
                "PERSISTENT_PROFILE",
            ]
        ) as config:
            runtime_root = config.workspace.parent / "runtime"
            artifacts = allocate_run_artifacts(runtime_root)
            runtime = prepare_tunnel(config, artifacts, runner=runner)

            assert runtime is not None
            destination = config.tunnel.write_profile
            assert destination is not None
            self.assertTrue(destination.is_file())
            self.assertIn(str(destination), runtime.run_args)
            runtime.cleanup()
            self.assertTrue(destination.is_file())

    def test_doctor_returns_parsed_json_and_raises_on_failure(self) -> None:
        with configured(["--tunnel-profile", "dev"]) as config:
            with tempfile.TemporaryDirectory() as tmp:
                artifacts = allocate_run_artifacts(Path(tmp))
                runtime = prepare_tunnel(config, artifacts, runner=RecordingRunner())
                assert runtime is not None

                result = run_tunnel_doctor(
                    runtime,
                    environment={},
                    runner=RecordingRunner(stdout='{"ok": true}'),
                )
                self.assertEqual(result, {"ok": True})

                with self.assertRaisesRegex(TunnelError, "doctor failed"):
                    run_tunnel_doctor(
                        runtime,
                        environment={},
                        runner=RecordingRunner(returncode=4),
                    )

    def test_generated_local_mcp_accepts_missing_oauth_metadata_as_advisory(self) -> None:
        payload = {
            "result": "fail",
            "failed_checks": ["oauth_metadata"],
            "checks": [
                {
                    "id": "mcp_server_reachable",
                    "status": "PASS",
                    "summary": "HTTP 405",
                },
                {
                    "id": "oauth_metadata",
                    "status": "FAIL",
                    "summary": "HTTP 404",
                },
            ],
        }
        runner = RecordingRunner(returncode=2, stdout=json.dumps(payload))
        with configured(["--tunnel-id", "tunnel_example"]) as config:
            with tempfile.TemporaryDirectory() as tmp:
                runtime = prepare_tunnel(
                    config,
                    allocate_run_artifacts(Path(tmp)),
                    runner=RecordingRunner(),
                )
                assert runtime is not None

                result = run_tunnel_doctor(runtime, environment={}, runner=runner)

                self.assertEqual(result, payload)

    def test_generated_external_mcp_keeps_oauth_metadata_failure_strict(self) -> None:
        payload = {
            "result": "fail",
            "failed_checks": ["oauth_metadata"],
        }
        with configured(
            [
                "--tunnel-id",
                "tunnel_example",
                "--tunnel-mcp-server-url",
                "https://mcp.example.com/mcp",
            ]
        ) as config:
            with tempfile.TemporaryDirectory() as tmp:
                runtime = prepare_tunnel(
                    config,
                    allocate_run_artifacts(Path(tmp)),
                    runner=RecordingRunner(),
                )
                assert runtime is not None

                with self.assertRaisesRegex(TunnelError, "doctor failed"):
                    run_tunnel_doctor(
                        runtime,
                        environment={},
                        runner=RecordingRunner(
                            returncode=2,
                            stdout=json.dumps(payload),
                        ),
                    )

    def test_tunnel_ready_uses_url_file_and_readyz(self) -> None:
        with ready_server() as base_url, tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "health.url"
            url_file.write_text(base_url, encoding="utf-8")

            resolved = wait_for_tunnel_ready(
                FakeProcess(),
                url_file,
                timeout=1,
                poll_interval=0.01,
            )

            self.assertEqual(resolved, base_url)

    def test_tunnel_ready_stops_when_process_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TunnelError, "exited before readiness"):
                wait_for_tunnel_ready(
                    FakeProcess(5),
                    Path(tmp) / "health.url",
                    timeout=1,
                    poll_interval=0.01,
                )

    def test_diagnostic_capture_is_independent_and_writes_artifacts(self) -> None:
        with configured(["--tunnel-profile", "dev"]) as config:
            with ready_server() as base_url, tempfile.TemporaryDirectory() as tmp:
                artifacts = allocate_run_artifacts(Path(tmp))
                runtime = prepare_tunnel(config, artifacts, runner=RecordingRunner())
                assert runtime is not None
                runtime.health_url_file.write_text(base_url, encoding="utf-8")

                errors = capture_tunnel_diagnostics(
                    runtime,
                    artifacts,
                    environment={},
                    log_minutes=45,
                    runner=RecordingRunner(stdout='{"healthy": true}'),
                )

                self.assertEqual(errors, [])
                self.assertEqual(
                    json.loads(artifacts.tunnel_status.read_text(encoding="utf-8")),
                    {"status": "connected"},
                )
                self.assertEqual(
                    json.loads(artifacts.tunnel_health.read_text(encoding="utf-8")),
                    {"healthy": True},
                )
                self.assertEqual(artifacts.tunnel_events.read_bytes(), b"archive")


if __name__ == "__main__":
    unittest.main()
