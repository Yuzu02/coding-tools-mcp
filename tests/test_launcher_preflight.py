from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.launcher.config import ServiceConfig, resolve_config
from scripts.launcher.preflight import run_preflight


def make_repository(root: Path) -> Path:
    repository = root / "coding-tools-mcp"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return repository


def write_host_config(
    path: Path,
    *,
    workspace: Path,
    repository: Path,
    runtime_root: Path,
    state_root: Path,
    cache_root: Path,
    profile_file: Path,
    semantic: bool = False,
    extra_project: tuple[str, Path, bool] | None = None,
) -> None:
    enabled = '["projects", "semantic"]' if semantic else '["projects"]'
    lines = [
        "config_version = 2",
        "[runtime]",
        f'bootstrap_workspace = "{workspace}"',
        f'runtime_root = "{runtime_root}"',
        f'state_root = "{state_root}"',
        f'cache_root = "{cache_root}"',
        "[transport]",
        'kind = "http"',
        'host = "127.0.0.1"',
        "port = 9666",
        "[security]",
        'permission_mode = "dangerous"',
        'shell_env_inherit = "all"',
        "allow_network = true",
        'auth_mode = "noauth"',
        "[extensions]",
        f"enabled = {enabled}",
    ]
    if extra_project is not None:
        project_id, root, allow_unavailable = extra_project
        lines.extend(
            (
                f"[extensions.projects.registry.{project_id}]",
                f'root = "{root}"',
                f"allow_unavailable = {str(allow_unavailable).lower()}",
            )
        )
    lines.extend(
        (
            "[deployment]",
            f'mcp_repository = "{repository}"',
            "sync = false",
            "[deployment.tunnel]",
            'mode = "profile-file"',
            f'profile_file = "{profile_file}"',
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


class LauncherPreflightTests(unittest.TestCase):
    def fixture(
        self,
        root: Path,
        *,
        semantic: bool = False,
        runtime_root: Path | None = None,
        extra_project: tuple[str, Path, bool] | None = None,
        environment: dict[str, str] | None = None,
    ) -> ServiceConfig:
        repository = make_repository(root)
        workspace = root / "workspace"
        workspace.mkdir()
        runtime = runtime_root or root / "runtime"
        state = root / "state"
        cache = root / "cache"
        for item in (runtime, state, cache):
            if not item.exists():
                item.mkdir(parents=True)
        profile = root / "tunnel.yaml"
        profile.write_text("config_version: 1\n", encoding="utf-8")
        host = root / "host.toml"
        write_host_config(
            host,
            workspace=workspace,
            repository=repository,
            runtime_root=runtime,
            state_root=state,
            cache_root=cache,
            profile_file=profile,
            semantic=semantic,
            extra_project=extra_project,
        )
        return resolve_config(
            ["--host-config", str(host)],
            environ=environment or {},
            repo_root=repository,
        )

    def test_visible_unique_roots_free_port_and_profile_metadata_are_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.fixture(Path(tmp))

            report = run_preflight(config, port_probe=lambda _host, _port: False)

            self.assertTrue(report.ok, report.to_dict())
            self.assertEqual(report.findings, ())
            self.assertEqual(report.fingerprint, config.config_snapshot.fingerprint)

    def test_missing_registered_root_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing-project"
            config = self.fixture(
                root,
                extra_project=("missing", missing, True),
            )

            report = run_preflight(config, port_probe=lambda _host, _port: False)

            self.assertFalse(report.ok)
            self.assertIn("PROJECT_ROOT_NOT_VISIBLE", {finding.code for finding in report.findings})

    def test_occupied_listener_port_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.fixture(Path(tmp))

            report = run_preflight(config, port_probe=lambda _host, _port: True)

            self.assertFalse(report.ok)
            self.assertIn("LISTENER_PORT_IN_USE", {finding.code for finding in report.findings})

    def test_non_writable_external_runtime_root_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            config = self.fixture(root, runtime_root=runtime)
            original_mode = runtime.stat().st_mode
            runtime.chmod(0o500)
            try:
                report = run_preflight(config, port_probe=lambda _host, _port: False)
            finally:
                runtime.chmod(original_mode)

            self.assertFalse(report.ok)
            self.assertIn("RUNTIME_ROOT_NOT_WRITABLE", {finding.code for finding in report.findings})

    def test_runtime_root_inside_registered_project_is_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = make_repository(root)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime = workspace / ".runtime"
            runtime.mkdir()
            state = root / "state"
            cache = root / "cache"
            state.mkdir()
            cache.mkdir()
            profile = root / "tunnel.yaml"
            profile.write_text("config_version: 1\n", encoding="utf-8")
            host = root / "host.toml"
            write_host_config(
                host,
                workspace=workspace,
                repository=repository,
                runtime_root=runtime,
                state_root=state,
                cache_root=cache,
                profile_file=profile,
            )
            config = resolve_config(
                ["--host-config", str(host)],
                environ={},
                repo_root=repository,
            )

            report = run_preflight(config, port_probe=lambda _host, _port: False)

            self.assertFalse(report.ok)
            self.assertIn("RUNTIME_ROOT_INSIDE_PROJECT", {finding.code for finding in report.findings})

    def test_semantic_backend_must_be_exactly_serena_1_5_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = self.fixture(Path(tmp), semantic=True)
            with mock.patch("scripts.launcher.preflight.metadata.version", return_value="1.5.2"):
                report = run_preflight(config, port_probe=lambda _host, _port: False)

            self.assertFalse(report.ok)
            self.assertIn("SEMANTIC_BACKEND_VERSION", {finding.code for finding in report.findings})

    def test_serialized_report_never_contains_process_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = "preflight-secret-value"
            config = self.fixture(
                Path(tmp),
                environment={"CONTROL_PLANE_API_KEY": secret, "PATH": os.environ.get("PATH", "")},
            )

            report = run_preflight(config, port_probe=lambda _host, _port: False)
            serialized = json.dumps(report.to_dict(), sort_keys=True)

            self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
