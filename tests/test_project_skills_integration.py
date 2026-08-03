from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.compliance.mcp_client import StdioMCPClient
from tests.compliance.test_support import structured_payload


def write_skill(scope: Path, name: str, description: str, body: str) -> Path:
    path = scope / ".agents" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def create_directory_alias(target: Path, alias: Path) -> bool:
    alias.parent.mkdir(parents=True, exist_ok=True)
    try:
        alias.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        if os.name != "nt":
            return False
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


class ProjectSkillsIntegrationTests(unittest.TestCase):
    def test_stdio_client_resolves_effective_skills_by_explicit_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as external_tmp:
            workspace = Path(tmp)
            sdk = workspace / "sdk"
            nested = sdk / "repos" / "effect"
            other = workspace / "other"
            nested.mkdir(parents=True)
            other.mkdir()
            (sdk / "package.json").write_text("{}", encoding="utf-8")
            (nested / "package.json").write_text("{}", encoding="utf-8")
            (other / "pyproject.toml").write_text("", encoding="utf-8")
            (sdk / "AGENTS.md").write_text("SDK instructions", encoding="utf-8")
            root_skill = write_skill(sdk, "effect-ts", "Root Effect guidance", "ROOT BODY")
            write_skill(nested, "effect-ts", "Nested override", "OVERRIDE BODY")
            write_skill(nested, "jsdocs", "Nested JSDoc guidance", "JSDOC BODY")

            alias_created = create_directory_alias(
                root_skill.parent,
                sdk / ".claude" / "skills" / "effect-ts",
            )
            external = Path(external_tmp)
            external_skill = write_skill(external, "external", "External guidance", "EXTERNAL BODY")
            outward_created = create_directory_alias(
                external_skill.parent,
                sdk / ".agents" / "skills" / "external",
            )

            with StdioMCPClient(workspace) as client:
                root_context = structured_payload(
                    client.call_tool("list_skills", {"workdir": "sdk"})
                )
                nested_context = structured_payload(
                    client.call_tool("list_skills", {"workdir": "sdk/repos/effect"})
                )
                other_context = structured_payload(
                    client.call_tool("list_skills", {"workdir": "other"})
                )
                loaded = structured_payload(
                    client.call_tool(
                        "read_skill",
                        {"workdir": "sdk/repos/effect", "skill": "effect-ts"},
                    )
                )

            self.assertEqual([item["name"] for item in root_context["skills"]], ["effect-ts"])
            self.assertEqual(
                [item["name"] for item in nested_context["skills"]],
                ["effect-ts", "jsdocs"],
            )
            self.assertEqual(nested_context["skills"][0]["owner_project"], "sdk")
            self.assertEqual(other_context["skills"], [])
            self.assertIn("ROOT BODY", loaded["content"])
            self.assertNotIn("OVERRIDE BODY", loaded["content"])
            if alias_created:
                self.assertEqual(
                    [item["name"] for item in root_context["skills"]].count("effect-ts"),
                    1,
                )
            if outward_created:
                self.assertNotIn("external", [item["name"] for item in root_context["skills"]])
                self.assertTrue(
                    any("outside workspace" in warning for warning in root_context["warnings"])
                )


if __name__ == "__main__":
    unittest.main()
