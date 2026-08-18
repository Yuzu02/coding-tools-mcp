from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from coding_tools_mcp.credential_providers import CredentialProviderRegistry


class CredentialProviderRegistryTests(unittest.TestCase):
    def test_server_module_imports(self) -> None:
        import coding_tools_mcp.server  # noqa: F401

    def test_host_mode_build_runtime_has_no_static_credential_authority(self) -> None:
        from coding_tools_mcp.server import build_parser, build_runtime, runtime_policy_from_args

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            host = root / "host.toml"
            host.write_text(
                "\n".join(
                    (
                        "config_version = 2",
                        "[runtime]",
                        f"bootstrap_workspace = {json.dumps(str(workspace))}",
                        "[transport]",
                        'kind = "stdio"',
                        'host = "127.0.0.1"',
                        "port = 8000",
                        "[security]",
                        'permission_mode = "dangerous"',
                        'shell_env_inherit = "none"',
                        "allow_network = false",
                        'auth_mode = "noauth"',
                        "",
                    )
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--host-config", str(host)])
            runtime = build_runtime(args, runtime_policy_from_args(args), emit_warning=False)
            try:
                self.assertEqual(runtime.exec_credentials, ())
            finally:
                runtime.close()

    def test_unknown_fragment_key_invalidates_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            registry_dir.mkdir()
            (registry_dir / "bad.toml").write_text('name = "bad"\ncommands = ["bad"]\nunknown = true\n', encoding="utf-8")
            snapshot = CredentialProviderRegistry(registry_dir, root / "broker").snapshot()
            self.assertEqual(snapshot.health, "invalid")
            self.assertEqual(snapshot.providers, ())

    def test_registry_rejects_provider_root_outside_own_broker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            (registry_dir / "bad.toml").write_text(
                'name = "bad"\ncommands = ["bad"]\nread_roots = ["/tmp"]\n',
                encoding="utf-8",
            )

            snapshot = CredentialProviderRegistry(registry_dir, broker_dir).snapshot()

            self.assertEqual(snapshot.health, "invalid")
            self.assertEqual(snapshot.providers, ())

    def test_valid_fragment_produces_exact_non_secret_provider_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            read_root = broker_dir / "example" / "read"
            write_root = broker_dir / "example" / "state"
            env_root = broker_dir / "example" / "env"
            for path in (registry_dir, read_root, write_root, env_root):
                path.mkdir(parents=True)
            (registry_dir / "arbitrary-name.toml").write_text(
                "\n".join(
                    (
                        'name = "example"',
                        'commands = ["example-cli"]',
                        f'read_roots = ["{read_root}"]',
                        f'write_roots = ["{write_root}"]',
                        'env_passthrough = ["EXAMPLE_TOKEN"]',
                        f'env_paths = ["EXAMPLE_CONFIG_DIR={env_root}"]',
                        "",
                    )
                ),
                encoding="utf-8",
            )

            snapshot = CredentialProviderRegistry(registry_dir, broker_dir).snapshot()

            self.assertEqual(snapshot.health, "healthy")
            self.assertEqual(len(snapshot.providers), 1)
            provider = snapshot.providers[0]
            self.assertEqual(provider.name, "example")
            self.assertEqual(provider.commands, ("example-cli",))
            self.assertEqual(provider.read_roots, (read_root.resolve(),))
            self.assertEqual(provider.write_roots, (write_root.resolve(),))
            self.assertEqual(provider.env_passthrough, ("EXAMPLE_TOKEN",))
            self.assertEqual(provider.env_paths, (("EXAMPLE_CONFIG_DIR", env_root.resolve()),))


if __name__ == "__main__":
    unittest.main()
