from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from scripts.launcher.config import (
    ConfigError,
    ServiceConfig,
    load_dotenv,
    resolve_config,
    scrub_mcp_environment,
)


def make_repository(root: Path) -> Path:
    repository = root / "coding-tools-mcp"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return repository


@contextmanager
def resolve_fixture(
    extra: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> Iterator[ServiceConfig]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repository = make_repository(root)
        workspace = root / "workspace"
        workspace.mkdir()
        profile = root / "profile.yaml"
        profile.write_text("config_version: 1\n", encoding="utf-8")
        arguments = [
            "--workspace",
            str(workspace),
            "--mcp-repository",
            str(repository),
        ]
        arguments.extend(str(profile) if value == "profile.yaml" else value for value in extra)
        yield resolve_config(arguments, environ=environment or {}, repo_root=repository)


class LauncherConfigTests(unittest.TestCase):
    def test_cli_overrides_process_environment_and_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = make_repository(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".env").write_text(
                "CODING_TOOLS_SERVICES_PORT=7000\n",
                encoding="utf-8",
            )

            config = resolve_config(
                [
                    "--workspace",
                    str(workspace),
                    "--mcp-repository",
                    str(repository),
                    "--port",
                    "9000",
                    "--no-tunnel",
                ],
                environ={"CODING_TOOLS_SERVICES_PORT": "8000"},
                repo_root=repository,
            )

            self.assertEqual(config.port, 9000)

    def test_process_environment_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = make_repository(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".env").write_text(
                "CODING_TOOLS_SERVICES_PERMISSION_MODE=safe\n",
                encoding="utf-8",
            )

            config = resolve_config(
                [
                    "--workspace",
                    str(workspace),
                    "--mcp-repository",
                    str(repository),
                    "--no-tunnel",
                ],
                environ={"CODING_TOOLS_SERVICES_PERMISSION_MODE": "trusted"},
                repo_root=repository,
            )

            self.assertEqual(config.permission_mode, "trusted")

    def test_invalid_dotenv_reports_line_without_previous_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "CONTROL_PLANE_API_KEY=do-not-print\nnot valid\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, r"line 2") as raised:
                load_dotenv(path)

            self.assertNotIn("do-not-print", str(raised.exception))

    def test_dotenv_supports_export_and_quoted_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "export FIRST='one two'\nSECOND=\"three\"\n",
                encoding="utf-8",
            )

            self.assertEqual(load_dotenv(path), {"FIRST": "one two", "SECOND": "three"})

    def test_profile_modes_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ConfigError, "mutually exclusive"):
            with resolve_fixture(
                [
                    "--tunnel-profile",
                    "dev",
                    "--tunnel-profile-file",
                    "profile.yaml",
                ]
            ):
                pass

    def test_no_tunnel_conflicts_with_profile_selection(self) -> None:
        with self.assertRaisesRegex(ConfigError, "mutually exclusive"):
            with resolve_fixture(["--no-tunnel", "--tunnel-profile", "dev"]):
                pass

    def test_literal_control_plane_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "env:NAME or file:/path"):
            with resolve_fixture(
                [
                    "--tunnel-id",
                    "tunnel_test",
                    "--control-plane-api-key-ref",
                    "sk-secret",
                ]
            ):
                pass

    def test_generated_profile_requires_tunnel_id(self) -> None:
        with self.assertRaisesRegex(ConfigError, "requires --tunnel-id"):
            with resolve_fixture(["--write-tunnel-profile", "generated.yaml"]):
                pass

    def test_default_tunnel_profile_uses_generic_profile(self) -> None:
        with resolve_fixture([]) as config:
            self.assertEqual(config.tunnel.mode, "profile")
            self.assertEqual(config.tunnel.profile, "coding-tools-dev")

    def test_mcp_argv_uses_uv_locked_without_session_flags(self) -> None:
        with resolve_fixture(["--no-tunnel"]) as config:
            self.assertEqual(
                config.mcp_argv()[:7],
                [
                    "uv",
                    "run",
                    "--project",
                    str(config.mcp_repository),
                    "--locked",
                    "python",
                    "-m",
                ],
            )
            self.assertNotIn("--http-session-mode", config.mcp_argv())
            self.assertIn("--enable-view-image", config.mcp_argv())

    def test_sync_argv_includes_each_extra(self) -> None:
        with resolve_fixture(
            ["--no-tunnel", "--sync-extra", "dev", "--sync-extra", "image"]
        ) as config:
            self.assertEqual(
                config.sync_argv(),
                [
                    "uv",
                    "sync",
                    "--project",
                    str(config.mcp_repository),
                    "--locked",
                    "--extra",
                    "dev",
                    "--extra",
                    "image",
                ],
            )

    def test_mcp_environment_removes_tunnel_credentials(self) -> None:
        scrubbed = scrub_mcp_environment(
            {
                "PATH": "bin",
                "CONTROL_PLANE_API_KEY": "secret",
                "OPENAI_API_KEY": "fallback",
                "CONTROL_PLANE_CLIENT_KEY": "key.pem",
                "CONTROL_PLANE_EXTRA_HEADERS": "Authorization: secret",
                "TUNNEL_CLIENT_PROFILE": "dev",
            },
            "env:CONTROL_PLANE_API_KEY",
        )

        self.assertEqual(scrubbed, {"PATH": "bin"})

    def test_remote_tunnel_admin_listener_requires_explicit_allow(self) -> None:
        with self.assertRaisesRegex(ConfigError, "remote tunnel admin"):
            with resolve_fixture(["--tunnel-health-listen-addr", "0.0.0.0:8080"]):
                pass

    def test_dangerous_mode_is_retained_in_redacted_summary(self) -> None:
        with resolve_fixture(["--no-tunnel", "--permission-mode", "dangerous"]) as config:
            self.assertEqual(config.redacted_summary()["permission_mode"], "dangerous")
            self.assertNotIn("process_environment", config.redacted_summary())

    def test_workspace_can_come_from_legacy_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = make_repository(root)
            workspace = root / "workspace"
            workspace.mkdir()

            config = resolve_config(
                ["--mcp-repository", str(repository), "--no-tunnel"],
                environ={"CODING_TOOLS_MCP_WORKSPACE": str(workspace)},
                repo_root=repository,
            )

            self.assertEqual(config.workspace, workspace.resolve())

    def test_no_env_file_does_not_require_default_dotenv(self) -> None:
        with resolve_fixture(["--no-env-file", "--no-tunnel"]) as config:
            self.assertFalse(config.env_file_loaded)


if __name__ == "__main__":
    unittest.main()
