from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions import (
    CORE_WORKSPACE,
    ContributionRegistry,
    ExtensionContext,
    RuntimeConfig,
    ServiceRegistry,
    builtin_extension_registry,
)
from coding_tools_mcp.extensions.projects import PROJECT_CATALOG, ProjectsExtension
from coding_tools_mcp.server import Runtime, Workspace


@contextlib.contextmanager
def runtime_fixture(*, extension_config: RuntimeConfig | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text("{}", encoding="utf-8")
        registry = builtin_extension_registry()
        runtime = Runtime(
            root,
            extension_registry=registry,
            extension_config=extension_config,
        )
        try:
            yield runtime
        finally:
            runtime.close()


class ProjectsExtensionTests(unittest.TestCase):
    def test_builtin_registry_enables_projects_by_default(self) -> None:
        registry = builtin_extension_registry()
        self.assertEqual(registry.default_enabled, ("projects",))

    def test_default_runtime_still_exposes_list_and_read_skill(self) -> None:
        with runtime_fixture() as runtime:
            self.assertIn("list_skills", runtime.exposed_tool_names())
            self.assertIn("read_skill", runtime.exposed_tool_names())
            self.assertEqual(len(runtime.exposed_tool_names()), 22)

    def test_disabled_projects_extension_contributes_neither_skill_tool(self) -> None:
        config = RuntimeConfig.defaults(enabled=())
        with runtime_fixture(extension_config=config) as runtime:
            self.assertNotIn("list_skills", runtime.exposed_tool_names())
            self.assertNotIn("read_skill", runtime.exposed_tool_names())
            self.assertEqual(len(runtime.exposed_tool_names()), 20)

    def test_projects_extension_publishes_structural_project_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            services = ServiceRegistry()
            contributions = ContributionRegistry()
            workspace = Workspace(root)
            services.provide(CORE_WORKSPACE, workspace)
            extension = ProjectsExtension()
            extension.configure({})
            extension.register(
                ExtensionContext(
                    services=services,
                    contributions=contributions,
                    extension_name="projects",
                )
            )

            catalog = services.require(PROJECT_CATALOG)

            self.assertEqual(catalog.workspace, root.resolve())
            self.assertEqual(catalog.main_projects[0].project_id, ".")


if __name__ == "__main__":
    unittest.main()
