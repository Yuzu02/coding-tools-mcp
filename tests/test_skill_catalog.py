from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions.projects.project_catalog import build_project_catalog
from coding_tools_mcp.extensions.projects.skill_catalog import (
    MAX_SKILL_BODY_BYTES,
    ProjectNotFoundError,
    SkillCatalog,
    SkillInvalidError,
    SkillNotFoundError,
)


def write_skill(
    scope: Path,
    container: str,
    directory_name: str,
    *,
    name: str | None = None,
    description: str = "Test skill",
    body: str = "# Instructions\n",
) -> Path:
    skill_name = name or directory_name
    path = scope / container / "skills" / directory_name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def make_workspace() -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    sdk = root / "sdk"
    nested = sdk / "repos" / "effect"
    other = root / "other"
    nested.mkdir(parents=True)
    other.mkdir()
    (sdk / "package.json").write_text("{}", encoding="utf-8")
    (nested / "package.json").write_text("{}", encoding="utf-8")
    (other / "pyproject.toml").write_text("", encoding="utf-8")
    return temporary, root, sdk, nested, other


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


class SkillCatalogTests(unittest.TestCase):
    def test_root_skill_is_visible_from_main_project(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "effect-ts", description="Effect guidance")

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual(context.main_project, "sdk")
            self.assertEqual(context.subprojects, ())
            self.assertEqual([skill.name for skill in context.skills], ["effect-ts"])
            self.assertEqual(context.skills[0].owner_project, "sdk")
            self.assertEqual(context.skills[0].source_format, "agents")

    def test_nested_scope_adds_skill_but_cannot_override_root_name(self) -> None:
        temporary, root, sdk, nested, _ = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "effect-ts", description="Root Effect guidance")
            write_skill(nested, ".agents", "effect-ts", description="Nested override")
            write_skill(nested, ".agents", "jsdocs", description="JSDoc guidance")

            context = SkillCatalog(build_project_catalog(root)).list_for(nested)

            self.assertEqual(context.subprojects, ("sdk/repos/effect",))
            self.assertEqual([skill.name for skill in context.skills], ["effect-ts", "jsdocs"])
            by_name = {skill.name: skill for skill in context.skills}
            self.assertEqual(by_name["effect-ts"].description, "Root Effect guidance")
            self.assertEqual(by_name["effect-ts"].owner_project, "sdk")
            self.assertEqual(by_name["jsdocs"].owner_project, "sdk/repos/effect")

    def test_sibling_project_does_not_see_sdk_skills(self) -> None:
        temporary, root, sdk, _, other = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "effect-ts")

            context = SkillCatalog(build_project_catalog(root)).list_for(other)

            self.assertEqual(context.main_project, "other")
            self.assertEqual(context.skills, ())

    def test_claude_alias_to_agents_skill_is_deduplicated(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            source = write_skill(sdk, ".agents", "effect-ts")
            alias = sdk / ".claude" / "skills" / "effect-ts"
            if not create_directory_alias(source.parent, alias):
                self.skipTest("Directory aliases are unavailable on this platform")

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual([skill.name for skill in context.skills], ["effect-ts"])
            self.assertEqual(context.skills[0].source, "sdk/.agents/skills/effect-ts/SKILL.md")
            self.assertEqual(context.skills[0].source_format, "agents")

    def test_outward_alias_is_rejected_with_warning(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary, tempfile.TemporaryDirectory() as external_tmp:
            external = Path(external_tmp)
            write_skill(external, ".agents", "external")
            alias = sdk / ".agents" / "skills" / "external"
            if not create_directory_alias(external / ".agents" / "skills" / "external", alias):
                self.skipTest("Directory aliases are unavailable on this platform")

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual(context.skills, ())
            self.assertTrue(any("outside workspace" in warning for warning in context.warnings))

    def test_invalid_frontmatter_is_excluded_without_hiding_valid_skill(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "valid")
            invalid = sdk / ".agents" / "skills" / "invalid" / "SKILL.md"
            invalid.parent.mkdir(parents=True)
            invalid.write_text("---\nname: invalid\n---\nbody\n", encoding="utf-8")

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual([skill.name for skill in context.skills], ["valid"])
            self.assertTrue(any("description" in warning for warning in context.warnings))

    def test_parser_ignores_unknown_nested_frontmatter_and_long_description(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            long_description = "x" * 1500
            skill = sdk / ".agents" / "skills" / "xlsx" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\n"
                "name: xlsx\n"
                'description: "' + long_description + '"\n'
                "paths:\n"
                "  - artifacts/**/*.xlsx\n"
                "license: MIT\n"
                "---\n"
                "body\n",
                encoding="utf-8",
            )

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual([record.name for record in context.skills], ["xlsx"])
            self.assertEqual(context.skills[0].description, long_description)

    def test_invalid_required_scalar_forms_are_rejected(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            bad_cases = {
                "sequence": "---\nname: invalid\ndescription: [x]\n---\nbody\n",
                "tagged": "---\nname: invalid\ndescription: !tag value\n---\nbody\n",
                "unterminated": '---\nname: invalid\ndescription: "unterminated\n---\nbody\n',
                "list_name": "---\nname:\n  - invalid\ndescription: desc\n---\nbody\n",
            }
            for directory_name, body in bad_cases.items():
                skill = sdk / ".agents" / "skills" / directory_name / "SKILL.md"
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text(body, encoding="utf-8")

            context = SkillCatalog(build_project_catalog(root)).list_for(sdk)

            self.assertEqual(context.skills, ())
            self.assertGreaterEqual(len(context.warnings), 4)

    def test_instruction_files_follow_the_selected_scope_chain(self) -> None:
        temporary, root, sdk, nested, _ = make_workspace()
        with temporary:
            (sdk / "AGENTS.md").write_text("root rules", encoding="utf-8")
            (nested / "CLAUDE.md").write_text("nested rules", encoding="utf-8")

            root_context = SkillCatalog(build_project_catalog(root)).list_for(sdk)
            nested_context = SkillCatalog(build_project_catalog(root)).list_for(nested)

            self.assertEqual(root_context.instruction_files, ("sdk/AGENTS.md",))
            self.assertEqual(
                nested_context.instruction_files,
                ("sdk/AGENTS.md", "sdk/repos/effect/CLAUDE.md"),
            )

    def test_read_returns_only_effective_skill_and_truncates_content(self) -> None:
        temporary, root, sdk, nested, _ = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "effect-ts", body="x" * (MAX_SKILL_BODY_BYTES * 2))
            write_skill(nested, ".agents", "effect-ts", description="Nested override")
            catalog = SkillCatalog(build_project_catalog(root))

            loaded = catalog.read(nested, "effect-ts")

            self.assertEqual(loaded.skill.owner_project, "sdk")
            self.assertTrue(loaded.truncated)
            self.assertEqual(loaded.returned_bytes, MAX_SKILL_BODY_BYTES)
            self.assertGreater(loaded.total_bytes, loaded.returned_bytes)

    def test_unknown_skill_lists_only_effective_names(self) -> None:
        temporary, root, sdk, nested, _ = make_workspace()
        with temporary:
            write_skill(sdk, ".agents", "effect-ts")
            write_skill(nested, ".agents", "jsdocs")
            catalog = SkillCatalog(build_project_catalog(root))

            with self.assertRaises(SkillNotFoundError) as caught:
                catalog.read(nested, "missing")

            self.assertEqual(caught.exception.available, ("effect-ts", "jsdocs"))

    def test_read_outside_project_raises_project_not_found(self) -> None:
        temporary, root, _, _, _ = make_workspace()
        with temporary:
            catalog = SkillCatalog(build_project_catalog(root))

            with self.assertRaises(ProjectNotFoundError):
                catalog.read(root, "missing")

    def test_non_utf8_skill_body_becomes_invalid_on_read(self) -> None:
        temporary, root, sdk, _, _ = make_workspace()
        with temporary:
            path = write_skill(sdk, ".agents", "binary")
            catalog = SkillCatalog(build_project_catalog(root))
            self.assertEqual([skill.name for skill in catalog.list_for(sdk).skills], ["binary"])
            path.write_bytes(
                b"---\nname: binary\ndescription: Binary skill\n---\n\n" + b"\xff\xfe"
            )

            with self.assertRaises(SkillInvalidError):
                catalog.read(sdk, "binary")


if __name__ == "__main__":
    unittest.main()
