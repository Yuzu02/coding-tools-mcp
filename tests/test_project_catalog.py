from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.project_catalog import build_project_catalog


class ProjectCatalogTests(unittest.TestCase):
    def test_workspace_root_marker_becomes_dot_main_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            catalog = build_project_catalog(root)

            self.assertEqual([project.project_id for project in catalog.main_projects], ["."])
            self.assertEqual(catalog.main_projects[0].display_root, ".")
            self.assertEqual(catalog.main_projects[0].markers, ("pyproject.toml",))

    def test_direct_children_are_main_projects_when_workspace_is_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = root / "api"
            data = root / "data"
            api.mkdir()
            data.mkdir()
            (api / "package.json").write_text("{}", encoding="utf-8")
            (data / "pyproject.toml").write_text("", encoding="utf-8")
            (root / "notes").mkdir()

            catalog = build_project_catalog(root)

            self.assertEqual([project.project_id for project in catalog.main_projects], ["api", "data"])
            self.assertEqual(catalog.warnings, ())

    def test_nested_project_is_only_selected_for_contained_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "sdk"
            nested = main / "repos" / "effect"
            sibling = main / "src"
            nested.mkdir(parents=True)
            sibling.mkdir(parents=True)
            (main / "package.json").write_text("{}", encoding="utf-8")
            (nested / "package.json").write_text("{}", encoding="utf-8")

            catalog = build_project_catalog(root)

            main_selection = catalog.resolve(main)
            nested_selection = catalog.resolve(nested)
            sibling_selection = catalog.resolve(sibling)

            self.assertIsNotNone(main_selection)
            self.assertIsNotNone(nested_selection)
            self.assertIsNotNone(sibling_selection)
            assert main_selection is not None
            assert nested_selection is not None
            assert sibling_selection is not None
            self.assertEqual(main_selection.subprojects, ())
            self.assertEqual(sibling_selection.subprojects, ())
            self.assertEqual(
                [project.project_id for project in nested_selection.scope_chain],
                ["sdk", "sdk/repos/effect"],
            )

    def test_file_path_resolves_using_its_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "app"
            source = project / "src" / "main.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('ok')\n", encoding="utf-8")
            (project / "pyproject.toml").write_text("", encoding="utf-8")

            selection = build_project_catalog(root).resolve(source)

            self.assertIsNotNone(selection)
            assert selection is not None
            self.assertEqual(selection.main_project.project_id, "app")

    def test_excluded_directory_is_not_discovered_as_subproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "app"
            generated = project / "node_modules" / "dependency"
            generated.mkdir(parents=True)
            (project / "package.json").write_text("{}", encoding="utf-8")
            (generated / "package.json").write_text("{}", encoding="utf-8")

            selection = build_project_catalog(root).resolve(generated)

            self.assertIsNotNone(selection)
            assert selection is not None
            self.assertEqual(selection.subprojects, ())

    def test_workspace_root_outside_main_projects_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "app"
            project.mkdir()
            (project / "package.json").write_text("{}", encoding="utf-8")

            selection = build_project_catalog(root).resolve(root)

            self.assertIsNone(selection)

    def test_path_outside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            project = root / "app"
            project.mkdir()
            (project / "package.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(ValueError):
                build_project_catalog(root).resolve(Path(other))


if __name__ == "__main__":
    unittest.main()
