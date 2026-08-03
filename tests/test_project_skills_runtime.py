from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.server import Runtime, input_schemas


def write_skill(project: Path, name: str, description: str, body: str) -> Path:
    path = project / ".agents" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


class ProjectSkillsRuntimeTests(unittest.TestCase):
    def make_workspace(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        sdk = root / "sdk"
        nested = sdk / "repos" / "effect"
        nested.mkdir(parents=True)
        (sdk / "package.json").write_text("{}", encoding="utf-8")
        (nested / "package.json").write_text("{}", encoding="utf-8")
        return temporary, root, sdk, nested

    def test_skill_tools_are_registered_read_only_and_idempotent(self) -> None:
        temporary, root, _, _ = self.make_workspace()
        with temporary:
            runtime = Runtime(root)
            try:
                tools = {item["name"]: item for item in runtime.list_tools()["tools"]}

                self.assertIn("list_skills", tools)
                self.assertIn("read_skill", tools)
                for name in ("list_skills", "read_skill"):
                    annotations = tools[name]["annotations"]
                    self.assertIs(annotations["readOnlyHint"], True)
                    self.assertIs(annotations["idempotentHint"], True)
                    self.assertIs(annotations["destructiveHint"], False)
            finally:
                runtime.close()

    def test_skill_tool_schemas_use_explicit_workdir_and_no_source_path(self) -> None:
        schemas = input_schemas()

        self.assertEqual(schemas["list_skills"].get("required", []), [])
        self.assertEqual(schemas["list_skills"]["properties"]["workdir"]["default"], ".")
        self.assertEqual(schemas["read_skill"]["required"], ["skill"])
        self.assertEqual(schemas["read_skill"]["properties"]["workdir"]["default"], ".")
        self.assertNotIn("path", schemas["read_skill"]["properties"])
        self.assertNotIn("source", schemas["read_skill"]["properties"])

    def test_list_and_read_skill_return_project_scoped_payloads(self) -> None:
        temporary, root, sdk, nested = self.make_workspace()
        with temporary:
            (sdk / "AGENTS.md").write_text("SDK rules", encoding="utf-8")
            write_skill(sdk, "effect-ts", "Root Effect guidance", "ROOT SKILL BODY")
            write_skill(nested, "jsdocs", "Nested JSDoc guidance", "NESTED SKILL BODY")
            runtime = Runtime(root)
            try:
                listed = runtime.list_skills({"workdir": "sdk/repos/effect"})
                loaded = runtime.read_skill(
                    {"workdir": "sdk/repos/effect", "skill": "effect-ts"}
                )

                self.assertEqual(listed["main_project"], "sdk")
                self.assertEqual(listed["subprojects"], ["sdk/repos/effect"])
                self.assertEqual(listed["instruction_files"], ["sdk/AGENTS.md"])
                self.assertEqual(
                    [item["name"] for item in listed["skills"]],
                    ["effect-ts", "jsdocs"],
                )
                self.assertEqual(loaded["skill"]["owner_project"], "sdk")
                self.assertIn("ROOT SKILL BODY", loaded["content"])
                self.assertNotIn("NESTED SKILL BODY", loaded["content"])
            finally:
                runtime.close()

    def test_missing_skill_returns_structured_effective_names_only(self) -> None:
        temporary, root, sdk, nested = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "root")
            write_skill(nested, "jsdocs", "Nested JSDoc guidance", "nested")
            runtime = Runtime(root)
            try:
                result = runtime.call_tool(
                    "read_skill",
                    {"workdir": "sdk/repos/effect", "skill": "missing"},
                )

                payload = result["structuredContent"]
                self.assertIs(result["isError"], True)
                self.assertEqual(payload["error"]["code"], "SKILL_NOT_FOUND")
                self.assertEqual(payload["error"]["details"]["available"], ["effect-ts", "jsdocs"])
            finally:
                runtime.close()

    def test_skill_tools_render_agent_readable_metadata_and_body(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "ROOT SKILL BODY")
            runtime = Runtime(root)
            try:
                listed = runtime.call_tool("list_skills", {"workdir": "sdk"})
                loaded = runtime.call_tool(
                    "read_skill",
                    {"workdir": "sdk", "skill": "effect-ts"},
                )

                listed_text = "\n".join(
                    str(item.get("text", ""))
                    for item in listed["content"]
                    if item.get("type") == "text"
                )
                loaded_text = "\n".join(
                    str(item.get("text", ""))
                    for item in loaded["content"]
                    if item.get("type") == "text"
                )
                self.assertIn("effect-ts", listed_text)
                self.assertIn("Root Effect guidance", listed_text)
                self.assertIn("ROOT SKILL BODY", loaded_text)
            finally:
                runtime.close()

    def test_read_at_parent_workspace_returns_project_not_found(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "root")
            runtime = Runtime(root)
            try:
                result = runtime.call_tool("read_skill", {"workdir": ".", "skill": "effect-ts"})

                payload = result["structuredContent"]
                self.assertEqual(payload["error"]["code"], "PROJECT_NOT_FOUND")
            finally:
                runtime.close()

    def test_initialize_is_compact_and_server_info_exposes_main_projects(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            (sdk / "AGENTS.md").write_text("CHILD INSTRUCTION BODY", encoding="utf-8")
            write_skill(sdk, "effect-ts", "Root Effect guidance", "UNIQUE SKILL BODY")
            runtime = Runtime(root)
            try:
                initialized = runtime.initialize({"name": "test-client"})
                info = runtime.server_info({})

                instructions = initialized["instructions"]
                self.assertIn("list_skills", instructions)
                self.assertIn("read_skill", instructions)
                self.assertNotIn("CHILD INSTRUCTION BODY", instructions)
                self.assertNotIn("UNIQUE SKILL BODY", instructions)
                self.assertEqual(info["project_context"]["nested_instruction_files"], [])
                self.assertEqual(info["project_catalog"]["main_project_count"], 1)
                self.assertEqual(info["project_catalog"]["main_projects"][0]["id"], "sdk")
                self.assertNotIn("skills", info["project_catalog"]["main_projects"][0])
            finally:
                runtime.close()

    def test_catalog_instances_can_be_shared_across_runtime_sessions(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "root")
            owner = Runtime(root)
            sibling = Runtime(
                root,
                project_context=owner.project_context,
                project_catalog=owner.project_catalog,
                skill_catalog=owner.skill_catalog,
                command_manager=owner.command_manager,
            )
            try:
                self.assertIs(sibling.project_catalog, owner.project_catalog)
                self.assertIs(sibling.skill_catalog, owner.skill_catalog)
                self.assertEqual(
                    sibling.list_skills({"workdir": "sdk"})["skills"][0]["name"],
                    "effect-ts",
                )
            finally:
                sibling.close()
                owner.close()


if __name__ == "__main__":
    unittest.main()
