from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from coding_tools_mcp import server as server_module
from coding_tools_mcp.credential_providers import CredentialProviderRegistry, atomic_write_fragment
from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.server import Runtime


class CredentialLandlockTests(unittest.TestCase):
    def setUp(self) -> None:
        if sys.platform != "linux" or not Path("/proc").exists() or shutil.which("head") is None:
            self.skipTest("credential Landlock tests require a POSIX Linux host with head")

    @staticmethod
    def _write_provider(
        registry_dir: Path,
        broker_dir: Path,
        name: str,
        command: str,
        read_root: Path,
        write_root: Path,
    ) -> None:
        read_root.mkdir(parents=True)
        write_root.mkdir(parents=True)
        (read_root / "store").write_text("credential", encoding="utf-8")
        atomic_write_fragment(
            registry_dir / f"{name}.toml",
            f'name = "{name}"\ncommands = ["{command}"]\n'
            f'read_roots = ["{read_root}"]\nwrite_roots = ["{write_root}"]\n',
        )

    def test_non_provider_cannot_open_either_broker_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            a_read = broker_dir / "a" / "read"
            a_write = broker_dir / "a" / "write"
            b_read = broker_dir / "b" / "read"
            b_write = broker_dir / "b" / "write"
            self._write_provider(registry_dir, broker_dir, "a", "a-cli", a_read, a_write)
            self._write_provider(registry_dir, broker_dir, "b", "b-cli", b_read, b_write)
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": (
                            f"if head -c 0 {a_read / 'store'}; then echo A_READABLE; else echo A_BLOCKED; fi; "
                            f"if head -c 0 {b_read / 'store'}; then echo B_READABLE; else echo B_BLOCKED; fi"
                        ),
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()
            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertIn("A_BLOCKED", result.get("stdout", ""))
            self.assertIn("B_BLOCKED", result.get("stdout", ""))

    def test_non_provider_can_read_its_own_proc_self_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": "head -c 1 /proc/self/maps >/dev/null && printf SELF_READABLE",
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertEqual(result.get("stdout"), "SELF_READABLE")

    def test_non_provider_can_resolve_current_posix_identity(self) -> None:
        identity = shutil.which("id")
        if identity is None:
            self.skipTest("POSIX id command is not installed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": f"{identity} -un",
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertTrue(result.get("stdout", "").strip())

    def test_non_provider_can_allocate_posix_pty(self) -> None:
        script = shutil.which("script")
        if script is None:
            self.skipTest("POSIX script command is not installed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": f"{script} -q -c 'printf PTY_OK' /dev/null",
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertIn("PTY_OK", result.get("stdout", ""))

    def test_non_provider_cannot_read_parent_process_maps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": (
                            f"if head -c 1 /proc/{os.getpid()}/maps >/dev/null 2>&1; "
                            "then printf PARENT_READABLE; else printf PARENT_BLOCKED; fi"
                        ),
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertEqual(result.get("stdout"), "PARENT_BLOCKED")

    def test_non_provider_can_start_bun_javascript_runtime(self) -> None:
        bun = shutil.which("bun")
        if bun is None:
            self.skipTest("Bun is not installed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": f"{bun} -e 'console.log(\"BUN_RUNTIME_OK\")'",
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertEqual(result.get("stdout"), "BUN_RUNTIME_OK\n")

    def test_non_provider_bunx_can_resolve_nested_current_directory(self) -> None:
        bun = shutil.which("bun")
        if bun is None:
            self.skipTest("Bun is not installed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            local_bin = workspace / "node_modules" / ".bin"
            local_bin.mkdir(parents=True)
            probe = local_bin / "probe"
            probe.write_text("#!/bin/sh\nprintf 'BUNX_LOCAL_OK\\n'\n", encoding="utf-8")
            probe.chmod(0o755)
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": f"{bun} x --no-install probe",
                        "timeout_ms": 5000,
                        "yield_time_ms": 5000,
                    }
                )
            finally:
                runtime.close()

            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertEqual(result.get("stdout"), "BUNX_LOCAL_OK\n")

    def test_provider_can_read_and_write_only_its_own_roots(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            scripts = root / "scripts"
            scripts.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            a_read = broker_dir / "a" / "read"
            a_write = broker_dir / "a" / "write"
            b_read = broker_dir / "b" / "read"
            b_write = broker_dir / "b" / "write"
            self._write_provider(registry_dir, broker_dir, "a", "a-cli", a_read, a_write)
            self._write_provider(registry_dir, broker_dir, "b", "b-cli", b_read, b_write)
            a_script = scripts / "a-cli"
            a_script.write_text(
                "#!/bin/sh\n"
                f"if head -c 0 {a_read / 'store'}; then echo SELECTED_READABLE; else echo SELECTED_BLOCKED; fi\n"
                f"if head -c 0 {b_read / 'store'}; then echo SIBLING_READABLE; else echo SIBLING_BLOCKED; fi\n"
                f"if printf x > {a_write / 'created'}; then echo SELECTED_WRITABLE; else echo SELECTED_WRITE_BLOCKED; fi\n",
                encoding="utf-8",
            )
            a_script.chmod(a_script.stat().st_mode | stat.S_IXUSR)
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(os.environ, {"PATH": f"{scripts}:{os.environ.get('PATH', '')}"}, clear=False):
                    result = runtime.exec_command(
                        {"cmd": "a-cli", "timeout_ms": 5000, "yield_time_ms": 5000}
                    )
            finally:
                runtime.close()
            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertIn("SELECTED_READABLE", result.get("stdout", ""))
            self.assertIn("SIBLING_BLOCKED", result.get("stdout", ""))
            self.assertIn("SELECTED_WRITABLE", result.get("stdout", ""))
            self.assertTrue((a_write / "created").exists())

    def test_provider_b_cannot_open_provider_a_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            scripts = root / "scripts"
            scripts.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            a_read = broker_dir / "a" / "read"
            a_write = broker_dir / "a" / "write"
            b_read = broker_dir / "b" / "read"
            b_write = broker_dir / "b" / "write"
            self._write_provider(registry_dir, broker_dir, "a", "a-cli", a_read, a_write)
            self._write_provider(registry_dir, broker_dir, "b", "b-cli", b_read, b_write)
            b_script = scripts / "b-cli"
            b_script.write_text(
                "#!/bin/sh\n"
                f"if head -c 0 {a_read / 'store'}; then echo A_READABLE; else echo A_BLOCKED; fi\n",
                encoding="utf-8",
            )
            b_script.chmod(b_script.stat().st_mode | stat.S_IXUSR)
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(os.environ, {"PATH": f"{scripts}:{os.environ.get('PATH', '')}"}, clear=False):
                    result = runtime.exec_command(
                        {"cmd": "b-cli", "timeout_ms": 5000, "yield_time_ms": 5000}
                    )
            finally:
                runtime.close()
            self.assertEqual(result.get("exit_code"), 0, result)
            self.assertIn("A_BLOCKED", result.get("stdout", ""))

    def test_credential_sandbox_failure_fails_closed_before_spawn(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch(
                    "coding_tools_mcp.server.open_credential_landlock_ruleset",
                    side_effect=ToolFailure("SANDBOX_UNAVAILABLE", "unavailable", category="security"),
                ) as open_ruleset:
                    with self.assertRaises(ToolFailure) as raised:
                        runtime.exec_command({"cmd": "echo SHOULD_NOT_RUN", "timeout_ms": 5000})
            finally:
                runtime.close()
            self.assertEqual(raised.exception.code, "CREDENTIAL_SANDBOX_UNAVAILABLE")
            open_ruleset.assert_called_once()

    def test_restrict_self_failure_fails_closed_before_requested_command_spawn(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                failed_preflight = subprocess.CompletedProcess(
                    args=["landlock_exec"], returncode=126, stdout="", stderr="restrict_self failed"
                )
                with (
                    patch.object(server_module.subprocess, "run", return_value=failed_preflight) as preflight,
                    patch.object(
                        server_module,
                        "spawn_process",
                        side_effect=AssertionError("requested command must not spawn"),
                    ),
                ):
                    with self.assertRaises(ToolFailure) as raised:
                        runtime.exec_command({"cmd": "echo SHOULD_NOT_RUN", "timeout_ms": 5000})
            finally:
                runtime.close()
            self.assertEqual(raised.exception.code, "CREDENTIAL_SANDBOX_UNAVAILABLE")
            preflight.assert_called_once()

    def test_metadata_reports_all_exec_credential_isolation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                roots = runtime._credential_landlock_roots("echo ok", workspace)
                isolation = runtime.server_info_payload()["credential_providers"]["filesystem_isolation"]
            finally:
                runtime.close()
            self.assertIn(Path("/etc/git/gitignore_global"), roots.read_roots)
            self.assertEqual(isolation["backend"], "landlock")
            self.assertEqual(isolation["enforced_for"], "all_exec")
            self.assertIn(isolation["status"], {"available", "unavailable"})

    def test_mise_system_user_and_project_layers_are_readable_without_home_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            user_home = root / "home" / "yuzu"
            user_config = user_home / ".config" / "mise"
            user_data = user_home / ".local" / "share" / "mise"
            system_data = root / "usr-local-share-mise"
            global_config = user_config / "config.toml"
            system_config = root / "etc-mise" / "config.toml"
            for path in (user_config, user_data, system_data, system_config.parent):
                path.mkdir(parents=True, exist_ok=True)
            global_config.write_text("[tools]\nuser-tool = \"1\"\n", encoding="utf-8")
            system_config.write_text("[tools]\nsystem-tool = \"1\"\n", encoding="utf-8")
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                shell_env_policy=server_module.ShellEnvPolicy(inherit="all"),
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            host_env = {
                "PATH": "/usr/bin",
                "MISE_CONFIG_DIR": str(user_config),
                "MISE_DATA_DIR": str(user_data),
                "MISE_SYSTEM_DATA_DIR": str(system_data),
                "MISE_GLOBAL_CONFIG_FILE": str(global_config),
                "MISE_SYSTEM_CONFIG_FILE": str(system_config),
            }
            try:
                with patch.dict(server_module.os.environ, host_env, clear=True):
                    roots = runtime._credential_landlock_roots("mise run verify", workspace)
            finally:
                runtime.close()

            for expected in (
                workspace.resolve(),
                user_config.resolve(),
                user_data.resolve(),
                system_data.resolve(),
                global_config.resolve(),
                system_config.resolve(),
            ):
                self.assertIn(expected, roots.read_roots)
            self.assertNotIn(user_home.resolve(), roots.read_roots)

    def test_dangerous_registry_preserves_global_tmp_write_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                roots = runtime._credential_landlock_roots("mise run verify", workspace)
            finally:
                runtime.close()

            self.assertEqual(runtime.global_tmp_write_policy(), "allowed")
            for raw in (Path("/tmp"), Path("/var/tmp")):
                if raw.exists():
                    resolved = raw.resolve(strict=False)
                    self.assertIn(resolved, roots.read_roots)
                    self.assertIn(resolved, roots.write_roots)

    def test_mise_read_root_cannot_encompass_credential_broker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            registry_dir = root / "credentials.d"
            broker_dir = root / "mise-data" / "credentials"
            registry_dir.mkdir()
            broker_dir.mkdir(parents=True)
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                shell_env_policy=server_module.ShellEnvPolicy(inherit="all"),
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(
                    server_module.os.environ,
                    {"PATH": "/usr/bin", "MISE_DATA_DIR": str(broker_dir.parent)},
                    clear=True,
                ):
                    with self.assertRaises(ToolFailure) as raised:
                        runtime._credential_landlock_roots("mise run verify", workspace)
            finally:
                runtime.close()

            self.assertEqual(raised.exception.code, "CREDENTIAL_SANDBOX_UNAVAILABLE")

    def test_mise_config_symlink_target_is_added_as_exact_read_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            user_config = root / "home" / "yuzu" / ".config" / "mise"
            task_dir = user_config / "tasks"
            task_dir.mkdir(parents=True)
            external_dir = root / "shared" / "mise-tasks"
            external_dir.mkdir(parents=True)
            external_task = external_dir / "status"
            external_task.write_text("#!/bin/sh\n", encoding="utf-8")
            (task_dir / "status").symlink_to(external_task)
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                shell_env_policy=server_module.ShellEnvPolicy(inherit="all"),
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(
                    server_module.os.environ,
                    {"PATH": "/usr/bin", "MISE_CONFIG_DIR": str(user_config)},
                    clear=True,
                ):
                    roots = runtime._credential_landlock_roots("mise run verify", workspace)
            finally:
                runtime.close()

            self.assertIn(external_task.resolve(), roots.read_roots)
            self.assertNotIn(external_dir.resolve(), roots.read_roots)

    def test_mise_node_cli_allows_exact_installed_tool_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            executable = root / "mise" / "installs" / "npm-neon" / "3.6.0" / "bin" / "neon"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            registry_dir = root / "credentials.d"
            broker_dir = root / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(server_module.os.environ, {"PATH": str(executable.parent)}, clear=True):
                    roots = runtime._credential_landlock_roots("neon --version", workspace)
            finally:
                runtime.close()
            self.assertIn(executable.parent.parent, roots.read_roots)
            self.assertNotIn(broker_dir, roots.read_roots)

    def test_mise_tool_root_never_encompasses_credential_broker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            executable = root / "mise" / "installs" / "npm-neon" / "3.6.0" / "bin" / "neon"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            registry_dir = root / "credentials.d"
            broker_dir = executable.parent.parent / "broker"
            registry_dir.mkdir()
            broker_dir.mkdir()
            runtime = Runtime(
                workspace,
                permission_mode="dangerous",
                credential_registry=CredentialProviderRegistry(registry_dir, broker_dir),
            )
            try:
                with patch.dict(server_module.os.environ, {"PATH": str(executable.parent)}, clear=True):
                    roots = runtime._credential_landlock_roots("neon --version", workspace)
            finally:
                runtime.close()
            self.assertNotIn(executable.parent.parent, roots.read_roots)


if __name__ == "__main__":
    unittest.main()
