from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.server import Runtime, WorkspaceCommandManager


class ProjectCommandRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        for project in (self.bootstrap, self.alpha, self.beta):
            project.mkdir()
            (project / "pyproject.toml").write_text(
                f"[project]\nname='{project.name}'\nversion='0'\n",
                encoding="utf-8",
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self) -> Runtime:
        config = RuntimeConfig.defaults(
            enabled=("projects",),
            settings={
                "projects": {
                    "registry": {
                        "alpha": {"root": str(self.alpha)},
                        "beta": {"root": str(self.beta)},
                    }
                }
            },
        )
        return Runtime(
            self.bootstrap,
            extension_config=config,
            permission_mode="dangerous",
        )

    def test_command_manager_callbacks_cover_registration_ttl_eviction_and_close(self) -> None:
        registered: list[str] = []
        removed: list[str] = []
        manager = WorkspaceCommandManager(
            self.bootstrap,
            on_command_registered=registered.append,
            on_command_removed=removed.append,
        )
        runtime = Runtime(
            self.bootstrap,
            command_manager=manager,
            extension_config=RuntimeConfig.defaults(enabled=()),
            permission_mode="dangerous",
        )
        try:
            first = runtime.exec_command(
                {
                    "cmd": "python -u -c \"print('first')\"",
                    "yield_time_ms": 1000,
                }
            )
            first_id = str(first["command_id"])
            self.assertEqual(registered, [first_id])

            runtime.get_command({"command_id": first_id})
            with runtime.commands_lock:
                retained = runtime.output_commands[first_id]
                retained.completed_at = time.time() - 10
            with patch("coding_tools_mcp.server.COMPLETED_COMMAND_TTL_SECONDS", 1):
                runtime.list_commands({})
            self.assertEqual(removed, [first_id])

            second = runtime.exec_command(
                {
                    "cmd": "python -u -c \"import time; print('second', flush=True); time.sleep(60)\"",
                    "yield_time_ms": 0,
                }
            )
            second_id = str(second["command_id"])
            self.assertEqual(registered, [first_id, second_id])
        finally:
            runtime.close()
            manager.close()
        self.assertEqual(removed, [first_id, second_id])

    def test_opaque_command_id_routes_without_project_id(self) -> None:
        runtime = self.runtime()
        try:
            alpha = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python -u -c \"import time; print('ALPHA_READY', flush=True); time.sleep(60)\"",
                    "yield_time_ms": 0,
                },
            )["structuredContent"]
            beta = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "cmd": "python -u -c \"import time; print('BETA_READY', flush=True); time.sleep(60)\"",
                    "yield_time_ms": 0,
                },
            )["structuredContent"]

            alpha_id = str(alpha["command_id"])
            beta_id = str(beta["command_id"])
            alpha_poll = runtime.call_tool(
                "write_stdin",
                {"command_id": alpha_id, "chars": "", "yield_time_ms": 100},
            )["structuredContent"]
            beta_poll = runtime.call_tool(
                "write_stdin",
                {"command_id": beta_id, "chars": "", "yield_time_ms": 100},
            )["structuredContent"]

            self.assertEqual(alpha_poll["project_id"], "alpha")
            self.assertEqual(beta_poll["project_id"], "beta")
            self.assertEqual(alpha_poll["command_id"], alpha_id)
            self.assertEqual(beta_poll["command_id"], beta_id)

            alpha_kill = runtime.call_tool("kill_command", {"command_id": alpha_id})
            beta_kill = runtime.call_tool("kill_command", {"command_id": beta_id})
            self.assertFalse(alpha_kill["isError"])
            self.assertFalse(beta_kill["isError"])
        finally:
            runtime.close()

    def test_read_output_routes_retained_output_by_opaque_ref(self) -> None:
        runtime = self.runtime()
        try:
            started = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python -u -c \"print('ALPHA_OUTPUT')\"",
                    "yield_time_ms": 1000,
                    "verbosity": "full",
                },
            )["structuredContent"]
            output_ref = started["output_refs"]["stdout"]

            read = runtime.call_tool(
                "read_output",
                {"output_ref": output_ref, "offset": 0, "limit": 4096},
            )["structuredContent"]

            self.assertEqual(read["project_id"], "alpha")
            self.assertIn("ALPHA_OUTPUT", read["content"])
        finally:
            runtime.close()

    def test_same_client_request_id_is_independent_per_project(self) -> None:
        runtime = self.runtime()
        try:
            alpha = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python -u -c \"print('alpha')\"",
                    "client_request_id": "same-id",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]
            beta = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "cmd": "python -u -c \"print('beta')\"",
                    "client_request_id": "same-id",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]
            self.assertNotEqual(alpha["command_id"], beta["command_id"])

            missing_project = runtime.call_tool("get_command", {"client_request_id": "same-id"})
            self.assertTrue(missing_project["isError"])
            self.assertEqual(
                missing_project["structuredContent"]["error"]["code"],
                "INVALID_ARGUMENT",
            )

            alpha_recovered = runtime.call_tool(
                "get_command",
                {"project_id": "alpha", "client_request_id": "same-id"},
            )["structuredContent"]
            beta_recovered = runtime.call_tool(
                "get_command",
                {"project_id": "beta", "client_request_id": "same-id"},
            )["structuredContent"]
            self.assertEqual(alpha_recovered["command_id"], alpha["command_id"])
            self.assertEqual(beta_recovered["command_id"], beta["command_id"])

            conflict = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python -u -c \"print('different')\"",
                    "client_request_id": "same-id",
                    "yield_time_ms": 1000,
                },
            )
            self.assertTrue(conflict["isError"])
            self.assertEqual(conflict["structuredContent"]["error"]["code"], "IDEMPOTENCY_CONFLICT")
        finally:
            runtime.close()

    def test_get_command_by_command_id_needs_no_project_id(self) -> None:
        runtime = self.runtime()
        try:
            started = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "cmd": "python -u -c \"print('beta')\"",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]
            recovered = runtime.call_tool(
                "get_command",
                {"command_id": started["command_id"]},
            )["structuredContent"]
            self.assertEqual(recovered["project_id"], "beta")
            self.assertEqual(recovered["command_id"], started["command_id"])
        finally:
            runtime.close()

    def test_list_commands_project_filter_client_request_and_aggregate(self) -> None:
        runtime = self.runtime()
        try:
            alpha = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python -u -c \"print('alpha')\"",
                    "client_request_id": "shared",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]
            beta = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "cmd": "python -u -c \"print('beta')\"",
                    "client_request_id": "shared",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]

            missing_project = runtime.call_tool("list_commands", {"client_request_id": "shared"})
            self.assertTrue(missing_project["isError"])
            self.assertEqual(
                missing_project["structuredContent"]["error"]["code"],
                "INVALID_ARGUMENT",
            )

            alpha_list = runtime.call_tool(
                "list_commands",
                {"project_id": "alpha", "client_request_id": "shared"},
            )["structuredContent"]
            self.assertEqual([item["command_id"] for item in alpha_list["commands"]], [alpha["command_id"]])
            self.assertEqual(alpha_list["commands"][0]["project_id"], "alpha")

            aggregate = runtime.call_tool("list_commands", {})["structuredContent"]
            owners = {item["command_id"]: item["project_id"] for item in aggregate["commands"]}
            self.assertEqual(owners[alpha["command_id"]], "alpha")
            self.assertEqual(owners[beta["command_id"]], "beta")
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
