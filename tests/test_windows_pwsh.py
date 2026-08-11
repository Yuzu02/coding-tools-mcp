from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_mcp import processes
from coding_tools_mcp import server
from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.server import Runtime, ShellEnvPolicy


class WindowsPowerShellSpawnTests(unittest.TestCase):
    def tearDown(self) -> None:
        processes.pwsh_major_version.cache_clear()

    def test_windows_string_command_uses_server_resolved_noninteractive_pwsh(self) -> None:
        captured: dict[str, object] = {}

        class FakeProcess:
            pass

        def fake_popen(command: object, **kwargs: object) -> FakeProcess:
            captured["command"] = command
            captured.update(kwargs)
            return FakeProcess()

        trusted = r"C:\Program Files\PowerShell\7\pwsh.exe"
        trusted_path = r"C:\Program Files\PowerShell\7;C:\Windows\System32"
        attacker_path = r"C:\workspace\bin"

        with (
            patch.object(processes.os, "name", "nt"),
            patch.object(processes.os, "getcwd", return_value=r"C:\server"),
            patch.dict(processes.os.environ, {"Path": trusted_path}, clear=True),
            patch.object(processes.os.path, "isfile", side_effect=lambda path: path == trusted),
            patch.object(processes, "pwsh_major_version", return_value=7),
            patch.object(processes.subprocess, "Popen", side_effect=fake_popen),
        ):
            process, pty_fd = processes.spawn_process(
                "Write-Output 'ok'",
                cwd=r"C:\workspace",
                shell=True,
                env={"Path": attacker_path},
                tty=False,
                popen_kwargs={},
            )

        self.assertIsInstance(process, FakeProcess)
        self.assertIsNone(pty_fd)
        self.assertEqual(
            captured["command"],
            [
                trusted,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Write-Output 'ok'",
            ],
        )
        self.assertIs(captured["shell"], False)
        self.assertEqual(captured["env"], {"Path": attacker_path})

    def test_windows_pwsh_resolution_skips_current_directory_entries(self) -> None:
        trusted = r"C:\Program Files\PowerShell\7\pwsh.exe"
        malicious = r"C:\workspace\pwsh.exe"

        with (
            patch.object(processes.os, "name", "nt"),
            patch.object(processes.os, "getcwd", return_value=r"C:\workspace"),
            patch.dict(
                processes.os.environ,
                {"Path": r".;C:\workspace;C:\Program Files\PowerShell\7"},
                clear=True,
            ),
            patch.object(
                processes.os.path,
                "isfile",
                side_effect=lambda path: path in {trusted, malicious},
            ),
            patch.object(processes, "pwsh_major_version", return_value=7),
        ):
            self.assertEqual(processes.resolve_pwsh(), trusted)

    def test_windows_pwsh_rejects_invalid_explicit_path(self) -> None:
        with (
            patch.object(processes.os, "name", "nt"),
            patch.dict(
                processes.os.environ,
                {processes.PWSH_PATH_ENV: r".\pwsh.exe"},
                clear=True,
            ),
            patch.object(processes.os.path, "isfile", return_value=True),
        ):
            with self.assertRaises(ToolFailure) as raised:
                processes.resolve_pwsh()

        self.assertEqual(raised.exception.code, "SHELL_NOT_FOUND")

    def test_windows_string_command_requires_pwsh(self) -> None:
        with (
            patch.object(processes.os, "name", "nt"),
            patch.object(processes.os, "getcwd", return_value=r"C:\server"),
            patch.dict(processes.os.environ, {"Path": r"C:\Windows\System32"}, clear=True),
            patch.object(processes.os.path, "isfile", return_value=False),
        ):
            with self.assertRaises(ToolFailure) as raised:
                processes.resolve_pwsh()

        self.assertEqual(raised.exception.code, "SHELL_NOT_FOUND")

    def test_windows_string_command_rejects_powershell_older_than_7(self) -> None:
        executable = r"C:\PowerShell\6\pwsh.exe"
        with (
            patch.object(processes.os, "name", "nt"),
            patch.object(processes.os, "getcwd", return_value=r"C:\server"),
            patch.dict(processes.os.environ, {"Path": r"C:\PowerShell\6"}, clear=True),
            patch.object(processes.os.path, "isfile", side_effect=lambda path: path == executable),
            patch.object(processes, "pwsh_major_version", return_value=6),
        ):
            with self.assertRaises(ToolFailure) as raised:
                processes.resolve_pwsh()

        self.assertEqual(raised.exception.code, "SHELL_VERSION_UNSUPPORTED")

    @unittest.skipUnless(os.name == "nt", "requires Windows PowerShell 7")
    def test_runtime_executes_bare_powershell_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(
                Path(tmp),
                permission_mode="trusted",
                shell_env_policy=ShellEnvPolicy(inherit="all"),
            )
            try:
                result = runtime.exec_command(
                    {
                        "cmd": "Write-Output ('PS_MAJOR=' + $PSVersionTable.PSVersion.Major)",
                        "timeout_ms": 30_000,
                        "yield_time_ms": 30_000,
                        "verbosity": "full",
                    }
                )
            finally:
                runtime.close()

        self.assertEqual(result.get("status"), "exited", result)
        self.assertEqual(result.get("exit_code"), 0, result)
        self.assertIn("PS_MAJOR=7", str(result.get("stdout", "")))


