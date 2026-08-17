from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.server import Runtime


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
        temporary, root, _, _ = self.make_workspace()
        with temporary:
            runtime = Runtime(root)
            try:
                tools = {item["name"]: item for item in runtime.list_tools()["tools"]}
                list_schema = tools["list_skills"]["inputSchema"]
                read_schema = tools["read_skill"]["inputSchema"]

                self.assertEqual(list_schema.get("required", []), ["project_id"])
                self.assertEqual(list_schema["properties"]["project_id"]["minLength"], 1)
                self.assertEqual(list_schema["properties"]["workdir"]["default"], ".")
                self.assertEqual(read_schema["required"], ["project_id", "skill"])
                self.assertEqual(read_schema["properties"]["project_id"]["minLength"], 1)
                self.assertEqual(read_schema["properties"]["workdir"]["default"], ".")
                self.assertNotIn("path", read_schema["properties"])
                self.assertNotIn("source", read_schema["properties"])
            finally:
                runtime.close()

    def test_list_and_read_skill_return_project_scoped_payloads(self) -> None:
        temporary, root, sdk, nested = self.make_workspace()
        with temporary:
            (sdk / "AGENTS.md").write_text("SDK rules", encoding="utf-8")
            write_skill(sdk, "effect-ts", "Root Effect guidance", "ROOT SKILL BODY")
            write_skill(nested, "jsdocs", "Nested JSDoc guidance", "NESTED SKILL BODY")
            runtime = Runtime(root)
            try:
                listed_result = runtime.call_tool(
                    "list_skills",
                    {"project_id": "default", "workdir": "sdk/repos/effect"},
                )
                loaded_result = runtime.call_tool(
                    "read_skill",
                    {
                        "project_id": "default",
                        "workdir": "sdk/repos/effect",
                        "skill": "effect-ts",
                    },
                )
                listed = listed_result["structuredContent"]
                loaded = loaded_result["structuredContent"]

                self.assertFalse(listed_result["isError"])
                self.assertFalse(loaded_result["isError"])
                self.assertEqual(listed["project_id"], "default")
                self.assertEqual(loaded["project_id"], "default")

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
                    {
                        "project_id": "default",
                        "workdir": "sdk/repos/effect",
                        "skill": "missing",
                    },
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
                listed = runtime.call_tool(
                    "list_skills",
                    {"project_id": "default", "workdir": "sdk"},
                )
                loaded = runtime.call_tool(
                    "read_skill",
                    {"project_id": "default", "workdir": "sdk", "skill": "effect-ts"},
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
                result = runtime.call_tool(
                    "read_skill",
                    {"project_id": "default", "workdir": ".", "skill": "effect-ts"},
                )

                payload = result["structuredContent"]
                self.assertEqual(payload["error"]["code"], "PROJECT_NOT_FOUND")
            finally:
                runtime.close()

    def test_skill_workdir_rejects_absolute_paths_and_files(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "root")
            runtime = Runtime(root)
            try:
                absolute = runtime.call_tool(
                    "list_skills",
                    {"project_id": "default", "workdir": str(sdk)},
                )
                file_path = runtime.call_tool(
                    "read_skill",
                    {
                        "project_id": "default",
                        "workdir": "sdk/package.json",
                        "skill": "effect-ts",
                    },
                )

                absolute_payload = absolute["structuredContent"]
                file_payload = file_path["structuredContent"]
                self.assertEqual(absolute_payload["error"]["code"], "ABSOLUTE_PATH_DENIED")
                self.assertEqual(file_payload["error"]["code"], "NOT_A_DIRECTORY")
            finally:
                runtime.close()

    def test_skill_workdir_rejects_traversal_and_nul(self) -> None:
        temporary, root, sdk, _ = self.make_workspace()
        with temporary:
            write_skill(sdk, "effect-ts", "Root Effect guidance", "root")
            runtime = Runtime(root)
            try:
                traversal = runtime.call_tool(
                    "list_skills",
                    {"project_id": "default", "workdir": "../sdk"},
                )
                nul = runtime.call_tool(
                    "list_skills",
                    {"project_id": "default", "workdir": "sdk\x00"},
                )

                traversal_payload = traversal["structuredContent"]
                nul_payload = nul["structuredContent"]
                self.assertEqual(traversal_payload["error"]["code"], "PATH_OUTSIDE_WORKSPACE")
                self.assertEqual(nul_payload["error"]["code"], "INVALID_ARGUMENT")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
