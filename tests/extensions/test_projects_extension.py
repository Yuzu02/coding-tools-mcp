from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions import (
    CORE_WORKSPACE,
    CORE_WORKSPACE_RUNTIMES,
    ContributionRegistry,
    ExtensionContext,
    RuntimeConfig,
    ServiceRegistry,
    builtin_extension_registry,
)
from coding_tools_mcp.extensions.projects import (
    PROJECT_CATALOG,
    PROJECT_REGISTRY,
    PROJECT_RUNTIMES,
    ProjectsExtension,
)
from coding_tools_mcp.host_config import build_developer_snapshot
from coding_tools_mcp.server import Runtime


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
            self.assertIn("list_projects", runtime.exposed_tool_names())
            self.assertIn("resolve_project", runtime.exposed_tool_names())
            self.assertIn("list_skills", runtime.exposed_tool_names())
            self.assertIn("read_skill", runtime.exposed_tool_names())
            self.assertEqual(len(runtime.exposed_tool_names()), 24)

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
            runtime = Runtime(root, extension_config=RuntimeConfig.defaults(enabled=()))
            services = ServiceRegistry()
            contributions = ContributionRegistry()
            try:
                services.provide(CORE_WORKSPACE, runtime.workspace)
                services.provide(CORE_WORKSPACE_RUNTIMES, runtime.workspace_runtime_service)
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
                registry = services.require(PROJECT_REGISTRY)
                runtimes = services.require(PROJECT_RUNTIMES)

                self.assertEqual(catalog.workspace, root.resolve())
                self.assertEqual(catalog.main_projects[0].scope_id, ".")
                self.assertEqual(registry.ids(), ("default",))
                self.assertEqual(registry.get("default").root, root.resolve())
                self.assertEqual(runtimes.active(), ())
                extension.stop()
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    runtimes.require("default")
            finally:
                runtime.close()

    def test_projects_extension_accepts_explicit_registered_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap = root / "bootstrap"
            alpha = root / "alpha"
            bootstrap.mkdir()
            alpha.mkdir()
            (bootstrap / "package.json").write_text("{}", encoding="utf-8")
            (alpha / "pyproject.toml").write_text("", encoding="utf-8")
            runtime = Runtime(bootstrap, extension_config=RuntimeConfig.defaults(enabled=()))
            services = ServiceRegistry()
            contributions = ContributionRegistry()
            try:
                services.provide(CORE_WORKSPACE, runtime.workspace)
                services.provide(CORE_WORKSPACE_RUNTIMES, runtime.workspace_runtime_service)
                extension = ProjectsExtension()
                extension.configure(
                    {
                        "registry": {
                            "alpha": {
                                "root": str(alpha),
                            }
                        }
                    }
                )
                extension.register(
                    ExtensionContext(
                        services=services,
                        contributions=contributions,
                        extension_name="projects",
                    )
                )

                registry = services.require(PROJECT_REGISTRY)
                self.assertEqual(registry.ids(), ("alpha",))
                self.assertEqual(registry.get("alpha").root, alpha.resolve())
            finally:
                runtime.close()

    def test_runtime_snapshot_project_identity_wins_over_legacy_extension_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap = root / "bootstrap"
            selected = root / "root-basename"
            legacy = root / "legacy"
            bootstrap.mkdir()
            selected.mkdir()
            legacy.mkdir()
            (bootstrap / "package.json").write_text("{}", encoding="utf-8")
            (selected / "pyproject.toml").write_text("", encoding="utf-8")
            (legacy / "pyproject.toml").write_text("", encoding="utf-8")

            snapshot_config = RuntimeConfig.defaults(
                enabled=("projects",),
                settings={
                    "projects": {
                        "registry": {
                            "stable-snapshot-id": {"root": str(selected)},
                        }
                    }
                },
            )
            snapshot = build_developer_snapshot(
                runtime_config=snapshot_config,
                bootstrap_workspace=bootstrap,
            )
            legacy_config = RuntimeConfig.defaults(
                enabled=("projects",),
                settings={
                    "projects": {
                        "registry": {
                            "legacy-id": {"root": str(legacy)},
                        }
                    }
                },
            )

            runtime = Runtime(
                bootstrap,
                extension_config=legacy_config,
                config_snapshot=snapshot,
            )
            try:
                payload = runtime.call_tool("list_projects", {})["structuredContent"]
                self.assertEqual(
                    [(item["id"], item["root"]) for item in payload["projects"]],
                    [("stable-snapshot-id", str(selected.resolve()))],
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