class WindowsPowerShellPolicyTests(unittest.TestCase):
    def test_safe_mode_blocks_powershell_network_commands_without_url_literals(self) -> None:
        commands = (
            "Invoke-WebRequest -Uri example.com",
            "Invoke-RestMethod -Uri api.example.com",
            "iwr example.com",
            "irm api.example.com",
            "Start-BitsTransfer -Source example.com -Destination out.bin",
            "New-Object System.Net.WebClient",
            "Test-NetConnection example.com",
            "Test-Connection example.com",
            "ping example.com",
            "tnc example.com",
            "Resolve-DnsName example.com",
            "[System.Net.Dns]::GetHostAddresses('example.com')",
            "Write-Output ok\nInvoke-WebRequest -Uri example.com",
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp), permission_mode="safe")
            try:
                for command in commands:
                    with self.subTest(command=command):
                        with self.assertRaises(ToolFailure) as raised:
                            runtime._check_command_policy(command, {})
                        self.assertEqual(raised.exception.details.get("permission"), "network")
            finally:
                runtime.close()

    def test_safe_mode_blocks_recursive_powershell_deletion_aliases_and_abbreviations(self) -> None:
        commands = (
            "Remove-Item -Recurse .",
            "Remove-Item -Recurse -Force .",
            "Remove-Item -Force -LiteralPath . -Recurse",
            "Remove-Item -Recu -For .",
            "rm -r .",
            "rm -Recurse -Force .",
            "ri -Recu .",
            "ri -Force -Recurse .",
            "del -Recurse .",
            "rmdir -r .",
            "Write-Output ok\r\nRemove-Item -Recurse .",
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp), permission_mode="safe")
            try:
                for command in commands:
                    with self.subTest(command=command):
                        with self.assertRaises(ToolFailure) as raised:
                            runtime._check_command_policy(command, {})
                        self.assertEqual(
                            raised.exception.details.get("permission"),
                            "destructive_command",
                        )
            finally:
                runtime.close()


class WindowsPowerShellDynamicSyntaxTests(unittest.TestCase):
    """PowerShell resolves commands at runtime, so cmdlet-name scanning alone
    cannot decide whether a command is destructive or reaches the network."""

    def assert_policy(self, command: str, *, mode: str = "safe") -> ToolFailure | None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp), permission_mode=mode)
            try:
                with patch.object(server, "powershell_executes_string_commands", return_value=True):
                    try:
                        runtime._check_command_policy(command, {})
                    except ToolFailure as failure:
                        return failure
            finally:
                runtime.close()
        return None

    def test_safe_mode_gates_command_names_built_from_variables_and_splatting(self) -> None:
        # Both of these pass every cmdlet-name scan while still invoking a
        # network download and a recursive delete.
        cases = {
            "$c='Invoke-WebRequest'; & $c example.com": "expansion",
            "$p=@{Recurse=$true}; Remove-Item . @p": "expansion",
            "Set-Alias grab Invoke-WebRequest; grab example.com": "dynamic_eval",
            "[IO.File]::Delete('C:\\data\\report.csv')": "static_member",
            "Remove-Item . @args": "splatting",
            ". .\\payload.ps1": "call_operator",
            "iex (Get-Content payload.txt -Raw)": "dynamic_eval",
        }
        for command, construct in cases.items():
            with self.subTest(command=command):
                failure = self.assert_policy(command)
                self.assertIsNotNone(failure, f"{command!r} was allowed in safe mode")
                assert failure is not None
                self.assertEqual(failure.details.get("permission"), "shell_expansion")
                self.assertEqual(failure.details.get("construct"), construct)

    def test_safe_mode_gates_nested_shells_that_smuggle_encoded_scripts(self) -> None:
        for command in (
            "pwsh -EncodedCommand SQBuAHYAbwBrAGUA",
            "pwsh -enc SQBuAHYAbwBrAGUA",
            "pwsh -Command Invoke-WebRequest example.com",
            "powershell.exe -c whoami",
            "cmd /c del /s /q C:\\data",
        ):
            with self.subTest(command=command):
                failure = self.assert_policy(command)
                self.assertIsNotNone(failure, f"{command!r} was allowed in safe mode")
                assert failure is not None
                self.assertEqual(failure.details.get("permission"), "inline_script")

    def test_safe_mode_still_allows_literal_powershell_commands(self) -> None:
        for command in (
            "Get-ChildItem -Path src -Recurse -Name",
            "Write-Output ok",
            "git commit -m 'fix parser' && git log --oneline -1",
            "git config user.email dev@example.com",
            ".\\build.exe --release",
            "Get-Content README.md | Select-String planet.txt",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.assert_policy(command), f"{command!r} was blocked in safe mode")

    def test_trusted_mode_allows_dynamic_syntax(self) -> None:
        for command in ("$c='Get-Date'; & $c", "Remove-Item . @p"):
            with self.subTest(command=command):
                self.assertIsNone(self.assert_policy(command, mode="trusted"))

    def test_network_scan_does_not_flag_words_ending_in_net(self) -> None:
        self.assertIsNone(server.POWERSHELL_NETWORK_RE.search("Get-Content planet.txt"))
        self.assertIsNotNone(server.POWERSHELL_NETWORK_RE.search("New-Object System.Net.WebClient"))
        self.assertIsNotNone(server.POWERSHELL_NETWORK_RE.search("[Net.Dns]::GetHostAddresses('a')"))

    def test_posix_hosts_keep_their_existing_expansion_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp), permission_mode="safe")
            try:
                with patch.object(server, "powershell_executes_string_commands", return_value=False):
                    runtime._check_command_policy("echo $HOME", {})
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
