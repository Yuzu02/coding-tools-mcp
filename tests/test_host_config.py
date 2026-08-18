from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.config_schema import ConfigError, scalar, table
from coding_tools_mcp.host_config import load_host_config


class HostConfigTests(unittest.TestCase):
    def test_host_config_requires_version_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.toml"
            path.write_text("config_version = 1\n", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "config_version must be 2"):
                load_host_config(
                    path,
                    extension_schemas={},
                    default_enabled=(),
                )

    def test_host_config_rejects_unknown_root_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.toml"
            path.write_text(
                "config_version = 2\ntypo = true\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "unknown configuration key: host.typo"):
                load_host_config(
                    path,
                    extension_schemas={},
                    default_enabled=(),
                )

    def test_host_config_rejects_unknown_nested_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.toml"
            path.write_text(
                "config_version = 2\n[security]\ntypo = true\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ConfigError,
                "unknown configuration key: host.security.typo",
            ):
                load_host_config(
                    path,
                    extension_schemas={},
                    default_enabled=(),
                )

    def test_noauth_http_requires_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            path = root / "config.toml"
            path.write_text(
                "\n".join(
                    (
                        "config_version = 2",
                        "[runtime]",
                        f'bootstrap_workspace = "{workspace}"',
                        "[transport]",
                        'kind = "http"',
                        'host = "0.0.0.0"',
                        "port = 8000",
                        "[security]",
                        'auth_mode = "noauth"',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "loopback"):
                load_host_config(
                    path,
                    extension_schemas={},
                    default_enabled=(),
                )

    def test_host_config_parses_exec_credential_providers_without_literal_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            vercel_store = root / "vercel-store"
            vercel_session = root / "vercel-session"
            path = root / "config.toml"
            path.write_text(
                "\n".join(
                    (
                        "config_version = 2",
                        "[runtime]",
                        f'bootstrap_workspace = "{workspace}"',
                        "[security]",
                        'auth_mode = "noauth"',
                        "[[security.exec_credentials]]",
                        'name = "vercel"',
                        'commands = ["vercel"]',
                        f'read_roots = ["{vercel_store}"]',
                        f'write_roots = ["{vercel_session}"]',
                        'env_passthrough = ["VERCEL_TOKEN"]',
                        f'env_paths = ["XDG_DATA_HOME={root / "xdg-data"}"]',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            config = load_host_config(
                path,
                extension_schemas={},
                default_enabled=(),
            )

            self.assertEqual(len(config.security.exec_credentials), 1)
            provider = config.security.exec_credentials[0]
            self.assertEqual(provider.name, "vercel")
            self.assertEqual(provider.commands, ("vercel",))
            self.assertEqual(provider.read_roots, (vercel_store.resolve(),))
            self.assertEqual(provider.write_roots, (vercel_session.resolve(),))
            self.assertEqual(provider.env_passthrough, ("VERCEL_TOKEN",))
            self.assertEqual(provider.env_paths, (("XDG_DATA_HOME", (root / "xdg-data").resolve()),))

    def test_host_config_rejects_secret_name_in_exec_credential_env_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            path = root / "config.toml"
            path.write_text(
                "\n".join(
                    (
                        "config_version = 2",
                        "[runtime]",
                        f'bootstrap_workspace = "{workspace}"',
                        "[security]",
                        "[[security.exec_credentials]]",
                        'name = "bad"',
                        'commands = ["vercel"]',
                        f'env_paths = ["VERCEL_TOKEN={root / "literal-value-is-forbidden"}"]',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ConfigError, "secret-like environment variable"):
                load_host_config(
                    path,
                    extension_schemas={},
                    default_enabled=(),
                )

    def test_host_config_exec_credentials_cannot_override_isolated_process_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for field_line in (
                f'env_paths = ["HOME={root / "user-home"}"]',
                'env_passthrough = ["HOME"]',
                'env_passthrough = ["XDG_CONFIG_HOME"]',
                'env_passthrough = ["PATH"]',
            ):
                with self.subTest(field_line=field_line):
                    path = root / "config.toml"
                    path.write_text(
                        "\n".join(
                            (
                                "config_version = 2",
                                "[runtime]",
                                f'bootstrap_workspace = "{workspace}"',
                                "[security]",
                                "[[security.exec_credentials]]",
                                'name = "bad"',
                                'commands = ["vercel"]',
                                field_line,
                                "",
                            )
                        ),
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(ConfigError, "isolated process environment"):
                        load_host_config(
                            path,
                            extension_schemas={},
                            default_enabled=(),
                        )

    def test_secret_ref_accepts_env_and_absolute_file(self) -> None:
        from coding_tools_mcp.host_config import parse_secret_ref

        env_ref = parse_secret_ref("env:API_TOKEN")
        file_ref = parse_secret_ref("file:/run/secrets/api-token")

        self.assertEqual((env_ref.scheme, env_ref.target), ("env", "API_TOKEN"))
        self.assertEqual(
            (file_ref.scheme, file_ref.target),
            ("file", "/run/secrets/api-token"),
        )

    def test_secret_ref_rejects_literal_and_relative_file(self) -> None:
        from coding_tools_mcp.host_config import parse_secret_ref

        for raw in ("literal-secret", "file:relative/token", "env:not-valid!"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ConfigError, "secret reference"):
                parse_secret_ref(raw)

    def test_secret_ref_resolution_reads_only_selected_source(self) -> None:
        from coding_tools_mcp.host_config import parse_secret_ref, resolve_secret_ref

        with tempfile.TemporaryDirectory() as tmp:
            secret_path = Path(tmp) / "token"
            secret_path.write_text("file-secret\n", encoding="utf-8")

            self.assertEqual(
                resolve_secret_ref(
                    parse_secret_ref("env:API_TOKEN"),
                    environ={"API_TOKEN": "env-secret", "UNRELATED": "do-not-use"},
                ),
                "env-secret",
            )
            self.assertEqual(
                resolve_secret_ref(
                    parse_secret_ref(f"file:{secret_path}"),
                    environ={"API_TOKEN": "wrong-source"},
                ),
                "file-secret",
            )

    def test_standard_user_path_prefers_xdg_config_home(self) -> None:
        from coding_tools_mcp.host_config import standard_host_config_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            xdg = root / "xdg"

            selected = standard_host_config_path(
                environ={"XDG_CONFIG_HOME": str(xdg)},
                home=home,
            )

            self.assertEqual(selected, xdg / "coding-tools-mcp" / "config.toml")

    def test_host_config_ignores_adjacent_developer_local_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.toml"
            path.write_text("config_version = 2\n", encoding="utf-8")
            (root / "coding-tools.local.toml").write_text(
                "config_version = 1\n[extensions]\nenabled = [\"unknown\"]\n",
                encoding="utf-8",
            )

            config = load_host_config(
                path,
                extension_schemas={},
                default_enabled=(),
            )

            self.assertEqual(config.source, path.resolve())
            self.assertEqual(config.extensions.enabled_extensions, ())

    def test_host_config_normalizes_runtime_extensions_and_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            runtime_root = root / "runtime"
            state_root = root / "state"
            cache_root = root / "cache"
            logs_root = root / "logs"
            profile_file = root / "tunnel.yaml"
            path = root / "config.toml"
            path.write_text(
                "\n".join(
                    (
                        "config_version = 2",
                        "[runtime]",
                        f'bootstrap_workspace = "{workspace}"',
                        f'runtime_root = "{runtime_root}"',
                        f'state_root = "{state_root}"',
                        f'cache_root = "{cache_root}"',
                        "enable_view_image = true",
                        "[transport]",
                        'kind = "http"',
                        'host = "127.0.0.1"',
                        "port = 8123",
                        "[security]",
                        'permission_mode = "dangerous"',
                        'shell_env_inherit = "all"',
                        "allow_network = false",
                        'auth_mode = "noauth"',
                        "[extensions]",
                        'enabled = ["fake"]',
                        "[extensions.fake]",
                        "flag = true",
                        "[deployment]",
                        f'mcp_repository = "{workspace}"',
                        "sync = false",
                        'sync_extras = ["semantic"]',
                        f'logs_root = "{logs_root}"',
                        "startup_timeout_seconds = 75",
                        "shutdown_timeout_seconds = 12",
                        "poll_interval_seconds = 1",
                        "[deployment.tunnel]",
                        'mode = "profile-file"',
                        f'profile_file = "{profile_file}"',
                        'health_listen_addr = "127.0.0.1:9191"',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            config = load_host_config(
                path,
                extension_schemas={"fake": table({"flag": scalar(bool)})},
                default_enabled=(),
            )

            self.assertEqual(config.runtime.bootstrap_workspace, workspace.resolve())
            self.assertEqual(config.runtime.runtime_root, runtime_root.resolve())
            self.assertEqual(config.runtime.state_root, state_root.resolve())
            self.assertEqual(config.runtime.cache_root, cache_root.resolve())
            self.assertTrue(config.runtime.enable_view_image)
            self.assertEqual((config.transport.host, config.transport.port), ("127.0.0.1", 8123))
            self.assertEqual(config.security.permission_mode, "dangerous")
            self.assertEqual(config.security.shell_env_inherit, "all")
            self.assertFalse(config.security.allow_network)
            self.assertEqual(config.extensions.enabled_extensions, ("fake",))
            self.assertIs(config.extensions.extension("fake")["flag"], True)
            self.assertFalse(config.deployment.sync)
            self.assertEqual(config.deployment.sync_extras, ("semantic",))
            self.assertEqual(config.deployment.logs_root, logs_root.resolve())
            self.assertEqual(config.deployment.tunnel.mode, "profile-file")
            self.assertEqual(config.deployment.tunnel.profile_file, profile_file.resolve())


if __name__ == "__main__":
    unittest.main()
