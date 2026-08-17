from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.server import TOOL_REGISTRY, Runtime, input_schemas
from tests.compliance.mcp_client import StdioMCPClient
from tests.compliance.test_support import structured_payload


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def init_repo(repo: Path, *, subject: str) -> None:
    repo.mkdir(parents=True)
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Git Workdir Test"),
        ("add", "-A"),
        ("commit", "-q", "-m", subject),
    ):
        completed = run_git(repo, *args)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)


@unittest.skipUnless(shutil.which("git"), "git is not available")
class GitWorkdirResolutionTests(unittest.TestCase):
    def make_workspace(self) -> tuple[TemporaryDirectory[str], Path, Path, Path]:
        temporary = TemporaryDirectory()
        workspace = Path(temporary.name)
        alpha = workspace / "alpha"
        beta = workspace / "beta"
        init_repo(alpha, subject="alpha initial")
        init_repo(beta, subject="beta initial")
        return temporary, workspace, alpha, beta

    def test_all_git_tools_accept_explicit_workdir(self) -> None:
        schemas = input_schemas()
        for tool_name in ("git_status", "git_diff", "git_log", "git_show", "git_blame"):
            with self.subTest(tool=tool_name):
                self.assertIn("workdir", schemas[tool_name]["properties"])
                spec = TOOL_REGISTRY[tool_name]
                self.assertTrue(spec.read_only)
                self.assertTrue(spec.idempotent)

    def test_explicit_workdir_selects_nested_repository_for_every_git_tool(self) -> None:
        temporary, workspace, alpha, _ = self.make_workspace()
        with temporary:
            (alpha / "tracked.txt").write_text("one changed\ntwo\n", encoding="utf-8")
            runtime = Runtime(workspace)
            try:
                status = runtime.git_status({"workdir": "alpha"})
                diff = runtime.git_diff({"workdir": "alpha"})
                log = runtime.git_log({"workdir": "alpha", "max_count": 5})
                show = runtime.git_show(
                    {"workdir": "alpha", "include_diff": False, "max_bytes": 4096}
                )
                blame = runtime.git_blame(
                    {"workdir": "alpha", "path": "tracked.txt", "max_lines": 5}
                )

                self.assertTrue(status["is_repo"])
                self.assertEqual(status["head"], run_git(alpha, "rev-parse", "HEAD").stdout.strip())
                self.assertIn("tracked.txt", [item["path"] for item in status["entries"]])
                self.assertIn("one changed", diff["diff"])
                self.assertEqual(log["commits"][0]["subject"], "alpha initial")
                self.assertIn("alpha initial", show["content"])
                self.assertEqual(blame["lines"][0]["content"], "one changed")
            finally:
                runtime.close()

    def test_path_filters_are_relative_to_selected_workdir(self) -> None:
        temporary, workspace, alpha, _ = self.make_workspace()
        with temporary:
            (alpha / "tracked.txt").write_text("one changed\ntwo\n", encoding="utf-8")
            (alpha / "other.txt").write_text("other changed\n", encoding="utf-8")
            runtime = Runtime(workspace)
            try:
                diff = runtime.git_diff({"workdir": "alpha", "path": "tracked.txt"})
                log = runtime.git_log(
                    {"workdir": "alpha", "path": "tracked.txt", "max_count": 5}
                )
                show = runtime.git_show(
                    {
                        "workdir": "alpha",
                        "path": "tracked.txt",
                        "include_diff": True,
                        "max_bytes": 4096,
                    }
                )

                self.assertIn("tracked.txt", diff["diff"])
                self.assertNotIn("other.txt", diff["diff"])
                self.assertEqual(log["path"], "tracked.txt")
                self.assertEqual(log["commits"][0]["subject"], "alpha initial")
                self.assertIn("tracked.txt", show["content"])
                self.assertNotIn("other.txt", show["content"])
            finally:
                runtime.close()

    def test_path_filter_cannot_escape_selected_repository(self) -> None:
        temporary, workspace, _, _ = self.make_workspace()
        with temporary:
            runtime = Runtime(workspace)
            try:
                with self.assertRaises(ToolFailure) as captured:
                    runtime.git_diff({"workdir": "alpha", "path": "../beta/tracked.txt"})
                self.assertEqual(captured.exception.code, "PATH_OUTSIDE_WORKSPACE")

                with self.assertRaises(ToolFailure) as captured:
                    runtime.git_blame(
                        {"workdir": "alpha", "path": "../beta/tracked.txt"}
                    )
                self.assertEqual(captured.exception.code, "PATH_OUTSIDE_WORKSPACE")
            finally:
                runtime.close()

    def test_git_status_preserves_path_as_legacy_workdir_alias(self) -> None:
        temporary, workspace, _, _ = self.make_workspace()
        with temporary:
            runtime = Runtime(workspace)
            try:
                legacy = runtime.git_status({"path": "alpha"})
                explicit = runtime.git_status({"workdir": "alpha"})
                explicit_with_default_alias = runtime.git_status(
                    {"workdir": "alpha", "path": "."}
                )
                legacy_with_default_workdir = runtime.git_status(
                    {"workdir": ".", "path": "alpha"}
                )
                self.assertEqual(legacy["head"], explicit["head"])
                self.assertEqual(explicit_with_default_alias["head"], explicit["head"])
                self.assertEqual(legacy_with_default_workdir["head"], explicit["head"])

                with self.assertRaises(ToolFailure) as captured:
                    runtime.git_status({"path": "alpha", "workdir": "beta"})
                self.assertEqual(captured.exception.code, "INVALID_ARGUMENT")
            finally:
                runtime.close()

    def test_pagination_continuations_preserve_workdir(self) -> None:
        temporary, workspace, alpha, _ = self.make_workspace()
        with temporary:
            (alpha / "tracked.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            completed = run_git(alpha, "commit", "-q", "-am", "alpha second")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            runtime = Runtime(workspace)
            try:
                log = runtime.git_log({"workdir": "alpha", "max_count": 1})
                blame = runtime.git_blame(
                    {
                        "workdir": "alpha",
                        "path": "tracked.txt",
                        "start_line": 1,
                        "end_line": 3,
                        "max_lines": 1,
                    }
                )

                self.assertEqual(
                    log["next_action"]["arguments"]["workdir"],
                    "alpha",
                )
                self.assertEqual(
                    blame["next_action"]["arguments"]["workdir"],
                    "alpha",
                )
            finally:
                runtime.close()

    def test_stdio_protocol_routes_git_calls_to_explicit_repository(self) -> None:
        temporary, workspace, alpha, beta = self.make_workspace()
        with temporary:
            (alpha / "tracked.txt").write_text("alpha changed\ntwo\n", encoding="utf-8")
            with StdioMCPClient(workspace, default_project_id="default") as client:
                alpha_status = structured_payload(
                    client.call_tool("git_status", {"workdir": "alpha"})
                )
                alpha_diff = structured_payload(
                    client.call_tool("git_diff", {"workdir": "alpha"})
                )
                beta_log = structured_payload(
                    client.call_tool("git_log", {"workdir": "beta", "max_count": 1})
                )
                beta_show = structured_payload(
                    client.call_tool(
                        "git_show",
                        {"workdir": "beta", "include_diff": False, "max_bytes": 4096},
                    )
                )
                beta_blame = structured_payload(
                    client.call_tool(
                        "git_blame",
                        {"workdir": "beta", "path": "tracked.txt", "max_lines": 2},
                    )
                )

            self.assertEqual(
                alpha_status["head"],
                run_git(alpha, "rev-parse", "HEAD").stdout.strip(),
            )
            self.assertIn("alpha changed", alpha_diff["diff"])
            self.assertEqual(beta_log["commits"][0]["subject"], "beta initial")
            self.assertIn("beta initial", beta_show["content"])
            self.assertEqual(beta_blame["lines"][0]["content"], "one")
            self.assertEqual(
                beta_log["commits"][0]["hash"],
                run_git(beta, "rev-parse", "HEAD").stdout.strip(),
            )


if __name__ == "__main__":
    unittest.main()
