from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.server import Runtime


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


class ProjectToolRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        for project, identity in (
            (self.bootstrap, "BOOTSTRAP"),
            (self.alpha, "ALPHA"),
            (self.beta, "BETA"),
        ):
            project.mkdir()
            (project / "pyproject.toml").write_text(
                f"[project]\nname='{project.name}'\nversion='0'\n",
                encoding="utf-8",
            )
            (project / "same.txt").write_text(f"{identity}\n", encoding="utf-8")
        self._init_repo(self.alpha, "alpha")
        self._init_repo(self.beta, "beta")
        (self.alpha / "alpha-untracked.txt").write_text("alpha only\n", encoding="utf-8")
        (self.beta / "beta-untracked.txt").write_text("beta only\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _init_repo(self, project: Path, label: str) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project, check=True)
        blame = project / "blame.txt"
        blame.write_text("one\ntwo\nthree\nfour\nfive\nsix\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"{label}-first"], cwd=project, check=True)
        (project / "history.txt").write_text(f"{label} second\n", encoding="utf-8")
        subprocess.run(["git", "add", "history.txt"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"{label}-second"], cwd=project, check=True)

    def runtime(self, *, enable_view_image: bool = True) -> Runtime:
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
            enable_view_image=enable_view_image,
        )

    def test_all_project_scoped_core_schemas_require_project_id(self) -> None:
        runtime = self.runtime()
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            for name in PROJECT_SCOPED_CORE_TOOLS:
                with self.subTest(name=name):
                    schema = tools[name]["inputSchema"]
                    self.assertIn("project_id", schema["properties"])
                    self.assertIn("project_id", schema["required"])

            for name in (
                "server_info",
                "list_projects",
                "resolve_project",
                "request_permissions",
                "list_commands",
                "get_command",
                "write_stdin",
                "kill_command",
                "read_output",
            ):
                with self.subTest(global_or_opaque=name):
                    self.assertNotIn(
                        "project_id",
                        tools[name]["inputSchema"].get("required", []),
                    )
        finally:
            runtime.close()

    def test_optional_view_image_target_does_not_break_startup_when_disabled(self) -> None:
        runtime = self.runtime(enable_view_image=False)
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            self.assertNotIn("view_image", tools)
            for name in PROJECT_SCOPED_CORE_TOOLS[:-1]:
                with self.subTest(name=name):
                    self.assertIn("project_id", tools[name]["inputSchema"]["required"])
        finally:
            runtime.close()

    def test_public_workdir_vocabulary_is_unambiguous(self) -> None:
        runtime = self.runtime()
        try:
            tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
            exec_properties = tools["exec_command"]["inputSchema"]["properties"]
            self.assertIn("workdir", exec_properties)
            self.assertNotIn("cwd", exec_properties)

            status_properties = tools["git_status"]["inputSchema"]["properties"]
            self.assertIn("workdir", status_properties)
            self.assertNotIn("path", status_properties)
        finally:
            runtime.close()

    def test_same_relative_paths_route_independently_for_read_list_search_and_environment(self) -> None:
        runtime = self.runtime()
        try:
            alpha_read = runtime.call_tool(
                "read_file",
                {"project_id": "alpha", "path": "same.txt"},
            )["structuredContent"]
            beta_read = runtime.call_tool(
                "read_file",
                {"project_id": "beta", "path": "same.txt"},
            )["structuredContent"]
            self.assertEqual(alpha_read["content"], "ALPHA\n")
            self.assertEqual(beta_read["content"], "BETA\n")
            self.assertEqual(alpha_read["project_id"], "alpha")
            self.assertEqual(beta_read["project_id"], "beta")

            alpha_files = runtime.call_tool(
                "list_files",
                {"project_id": "alpha", "path": ".", "patterns": ["*.txt"]},
            )["structuredContent"]
            beta_files = runtime.call_tool(
                "list_files",
                {"project_id": "beta", "path": ".", "patterns": ["*.txt"]},
            )["structuredContent"]
            self.assertIn("alpha-untracked.txt", [item["path"] for item in alpha_files["files"]])
            self.assertNotIn("beta-untracked.txt", [item["path"] for item in alpha_files["files"]])
            self.assertIn("beta-untracked.txt", [item["path"] for item in beta_files["files"]])

            alpha_search = runtime.call_tool(
                "search_text",
                {"project_id": "alpha", "query": "ALPHA", "path": "."},
            )["structuredContent"]
            beta_search = runtime.call_tool(
                "search_text",
                {"project_id": "beta", "query": "BETA", "path": "."},
            )["structuredContent"]
            self.assertGreater(alpha_search["total_matches"], 0)
            self.assertGreater(beta_search["total_matches"], 0)

            alpha_env = runtime.call_tool(
                "check_exec_environment",
                {"project_id": "alpha"},
            )["structuredContent"]
            beta_env = runtime.call_tool(
                "check_exec_environment",
                {"project_id": "beta"},
            )["structuredContent"]
            self.assertEqual(Path(alpha_env["workspace"]), self.alpha.resolve())
            self.assertEqual(Path(beta_env["workspace"]), self.beta.resolve())
        finally:
            runtime.close()

    def test_apply_patch_exec_and_git_use_selected_project(self) -> None:
        runtime = self.runtime()
        try:
            patch = """*** Begin Patch
*** Update File: same.txt
@@
-ALPHA
+ALPHA_PATCHED
*** End Patch"""
            applied = runtime.call_tool(
                "apply_patch",
                {"project_id": "alpha", "patch": patch},
            )
            self.assertFalse(applied["isError"])
            self.assertEqual((self.alpha / "same.txt").read_text(encoding="utf-8"), "ALPHA_PATCHED\n")
            self.assertEqual((self.beta / "same.txt").read_text(encoding="utf-8"), "BETA\n")

            executed = runtime.call_tool(
                "exec_command",
                {"project_id": "beta", "cmd": "python -c \"import os; print(os.getcwd())\""},
            )["structuredContent"]
            self.assertEqual(executed["exit_code"], 0)
            self.assertIn(str(self.beta.resolve()), executed["stdout"])

            alpha_status = runtime.call_tool(
                "git_status",
                {"project_id": "alpha"},
            )["structuredContent"]
            beta_status = runtime.call_tool(
                "git_status",
                {"project_id": "beta"},
            )["structuredContent"]
            self.assertIn("alpha-untracked.txt", [item["path"] for item in alpha_status["entries"]])
            self.assertNotIn("beta-untracked.txt", [item["path"] for item in alpha_status["entries"]])
            self.assertIn("beta-untracked.txt", [item["path"] for item in beta_status["entries"]])

            alpha_log = runtime.call_tool(
                "git_log",
                {"project_id": "alpha", "max_count": 1},
            )["structuredContent"]
            beta_log = runtime.call_tool(
                "git_log",
                {"project_id": "beta", "max_count": 1},
            )["structuredContent"]
            self.assertEqual(alpha_log["commits"][0]["subject"], "alpha-second")
            self.assertEqual(beta_log["commits"][0]["subject"], "beta-second")
        finally:
            runtime.close()

    def test_omitted_workdir_targets_project_root_and_explicit_workdir_stays_relative(self) -> None:
        runtime = self.runtime()
        subdir = self.beta / "nested"
        subdir.mkdir()
        try:
            root = runtime.call_tool(
                "exec_command",
                {"project_id": "beta", "cmd": "python -c \"import os; print(os.getcwd())\""},
            )["structuredContent"]
            self.assertEqual(Path(root["stdout"].strip()), self.beta.resolve())

            nested = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "workdir": "nested",
                    "cmd": "python -c \"import os; print(os.getcwd())\"",
                },
            )["structuredContent"]
            self.assertEqual(Path(nested["stdout"].strip()), subdir.resolve())

            escaped = runtime.call_tool(
                "exec_command",
                {
                    "project_id": "beta",
                    "workdir": "../alpha",
                    "cmd": "python -c \"print('NO')\"",
                },
            )
            self.assertTrue(escaped["isError"], escaped)
            self.assertEqual(
                escaped["structuredContent"]["error"]["code"],
                "PATH_OUTSIDE_WORKSPACE",
            )
        finally:
            runtime.close()

    def test_project_scoped_continuations_preserve_project_id(self) -> None:
        runtime = self.runtime()
        try:
            read = runtime.call_tool(
                "read_file",
                {"project_id": "alpha", "path": "blame.txt", "max_bytes": 8},
            )["structuredContent"]
            self.assertTrue(read["truncated"])
            self.assertEqual(read["next_action"]["tool"], "read_file")
            self.assertEqual(read["next_action"]["arguments"]["project_id"], "alpha")

            log = runtime.call_tool(
                "git_log",
                {"project_id": "alpha", "max_count": 1},
            )["structuredContent"]
            self.assertTrue(log["truncated"])
            self.assertEqual(log["next_action"]["arguments"]["project_id"], "alpha")
            self.assertNotIn("workdir", log["next_action"]["arguments"])

            blame = runtime.call_tool(
                "git_blame",
                {
                    "project_id": "alpha",
                    "path": "blame.txt",
                    "start_line": 1,
                    "end_line": 6,
                    "max_lines": 2,
                },
            )["structuredContent"]
            self.assertTrue(blame["truncated"])
            self.assertEqual(blame["next_action"]["arguments"]["project_id"], "alpha")
        finally:
            runtime.close()

    def test_missing_project_id_is_rejected_before_core_handler(self) -> None:
        runtime = self.runtime()
        try:
            with self.assertRaises(JsonRpcError):
                runtime.call_tool("read_file", {"path": "same.txt"})
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
