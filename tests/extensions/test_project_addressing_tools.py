from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.server import Runtime


def write_skill(project: Path, body: str) -> None:
    path = project / ".agents" / "skills" / "shared" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: shared\ndescription: Shared skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


class ProjectAddressingToolTests(unittest.TestCase):
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
        (self.alpha / "AGENTS.md").write_text("ALPHA RULES\n", encoding="utf-8")
        (self.beta / "AGENTS.md").write_text("BETA RULES\n", encoding="utf-8")
        write_skill(self.alpha, "ALPHA BODY")
        write_skill(self.beta, "BETA BODY")
        nested = self.alpha / "packages" / "nested"
        nested.mkdir(parents=True)
        (nested / "package.json").write_text("{}", encoding="utf-8")
        self.alpha_target = nested / "src" / "module.py"
        self.alpha_target.parent.mkdir()
        self.alpha_target.write_text("print('alpha')\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runtime(self, *, include_unavailable: bool = False) -> Runtime:
        registry: dict[str, dict[str, object]] = {
            "alpha": {"root": str(self.alpha)},
            "beta": {"root": str(self.beta)},
        }
        if include_unavailable:
            registry["missing"] = {
                "root": str(self.root / "missing"),
                "allow_unavailable": True,
            }
        config = RuntimeConfig.defaults(
            enabled=("projects",),
            settings={"projects": {"registry": registry}},
        )
        return Runtime(self.bootstrap, extension_config=config)

    def test_list_projects_returns_registered_ids_without_creating_current_project(self) -> None:
        runtime = self.runtime()
        try:
            payload = runtime.call_tool("list_projects", {})["structuredContent"]
            self.assertEqual([item["id"] for item in payload["projects"]], ["alpha", "beta"])
            self.assertEqual(payload["project_count"], 2)
            self.assertTrue(all(item["available"] for item in payload["projects"]))
        finally:
            runtime.close()

    def test_resolve_project_uses_stable_id_and_structural_scope_chain(self) -> None:
        runtime = self.runtime()
        try:
            payload = runtime.call_tool(
                "resolve_project",
                {"path": str(self.alpha_target)},
            )["structuredContent"]

            self.assertEqual(payload["project_id"], "alpha")
            self.assertEqual(payload["relative_path"], "packages/nested/src/module.py")
            self.assertEqual(
                [scope["scope_id"] for scope in payload["scope_chain"]],
                [".", "packages/nested"],
            )
            self.assertEqual(payload["scope_chain"][-1]["scope_root"], "packages/nested")
            self.assertEqual(payload["scope_chain"][-1]["kind"], "subproject")
        finally:
            runtime.close()

    def test_resolve_project_returns_typed_path_errors(self) -> None:
        runtime = self.runtime(include_unavailable=True)
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            relative = runtime.call_tool("resolve_project", {"path": "relative/path.txt"})
            self.assertEqual(relative["structuredContent"]["error"]["code"], "INVALID_PROJECT_PATH")

            missing = runtime.call_tool("resolve_project", {"path": str(outside)})
            self.assertEqual(missing["structuredContent"]["error"]["code"], "PROJECT_NOT_FOUND")

            unavailable_path = self.root / "missing" / "file.txt"
            unavailable_path.parent.mkdir()
            unavailable_path.write_text("later\n", encoding="utf-8")
            unavailable = runtime.call_tool("resolve_project", {"path": str(unavailable_path)})
            self.assertEqual(unavailable["structuredContent"]["error"]["code"], "PROJECT_UNAVAILABLE")
        finally:
            runtime.close()

    def test_resolve_project_rejects_symlink_escape(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "secret.txt"
        target.write_text("secret\n", encoding="utf-8")
        link = self.alpha / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        runtime = self.runtime()
        try:
            result = runtime.call_tool("resolve_project", {"path": str(link / "secret.txt")})
            self.assertEqual(result["structuredContent"]["error"]["code"], "INVALID_PROJECT_PATH")
        finally:
            runtime.close()

    def test_skill_tools_require_project_id_and_route_identical_skill_names(self) -> None:
        runtime = self.runtime()
        try:
            tools = {item["name"]: item for item in runtime.list_tools()["tools"]}
            for name in ("list_skills", "read_skill"):
                self.assertIn("project_id", tools[name]["inputSchema"]["properties"])
                self.assertIn("project_id", tools[name]["inputSchema"]["required"])

            alpha = runtime.call_tool(
                "read_skill",
                {"project_id": "alpha", "workdir": ".", "skill": "shared"},
            )["structuredContent"]
            beta = runtime.call_tool(
                "read_skill",
                {"project_id": "beta", "workdir": ".", "skill": "shared"},
            )["structuredContent"]
            self.assertEqual(alpha["project_id"], "alpha")
            self.assertEqual(beta["project_id"], "beta")
            self.assertIn("ALPHA BODY", alpha["content"])
            self.assertIn("BETA BODY", beta["content"])

            with self.assertRaises(JsonRpcError):
                runtime.call_tool("read_skill", {"workdir": ".", "skill": "shared"})
        finally:
            runtime.close()

    def test_default_and_disabled_composed_catalog_counts_are_explicit(self) -> None:
        default = Runtime(self.bootstrap)
        disabled = Runtime(
            self.bootstrap,
            extension_config=RuntimeConfig.defaults(enabled=()),
        )
        try:
            self.assertEqual(len(default.exposed_tool_names()), 26)
            self.assertIn("list_projects", default.exposed_tool_names())
            self.assertIn("resolve_project", default.exposed_tool_names())
            self.assertIn("project_context", default.exposed_tool_names())
            self.assertIn("doctor", default.exposed_tool_names())
            listed = default.call_tool("list_projects", {})["structuredContent"]
            self.assertEqual(listed["projects"][0]["id"], "default")
            self.assertEqual(len(disabled.exposed_tool_names()), 20)
        finally:
            default.close()
            disabled.close()


if __name__ == "__main__":
    unittest.main()
