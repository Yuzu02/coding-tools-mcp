from __future__ import annotations

import contextlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

from coding_tools_mcp.server import KILL_COMMAND_STATUSES, TOOL_REGISTRY, Runtime, input_schemas, tool_annotations


ROOT = Path(__file__).resolve().parents[2]


EXPECTED_STATELESS_TOOL_NAMES = frozenset(
    {
        "server_info",
        "check_exec_environment",
        "list_projects",
        "resolve_project",
        "read_file",
        "list_dir",
        "list_files",
        "search_text",
        "list_skills",
        "read_skill",
        "apply_patch",
        "exec_command",
        "list_commands",
        "get_command",
        "write_stdin",
        "kill_command",
        "read_output",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_blame",
        "request_permissions",
        "view_image",
    }
)


@contextlib.contextmanager
def default_runtime() -> Iterator[Runtime]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text("{}", encoding="utf-8")
        runtime = Runtime(root)
        try:
            yield runtime
        finally:
            runtime.close()


class SchemaDriftTests(unittest.TestCase):
    CONTRACT_PATH = ROOT / "docs/runtime-contract-v0.4.md"

    def test_input_schemas_cover_exactly_the_registered_tools(self) -> None:
        self.assertEqual(set(input_schemas()), set(TOOL_REGISTRY))
        with default_runtime() as runtime:
            self.assertEqual(set(runtime.exposed_tool_names()), EXPECTED_STATELESS_TOOL_NAMES)

    def test_contract_kill_command_status_enum_matches_live_constant(self) -> None:
        contract = self.CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn(json.dumps(list(KILL_COMMAND_STATUSES)), contract)

    def test_contract_contains_every_live_tool_and_input_property(self) -> None:
        contract = self.CONTRACT_PATH.read_text(encoding="utf-8")
        sections = markdown_tool_sections(contract)
        with default_runtime() as runtime:
            tools = {item["name"]: item for item in runtime.list_tools()["tools"]}
            self.assertEqual(set(tools), EXPECTED_STATELESS_TOOL_NAMES)
            for tool_name in sorted(EXPECTED_STATELESS_TOOL_NAMES):
                with self.subTest(tool=tool_name):
                    section = sections.get(tool_name, "")
                    self.assertTrue(section, f"runtime contract lacks section for {tool_name}")
                    schema = tools[tool_name]["inputSchema"]
                    for property_name in schema.get("properties", {}):
                        self.assertIn(f'"{property_name}"', section, f"{tool_name} contract missing {property_name}")

    def test_contract_contains_live_annotation_values(self) -> None:
        contract = self.CONTRACT_PATH.read_text(encoding="utf-8")
        sections = markdown_tool_sections(contract)
        with default_runtime() as runtime:
            for tool_name in sorted(EXPECTED_STATELESS_TOOL_NAMES):
                annotations = tool_annotations(runtime._tools[tool_name])
                section = sections.get(tool_name, "")
                for key, value in annotations.items():
                    with self.subTest(tool=tool_name, annotation=key):
                        self.assertIn(str(key), section)
                        self.assertIn(str(value).lower(), section.lower())

    def test_default_annotations_are_the_truthful_ones(self) -> None:
        # The documented catalog above is the truthful one, so the fake-readonly
        # override must never become what an unqualified call returns.
        with default_runtime() as runtime:
            for tool_name in sorted(EXPECTED_STATELESS_TOOL_NAMES):
                tool = runtime._tools[tool_name]
                with self.subTest(tool=tool_name):
                    self.assertEqual(
                        tool_annotations(tool),
                        tool_annotations(tool, fake_readonly=False),
                    )

    def test_fake_readonly_override_only_rewrites_exposure_hints(self) -> None:
        with default_runtime() as runtime:
            for tool_name in sorted(EXPECTED_STATELESS_TOOL_NAMES):
                tool = runtime._tools[tool_name]
                truthful = tool_annotations(tool)
                faked = tool_annotations(tool, fake_readonly=True)
                with self.subTest(tool=tool_name):
                    self.assertIs(faked["readOnlyHint"], True)
                    self.assertIs(faked["destructiveHint"], False)
                    self.assertIs(faked["openWorldHint"], False)
                    # Identity and idempotency are not exposure claims, so they stay real.
                    self.assertEqual(faked["title"], truthful["title"])
                    self.assertEqual(faked["idempotentHint"], truthful["idempotentHint"])

    def test_tools_docs_list_matches_live_tool_names(self) -> None:
        text = (ROOT / "docs/tools-and-schemas.md").read_text(encoding="utf-8")
        inventory = text.split("## Fixed inventory", 1)[1].split("## Result envelope", 1)[0]
        documented = set(re.findall(r"^- `([a-z0-9_]+)`:", inventory, flags=re.MULTILINE))
        self.assertEqual(documented, EXPECTED_STATELESS_TOOL_NAMES)
        self.assertIn(f"exactly {len(EXPECTED_STATELESS_TOOL_NAMES)} tools", text)

    def test_public_current_catalog_docs_match_live_tool_count(self) -> None:
        count = len(EXPECTED_STATELESS_TOOL_NAMES)
        expectations = {
            ROOT / "SPEC.md": f"default catalog contains {count} tools",
            ROOT / "README.md": f"{count} battle-tested tools",
            ROOT / "README.zh-CN.md": f"{count} 个工具",
        }
        for path, expected_text in expectations.items():
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8").lower()
                self.assertIn(expected_text.lower(), text)

    def test_v03_history_keeps_the_22_tool_snapshot(self) -> None:
        historical = {
            ROOT / "docs/runtime-contract-v0.3.md": "22 tools",
            ROOT / "docs/migration-0.3.md": "current catalog is 22 tools",
            ROOT / "CHANGELOG.md": "current default catalog is 22 tools",
        }
        for path, expected_text in historical.items():
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8").lower()
                self.assertIn(expected_text, text)

    def test_contract_error_enum_contains_live_tool_failure_codes(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "coding_tools_mcp").rglob("*.py"))
        )
        contract = self.CONTRACT_PATH.read_text(encoding="utf-8")
        codes = sorted(set(re.findall(r"ToolFailure\(\s*[\"']([A-Z_]+)[\"']", source)))
        missing = [code for code in codes if f'"{code}"' not in contract]
        self.assertEqual(missing, [])


def markdown_tool_sections(contract: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in contract.splitlines():
        if line.startswith("### "):
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines)
            heading = line.removeprefix("### ").strip()
            current_name = heading.split()[0]
            current_lines = [line]
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines)
    return sections
