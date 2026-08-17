from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_tools_mcp.extensions import ExtensionManifest, ExtensionRegistry
from coding_tools_mcp.server import (
    build_parser,
    build_runtime,
    run_http,
    run_stdio,
    runtime_policy_from_args,
)


def fake_extension(name: str):
    class FakeExtension:
        manifest = ExtensionManifest(name=name)

        def configure(self, config):
            pass

        def register(self, context):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    return FakeExtension


class ConfigStartupTests(unittest.TestCase):
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
