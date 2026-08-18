from __future__ import annotations

import tempfile
import unittest
import json
import os
from pathlib import Path

from coding_tools_mcp.credential_providers import CredentialProviderRegistry, atomic_write_fragment


class CredentialProviderRegistryTests(unittest.TestCase):
    @staticmethod
    def _fragment(name: str, command: str) -> str:
        return f'name = "{name}"\ncommands = ["{command}"]\n'

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

    def test_registry_reloads_add_and_remove_without_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            registry_dir.mkdir()
            registry = CredentialProviderRegistry(registry_dir, root / "broker")
            self.assertEqual(registry.snapshot().providers, ())

            atomic_write_fragment(registry_dir / "a.toml", self._fragment("a", "a-cli"))
            first = registry.snapshot()
            atomic_write_fragment(registry_dir / "b.toml", self._fragment("b", "b-cli"))
            second = registry.snapshot()
            (registry_dir / "a.toml").unlink()
            third = registry.snapshot()

            self.assertEqual([item.name for item in first.providers], ["a"])
            self.assertEqual([item.name for item in second.providers], ["a", "b"])
            self.assertEqual([item.name for item in third.providers], ["b"])
            self.assertNotEqual(first.generation, second.generation)
            self.assertNotEqual(second.generation, third.generation)
            self.assertNotEqual(first.fingerprint, second.fingerprint)
            self.assertNotEqual(second.fingerprint, third.fingerprint)

    def test_invalid_replacement_discards_previous_provider_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            registry_dir.mkdir()
            registry = CredentialProviderRegistry(registry_dir, root / "broker")
            atomic_write_fragment(registry_dir / "a.toml", self._fragment("a", "a-cli"))
            self.assertEqual(len(registry.snapshot().providers), 1)

            atomic_write_fragment(registry_dir / "a.toml", 'name="a"\ncommands=[]\nTOKEN_LIKE_VALUE="do-not-leak"\n')
            snapshot = registry.snapshot()

            self.assertEqual(snapshot.health, "invalid")
            self.assertEqual(snapshot.providers, ())
            self.assertEqual(snapshot.command_owners, {})
            self.assertNotIn("do-not-leak", snapshot.error or "")
            self.assertLessEqual(len(snapshot.error or ""), 256)

    def test_empty_registry_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_dir = root / "credentials.d"
            registry_dir.mkdir()
            snapshot = CredentialProviderRegistry(registry_dir, root / "broker").snapshot()
            self.assertEqual(snapshot.health, "healthy")
            self.assertEqual(snapshot.providers, ())
            self.assertEqual(snapshot.command_owners, {})
            self.assertTrue(snapshot.generation)
            self.assertEqual(len(snapshot.fingerprint), 64)

    def test_atomic_writer_publishes_exact_content_without_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "provider.toml"
            atomic_write_fragment(target, "first")
            first_stat = target.stat()
            atomic_write_fragment(target, "second")
            second_stat = target.stat()

            self.assertEqual(target.read_text(encoding="utf-8"), "second")
            self.assertNotEqual(first_stat.st_ino, second_stat.st_ino)
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])
            self.assertEqual(os.listdir(tmp), [target.name])


if __name__ == "__main__":
    unittest.main()
