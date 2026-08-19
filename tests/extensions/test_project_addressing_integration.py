from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.compliance.mcp_client import MCPClient, StdioMCPClient


PROJECT_SCOPED_CORE_TOOLS = (
    "check_exec_environment",
    "read_file",
    "list_dir",
    "list_files",
    "search_text",
    "apply_patch",
    "exec_command",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_blame",
    "view_image",
)


class ProjectAddressingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.mkdir()
        (self.bootstrap / "pyproject.toml").write_text(
            "[project]\nname='bootstrap'\nversion='0'\n",
            encoding="utf-8",
        )
        self.projects: dict[str, Path] = {}
        for project_id in ("alpha", "beta", "gamma", "delta"):
            project = self.root / project_id
            project.mkdir()
            (project / "pyproject.toml").write_text(
                f"[project]\nname='{project_id}'\nversion='0'\n",
                encoding="utf-8",
            )
            (project / "identity.txt").write_text(f"{project_id.upper()}\n", encoding="utf-8")
            self.projects[project_id] = project

        self.public_config = self.root / "public.toml"
        self.local_config = self.root / "local.toml"
        self.public_config.write_text(
            "config_version = 1\n\n[extensions]\nenabled = ['projects']\n\n[extensions.projects]\n",
            encoding="utf-8",
        )
        local_lines = ["config_version = 1", ""]
        for project_id, root in self.projects.items():
            local_lines.extend(
                [
                    f"[extensions.projects.registry.{project_id}]",
                    f'root = "{root.as_posix()}"',
                    "",
                ]
            )
        self.local_config.write_text("\n".join(local_lines), encoding="utf-8")
        self.extra_args = [
            "--config",
            str(self.public_config),
            "--local-config",
            str(self.local_config),
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stdio_layered_config_exposes_fixed_multi_project_contract(self) -> None:
        with StdioMCPClient(self.bootstrap, extra_args=self.extra_args) as client:
            tools = {tool["name"]: tool for tool in client.rpc("tools/list")["tools"]}
            self.assertEqual(len(tools), 26)
            self.assertIn("list_projects", tools)
            self.assertIn("resolve_project", tools)
            self.assertIn("project_context", tools)
            self.assertIn("doctor", tools)
            for name in (*PROJECT_SCOPED_CORE_TOOLS, "list_skills", "read_skill", "project_context"):
                with self.subTest(name=name):
                    self.assertIn("project_id", tools[name]["inputSchema"]["required"])

            listed = client.call_tool("list_projects", {})["structuredContent"]
            self.assertEqual(
                [project["id"] for project in listed["projects"]],
                ["alpha", "beta", "gamma", "delta"],
            )
            for project_id in self.projects:
                result = client.call_tool(
                    "read_file",
                    {"project_id": project_id, "path": "identity.txt"},
                )["structuredContent"]
                self.assertEqual(result["content"], f"{project_id.upper()}\n")

            with self.assertRaises(AssertionError):
                client.call_tool("read_file", {"path": "identity.txt"})

    def test_http_one_endpoint_routes_four_projects_concurrently_without_activation(self) -> None:
        with MCPClient(self.bootstrap, extra_args=self.extra_args) as owner:
            names = {tool["name"] for tool in owner.list_tools()}
            for forbidden in ("activate_project", "set_project", "select_project", "cd"):
                self.assertNotIn(forbidden, names)

            targets = list(self.projects) * 25

            def read(project_id: str) -> tuple[str, str]:
                with MCPClient(self.bootstrap, url=owner.url) as client:
                    result = client.call_tool(
                        "read_file",
                        {"project_id": project_id, "path": "identity.txt"},
                    )["structuredContent"]
                    return project_id, str(result["content"])

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(read, targets))

            for project_id, content in results:
                self.assertEqual(content, f"{project_id.upper()}\n")

    def test_http_command_recovery_is_stateless_and_client_request_is_project_scoped(self) -> None:
        with MCPClient(self.bootstrap, extra_args=self.extra_args) as owner:
            started = owner.call_tool(
                "exec_command",
                {
                    "project_id": "alpha",
                    "cmd": "python --version",
                    "client_request_id": "alpha-version",
                    "yield_time_ms": 1000,
                },
            )["structuredContent"]
            command_id = str(started["command_id"])

            with MCPClient(self.bootstrap, url=owner.url) as sibling:
                by_id = sibling.call_tool(
                    "get_command",
                    {"command_id": command_id},
                )["structuredContent"]
                self.assertEqual(by_id["command_id"], command_id)
                self.assertEqual(by_id["project_id"], "alpha")

                missing_scope = sibling.call_tool(
                    "get_command",
                    {"client_request_id": "alpha-version"},
                )
                self.assertTrue(missing_scope["isError"])
                self.assertEqual(
                    missing_scope["structuredContent"]["error"]["code"],
                    "INVALID_ARGUMENT",
                )

                by_request = sibling.call_tool(
                    "get_command",
                    {"project_id": "alpha", "client_request_id": "alpha-version"},
                )["structuredContent"]
                self.assertEqual(by_request["command_id"], command_id)


if __name__ == "__main__":
    unittest.main()
