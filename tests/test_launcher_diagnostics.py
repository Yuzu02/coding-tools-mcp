from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.launcher.diagnostics import (
    RunManifest,
    allocate_run_artifacts,
    atomic_write_json,
    bounded_log_tail,
    download_http_artifact,
)


class _ArtifactHandler(BaseHTTPRequestHandler):
    payload = b"diagnostic-payload"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class LauncherDiagnosticsTests(unittest.TestCase):
    def test_run_directories_are_unique_and_logs_are_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)

            first = allocate_run_artifacts(root, now=now)
            second = allocate_run_artifacts(root, now=now)

            self.assertNotEqual(first.run_directory, second.run_directory)
            self.assertEqual(first.manifest, first.run_directory / "run.json")
            self.assertEqual(
                first.mcp_stdout,
                first.run_directory / "coding-tools-mcp.stdout.log",
            )
            self.assertEqual(
                first.tunnel_stderr,
                first.run_directory / "tunnel-client.stderr.log",
            )

    def test_atomic_write_json_replaces_existing_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            atomic_write_json(path, {"state": "starting"})
            atomic_write_json(path, {"state": "running", "count": 2})

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"count": 2, "state": "running"},
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_manifest_redacts_known_secret_values_and_tracks_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = allocate_run_artifacts(Path(tmp))
            manifest = RunManifest.start(
                artifacts,
                {"api_key_ref": "env:CONTROL_PLANE_API_KEY"},
                secret_values=("secret-value",),
            )

            manifest.transition("starting-mcp")
            manifest.record_process("mcp", 123)
            manifest.record_ready("mcp")
            manifest.record_exit("mcp", 7)
            manifest.record_failure("doctor", "failed while using secret-value")
            manifest.finish(7)

            payload = json.loads(artifacts.manifest.read_text(encoding="utf-8"))
            serialized = json.dumps(payload)
            self.assertNotIn("secret-value", serialized)
            self.assertIn("[REDACTED]", serialized)
            self.assertEqual(payload["state"], "failed")
            self.assertEqual(payload["exitCode"], 7)
            self.assertEqual(payload["processes"]["mcp"]["pid"], 123)
            self.assertEqual(payload["processes"]["mcp"]["exitCode"], 7)
            self.assertIn("readyAt", payload["processes"]["mcp"])

    def test_manifest_omits_environment_and_raw_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = allocate_run_artifacts(Path(tmp))
            manifest = RunManifest.start(
                artifacts,
                {
                    "workspace": "/srv/workspace",
                    "process_environment": {"TOKEN": "secret"},
                    "argv": ["tool", "--key", "secret"],
                },
                secret_values=("secret",),
            )

            payload = json.loads(artifacts.manifest.read_text(encoding="utf-8"))
            self.assertNotIn("process_environment", payload["configuration"])
            self.assertNotIn("argv", payload["configuration"])
            self.assertNotIn("secret", json.dumps(payload))
            manifest.finish(0)

    def test_bounded_log_tail_limits_lines_and_replaces_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.log"
            path.write_bytes(b"old\n" * 100 + b"new\xff\n")

            tail = bounded_log_tail(path, max_bytes=128, max_lines=3)

            self.assertLessEqual(len(tail.splitlines()), 3)
            self.assertIn("new", tail)
            self.assertIn("\ufffd", tail)

    def test_download_http_artifact_replaces_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "artifact.bin"
            destination.write_bytes(b"old")
            server = ThreadingHTTPServer(("127.0.0.1", 0), _ArtifactHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                download_http_artifact(
                    f"http://{host}:{port}/artifact",
                    destination,
                    timeout=1,
                )
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()

            self.assertEqual(destination.read_bytes(), _ArtifactHandler.payload)


if __name__ == "__main__":
    unittest.main()
