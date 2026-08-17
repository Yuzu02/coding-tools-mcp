from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.compliance.mcp_client import MCPClient


def structured(result: dict[str, object]) -> dict[str, object]:
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        raise AssertionError(f"tool result lacks structuredContent: {result!r}")
    return payload


class ReliableCommandRecoveryHTTPTests(unittest.TestCase):
    def test_retry_and_recovery_work_across_fresh_http_sessions(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        key = "http-recovery-integration"
        command = (
            "Start-Sleep -Seconds 2; Write-Output recovery-ok"
            if os.name == "nt"
            else "sleep 2; printf 'recovery-ok\\n'"
        )
        server_command = (
            "{python} -m coding_tools_mcp --workspace {workspace} "
            "--host 127.0.0.1 --port {port} --permission-mode trusted"
        )
        arguments = {
            "cmd": command,
            "workdir": ".",
            "yield_time_ms": 0,
            "timeout_ms": 10_000,
            "client_request_id": key,
        }

        with patch.dict(
            os.environ,
            {"CODING_TOOLS_MCP_SERVER_CMD": server_command},
            clear=False,
        ):
            with MCPClient(workspace, default_project_id="default") as owner:
                first = structured(owner.call_tool("exec_command", arguments))
                with MCPClient(
                    workspace,
                    url=owner.url,
                    default_project_id="default",
                ) as sibling:
                    second = structured(sibling.call_tool("exec_command", arguments))
                    recovered = structured(
                        sibling.call_tool(
                            "get_command",
                            {
                                "project_id": "default",
                                "client_request_id": key,
                                "max_output_bytes": 4096,
                            },
                        )
                    )
                    listed = structured(
                        sibling.call_tool(
                            "list_commands",
                            {
                                "project_id": "default",
                                "client_request_id": key,
                                "status": "all",
                                "limit": 10,
                            },
                        )
                    )

                command_id = str(first["command_id"])
                final = first
                captured_stdout = str(first.get("stdout", ""))
                deadline = time.time() + 8
                while final.get("status") == "running" and time.time() < deadline:
                    final = structured(
                        owner.call_tool(
                            "write_stdin",
                            {
                                "command_id": command_id,
                                "chars": "",
                                "yield_time_ms": 1000,
                            },
                        )
                    )
                    captured_stdout += str(final.get("stdout", ""))

        self.assertEqual(second.get("command_id"), command_id)
        self.assertIs(second.get("deduplicated"), True)
        self.assertEqual(recovered.get("command_id"), command_id)
        commands = listed.get("commands")
        self.assertIsInstance(commands, list)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].get("command_id"), command_id)
        self.assertEqual(final.get("status"), "exited")
        self.assertEqual(final.get("exit_code"), 0)
        self.assertIn("recovery-ok", captured_stdout)


if __name__ == "__main__":
    unittest.main()
