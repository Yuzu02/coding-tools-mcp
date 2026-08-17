from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_tools_mcp.extensions import ConfigError, ExtensionManifest, ExtensionRegistry
from coding_tools_mcp.server import (
    build_parser,
    build_runtime,
    resolve_config_snapshot,
    run_http,
    run_stdio,
    runtime_policy_from_args,
)


def fake_extension(name: str):
    class FakeExtension:
        manifest = ExtensionManifest(name=name)

        def configure(self, config):
            pass

        def prepare(self):
            pass

        def register(self, context):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    return FakeExtension


def write_host_config(
    path: Path,
    *,
    workspace: Path,
    transport: str = "stdio",
    permission_mode: str = "dangerous",
    shell_env_inherit: str = "all",
    allow_network: bool = True,
    auth_mode: str = "noauth",
    auth_token_ref: str | None = None,
    port: int = 9123,
    security_extra: tuple[str, ...] = (),
) -> None:
    lines = [
        "config_version = 2",
        "[runtime]",
        f"bootstrap_workspace = {json.dumps(str(workspace))}",
        "enable_view_image = false",
        "[transport]",
        f"kind = {json.dumps(transport)}",
        'host = "127.0.0.1"',
        f"port = {port}",
        "[security]",
        f"permission_mode = {json.dumps(permission_mode)}",
        f"shell_env_inherit = {json.dumps(shell_env_inherit)}",
        f"allow_network = {str(allow_network).lower()}",
        f"auth_mode = {json.dumps(auth_mode)}",
    ]
    if auth_token_ref is not None:
        lines.append(f"auth_token_ref = {json.dumps(auth_token_ref)}")
    lines.extend(security_extra)
    lines.extend(
        (
            "[extensions]",
            'enabled = ["projects"]',
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


class ConfigStartupTests(unittest.TestCase):
    def test_build_parser_accepts_host_config(self) -> None:
        args = build_parser().parse_args(["--host-config", "host.toml"])

        self.assertEqual(args.host_config, "host.toml")

    def test_host_config_rejects_developer_config_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            host = root / "host.toml"
            write_host_config(host, workspace=workspace)
            registry = ExtensionRegistry([], default_enabled=())

            for option, value in (
                ("--config", "public.toml"),
                ("--local-config", "local.toml"),
                ("--extensions", "projects"),
            ):
                with self.subTest(option=option):
                    args = build_parser().parse_args(["--host-config", str(host), option, value])
                    with self.assertRaisesRegex(ConfigError, "--host-config"):
                        resolve_config_snapshot(
                            args,
                            registry=registry,
                            resolved_workspace=workspace,
                        )

    def test_build_runtime_uses_host_workspace_policy_extensions_and_ignores_local_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            host = root / "host.toml"
            write_host_config(host, workspace=workspace)
            (root / "coding-tools.local.toml").write_text(
                "this is deliberately not valid toml = [",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--host-config", str(host)])
            developer_policy = runtime_policy_from_args(args)

            with mock.patch("pathlib.Path.cwd", return_value=root):
                runtime = build_runtime(args, developer_policy, emit_warning=False)
            try:
                self.assertEqual(runtime.workspace.root, workspace.resolve())
                self.assertEqual(runtime.permission_mode, "dangerous")
                self.assertEqual(runtime.shell_env_policy.inherit, "all")
                self.assertTrue(runtime.allow_network)
                self.assertFalse(runtime.enable_view_image)
                self.assertEqual(runtime.extension_config.enabled_extensions, ("projects",))
                self.assertEqual(runtime.config_snapshot.resolution_mode, "host")
                self.assertEqual(runtime.config_snapshot.sources[0].path, host.resolve())
            finally:
                runtime.close()

    def test_run_http_resolves_host_bearer_secret_only_for_http_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            host = root / "host.toml"
            write_host_config(
                host,
                workspace=workspace,
                transport="http",
                auth_mode="bearer",
                auth_token_ref="env:TASK7_BEARER_TOKEN",
                port=9234,
            )
            args = build_parser().parse_args(["--host-config", str(host)])

            with (
                mock.patch.dict(os.environ, {"TASK7_BEARER_TOKEN": "task7-secret-value"}, clear=False),
                mock.patch("coding_tools_mcp.server.RuntimeHTTPServer") as server_type,
            ):
                result = run_http(args)

            self.assertEqual(result, 0)
            server_type.assert_called_once()
            address, _handler, runtime = server_type.call_args.args
            self.assertEqual(address, ("127.0.0.1", 9234))
            self.assertEqual(runtime.auth_token, "task7-secret-value")
            serialized = json.dumps(runtime.server_info_payload(), sort_keys=True)
            self.assertNotIn("task7-secret-value", serialized)

    def test_run_http_resolves_host_oauth_secret_refs_only_for_http_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            host = root / "host.toml"
            write_host_config(
                host,
                workspace=workspace,
                transport="http",
                auth_mode="oauth",
                port=9345,
                security_extra=(
                    'oauth_client_id = "task7-client"',
                    'oauth_client_secret_ref = "env:TASK7_OAUTH_CLIENT_SECRET"',
                    'oauth_password_ref = "env:TASK7_OAUTH_PASSWORD"',
                    'oauth_token_secret_ref = "env:TASK7_OAUTH_TOKEN_SECRET"',
                    'oauth_server_url = "https://mcp.example.invalid"',
                    'oauth_redirect_uris = ["https://client.example.invalid/callback"]',
                    "oauth_token_ttl_seconds = 600",
                ),
            )
            args = build_parser().parse_args(["--host-config", str(host)])
            environment = {
                "TASK7_OAUTH_CLIENT_SECRET": "oauth-client-secret-value",
                "TASK7_OAUTH_PASSWORD": "oauth-password-value",
                "TASK7_OAUTH_TOKEN_SECRET": "ab" * 32,
            }

            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch("coding_tools_mcp.server.RuntimeHTTPServer") as server_type,
            ):
                result = run_http(args)

            self.assertEqual(result, 0)
            _address, _handler, runtime = server_type.call_args.args
            oauth = runtime.oauth_config
            self.assertIsNotNone(oauth)
            assert oauth is not None
            self.assertEqual(oauth.password, "oauth-password-value")
            self.assertEqual(oauth.token_secret, bytes.fromhex("ab" * 32))
            self.assertEqual(oauth.token_ttl, 600)
            self.assertEqual(oauth.server_url, "https://mcp.example.invalid")
            self.assertTrue(
                oauth.registry.authenticates(
                    "task7-client",
                    "oauth-client-secret-value",
                    "client_secret_post",
                )
            )
            serialized = json.dumps(runtime.server_info_payload(), sort_keys=True)
            for secret in environment.values():
                self.assertNotIn(secret, serialized)

    def test_build_parser_accepts_config_local_config_and_extensions(self) -> None:
        args = build_parser().parse_args(
            [
                "--config",
                "public.toml",
                "--local-config",
                "local.toml",
                "--extensions",
                "projects,semantic",
            ]
        )

        self.assertEqual(args.config, "public.toml")
        self.assertEqual(args.local_config, "local.toml")
        self.assertEqual(args.extensions, "projects,semantic")

    def test_build_runtime_loads_public_config_from_cwd_when_no_override_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                "config_version = 1\n[extensions]\nenabled = []\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--workspace", tmp, "--stdio"])
            policy = runtime_policy_from_args(args)

            with mock.patch("pathlib.Path.cwd", return_value=root):
                runtime = build_runtime(args, policy, emit_warning=False)
            try:
                self.assertEqual(runtime.extension_config.sources, (root / "coding-tools.toml",))
            finally:
                runtime.close()

    def test_explicit_config_path_beats_environment_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit.toml"
            env_file = root / "env.toml"
            for path in (explicit, env_file):
                path.write_text(
                    "config_version = 1\n[extensions]\nenabled = []\n",
                    encoding="utf-8",
                )
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(explicit), "--stdio"])
            policy = runtime_policy_from_args(args)

            with mock.patch.dict(os.environ, {"CODING_TOOLS_MCP_CONFIG": str(env_file)}, clear=False):
                runtime = build_runtime(args, policy, emit_warning=False)
            try:
                self.assertEqual(runtime.extension_config.sources, (explicit.resolve(),))
            finally:
                runtime.close()

    def test_cli_extensions_replace_environment_extensions(self) -> None:
        alpha = fake_extension("alpha")
        beta = fake_extension("beta")
        registry = ExtensionRegistry([alpha, beta], default_enabled=())
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_config = Path(tmp) / "synthetic.toml"
            synthetic_config.write_text(
                'config_version = 1\n[extensions]\nenabled = ["alpha"]\n',
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--workspace",
                    tmp,
                    "--config",
                    str(synthetic_config),
                    "--extensions",
                    "beta",
                    "--stdio",
                ]
            )
            policy = runtime_policy_from_args(args)

            with (
                mock.patch("coding_tools_mcp.server.builtin_extension_registry", return_value=registry),
                mock.patch.dict(os.environ, {"CODING_TOOLS_MCP_EXTENSIONS": "alpha"}, clear=False),
            ):
                runtime = build_runtime(args, policy, emit_warning=False)
            try:
                self.assertEqual(runtime.extension_config.enabled_extensions, ("beta",))
            finally:
                runtime.close()

    def test_invalid_config_fails_before_http_server_construction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.toml"
            bad.write_text(
                "config_version = 999\n[extensions]\nenabled = []\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(bad)])

            with mock.patch("coding_tools_mcp.server.RuntimeHTTPServer") as server_type:
                self.assertEqual(run_http(args), 2)
            server_type.assert_not_called()

    def test_invalid_config_fails_stdio_startup_with_exit_code_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.toml"
            bad.write_text(
                "config_version = 999\n[extensions]\nenabled = []\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(bad), "--stdio"])

            self.assertEqual(run_stdio(args), 2)


if __name__ == "__main__":
    unittest.main()
