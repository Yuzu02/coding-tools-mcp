from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.extensions.config import ConfigError
from coding_tools_mcp.extensions.projects.registry import (
    ProjectRegistryError,
    build_project_registry,
    build_project_registry_from_records,
)
from coding_tools_mcp.host_config import RegisteredProjectConfig
from coding_tools_mcp.server import Runtime


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.mkdir()
        (self.bootstrap / "pyproject.toml").write_text(
            "[project]\nname='bootstrap'\nversion='0'\n",
            encoding="utf-8",
        )
        self.runtime = Runtime(
            self.bootstrap,
            extension_config=RuntimeConfig.defaults(enabled=()),
        )
        self.validate_root = self.runtime.workspace_runtime_service.validate_root

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def project(self, relative: str) -> Path:
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            f"[project]\nname='{path.name}'\nversion='0'\n",
            encoding="utf-8",
        )
        return path

    def build(self, settings, *, fallback_root: Path | None = None):
        return build_project_registry(
            settings,
            fallback_root=fallback_root or self.bootstrap,
            validate_root=self.validate_root,
        )

    def test_ids_are_stable_and_independent_of_root_basename(self) -> None:
        first = self.project("one/same-name")
        second = self.project("two/same-name")

        registry = self.build(
            {
                "registry": {
                    "frontend": {"root": str(first)},
                    "api": {"root": str(second)},
                }
            }
        )

        self.assertEqual(registry.ids(), ("frontend", "api"))
        self.assertEqual(registry.get("frontend").root, first.resolve())
        self.assertEqual(registry.get("api").root, second.resolve())

    def test_snapshot_records_preserve_stable_id_in_registry(self) -> None:
        root = self.project("nested/root-basename")

        registry = build_project_registry_from_records(
            (
                RegisteredProjectConfig(
                    project_id="stable-api-id",
                    root=root,
                ),
            ),
            validate_root=self.validate_root,
        )

        self.assertEqual(registry.ids(), ("stable-api-id",))
        self.assertEqual(registry.get("stable-api-id").root, root.resolve())

    def test_empty_registry_synthesizes_default_project(self) -> None:
        registry = self.build({})

        self.assertEqual(registry.ids(), ("default",))
        project = registry.get("default")
        self.assertEqual(project.root, self.bootstrap.resolve())
        self.assertTrue(project.available)

    def test_invalid_project_id_is_rejected(self) -> None:
        project = self.project("project")
        with self.assertRaisesRegex(ConfigError, "invalid project_id"):
            self.build({"registry": {"bad id": {"root": str(project)}}})

    def test_missing_root_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "root is required"):
            self.build({"registry": {"missing-root": {}}})

    def test_duplicate_canonical_roots_are_rejected(self) -> None:
        project = self.project("project")
        with self.assertRaisesRegex(ConfigError, "same canonical root"):
            self.build(
                {
                    "registry": {
                        "a": {"root": str(project)},
                        "b": {"root": str(project / ".")},
                    }
                }
            )

    def test_missing_root_requires_explicit_allow_unavailable(self) -> None:
        missing = self.root / "missing"
        with self.assertRaises((ConfigError, ToolFailure)):
            self.build({"registry": {"missing": {"root": str(missing)}}})

    def test_allow_unavailable_freezes_project_as_unavailable_until_restart(self) -> None:
        missing = self.root / "missing"
        registry = self.build(
            {
                "registry": {
                    "missing": {
                        "root": str(missing),
                        "allow_unavailable": True,
                    }
                }
            }
        )

        project = registry.get("missing")
        self.assertFalse(project.available)
        self.assertEqual(project.root, missing.resolve(strict=False))
        self.assertTrue(project.warnings)

        missing.mkdir()
        with self.assertRaisesRegex(ProjectRegistryError, "unavailable") as raised:
            registry.require_available("missing")
        self.assertEqual(raised.exception.code, "PROJECT_UNAVAILABLE")

    def test_unknown_id_is_typed_not_found(self) -> None:
        registry = self.build({})
        with self.assertRaises(ProjectRegistryError) as raised:
            registry.get("missing")
        self.assertEqual(raised.exception.code, "PROJECT_NOT_FOUND")

    def test_resolve_absolute_uses_deepest_nested_registered_root(self) -> None:
        parent = self.project("parent")
        child = self.project("parent/child")
        target = child / "src" / "value.txt"
        target.parent.mkdir()
        target.write_text("child\n", encoding="utf-8")
        registry = self.build(
            {
                "registry": {
                    "parent": {"root": str(parent)},
                    "child": {"root": str(child)},
                }
            }
        )

        project, resolved = registry.resolve_absolute(target)

        self.assertEqual(project.project_id, "child")
        self.assertEqual(resolved, target.resolve())
        self.assertEqual(registry.excluded_roots_for("parent"), (child.resolve(),))
        self.assertEqual(registry.excluded_roots_for("child"), ())

    def test_resolve_absolute_rejects_relative_and_outside_paths(self) -> None:
        project = self.project("project")
        registry = self.build({"registry": {"app": {"root": str(project)}}})

        with self.assertRaises(ProjectRegistryError) as relative:
            registry.resolve_absolute("relative/path.txt")
        self.assertEqual(relative.exception.code, "INVALID_PROJECT_PATH")

        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        with self.assertRaises(ProjectRegistryError) as missing:
            registry.resolve_absolute(outside)
        self.assertEqual(missing.exception.code, "PROJECT_NOT_FOUND")

    def test_resolve_absolute_rejects_symlink_escape_from_registered_root(self) -> None:
        project = self.project("project")
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "secret.txt"
        target.write_text("outside\n", encoding="utf-8")
        link = project / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        registry = self.build({"registry": {"app": {"root": str(project)}}})

        with self.assertRaises(ProjectRegistryError) as raised:
            registry.resolve_absolute(link / "secret.txt")
        self.assertEqual(raised.exception.code, "INVALID_PROJECT_PATH")


if __name__ == "__main__":
    unittest.main()
