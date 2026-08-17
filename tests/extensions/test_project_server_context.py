from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.server import Runtime


class ProjectServerContextTests(unittest.TestCase):
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
        (self.bootstrap / "AGENTS.md").write_text("BOOTSTRAP_PRIVATE_INSTRUCTIONS\n", encoding="utf-8")
        (self.alpha / "AGENTS.md").write_text("ALPHA_PRIVATE_INSTRUCTIONS\n", encoding="utf-8")
        (self.beta / "AGENTS.md").write_text("BETA_PRIVATE_INSTRUCTIONS\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self, *, permission_mode: str = "dangerous") -> Runtime:
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
            permission_mode=permission_mode,
        )

    def test_handshake_and_discover_are_project_neutral(self) -> None:
        runtime = self.runtime()
        try:
            handshake = runtime.initialize_result()["instructions"]
            discover = runtime.discover_payload()["instructions"]
            for instructions in (handshake, discover):
                self.assertIn("project_id", instructions)
                self.assertIn("list_projects", instructions)
                self.assertIn("project-scoped", instructions)
                self.assertNotIn("BOOTSTRAP_PRIVATE_INSTRUCTIONS", instructions)
                self.assertNotIn("ALPHA_PRIVATE_INSTRUCTIONS", instructions)
                self.assertNotIn("BETA_PRIVATE_INSTRUCTIONS", instructions)

            skills = runtime.call_tool(
                "list_skills",
                {"project_id": "alpha", "workdir": "."},
            )["structuredContent"]
            self.assertIn("AGENTS.md", skills["instruction_files"])
        finally:
            runtime.close()

    def test_projects_disabled_preserves_core_workspace_instructions(self) -> None:
        runtime = Runtime(
            self.bootstrap,
            extension_config=RuntimeConfig.defaults(enabled=()),
            permission_mode="dangerous",
        )
        try:
            instructions = runtime.initialize_result()["instructions"]
            self.assertIn("BOOTSTRAP_PRIVATE_INSTRUCTIONS", instructions)
            self.assertNotIn("list_projects", instructions)
        finally:
            runtime.close()

    def test_server_info_reports_global_multi_project_summary_not_bootstrap_state(self) -> None:
        runtime = self.runtime()
        try:
            payload = runtime.call_tool("server_info", {})["structuredContent"]

            for bootstrap_field in (
                "workspace",
                "runtime_dir",
                "home",
                "tmpdir",
                "cache_dir",
                "project_context",
            ):
                self.assertNotIn(bootstrap_field, payload)
            self.assertEqual(payload["projects"]["count"], 2)
            self.assertEqual(payload["projects"]["ids"], ["alpha", "beta"])
            self.assertEqual(payload["projects"]["available"], 2)
            self.assertEqual(payload["tool_count"], 24)
        finally:
            runtime.close()

    def test_dangerous_permission_request_uses_nested_project_target(self) -> None:
        runtime = self.runtime(permission_mode="dangerous")
        try:
            result = runtime.call_tool(
                "request_permissions",
                {
                    "tool_name": "exec_command",
                    "permission": "network",
                    "reason": "project-scoped test",
                    "arguments": {
                        "project_id": "beta",
                        "cmd": "python -c \"print('ok')\"",
                    },
                    "scope": "once",
                    "ttl_seconds": 60,
                },
            )["structuredContent"]

            self.assertEqual(Path(result["constraints"]["workspace"]), self.beta.resolve())
            requested = result["constraints"]["requested"]
            self.assertEqual(requested["arguments"]["project_id"], "beta")
            self.assertEqual(requested["arguments"]["cmd"], "python -c \"print('ok')\"")
        finally:
            runtime.close()

    def test_permission_request_for_project_scoped_tool_requires_nested_project_id(self) -> None:
        runtime = self.runtime(permission_mode="dangerous")
        try:
            result = runtime.call_tool(
                "request_permissions",
                {
                    "tool_name": "apply_patch",
                    "permission": "destructive_command",
                    "reason": "missing target",
                    "arguments": {"patch": "*** Begin Patch\n*** End Patch"},
                    "scope": "once",
                    "ttl_seconds": 60,
                },
            )
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["error"]["code"], "INVALID_ARGUMENT")
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
