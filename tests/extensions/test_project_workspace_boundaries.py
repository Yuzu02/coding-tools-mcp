from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions import RuntimeConfig
from coding_tools_mcp.server import Runtime


class ProjectWorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bootstrap = self.root / "bootstrap"
        self.parent = self.root / "parent"
        self.child = self.parent / "child"
        self.src = self.parent / "src"
        self.bootstrap.mkdir()
        self.child.mkdir(parents=True)
        self.src.mkdir()
        for directory in (self.bootstrap, self.parent, self.child):
            (directory / "pyproject.toml").write_text(
                "[project]\nname='fixture'\nversion='0'\n",
                encoding="utf-8",
            )
        (self.src / "ok.txt").write_text("PARENT_OK\n", encoding="utf-8")
        (self.child / "secret.txt").write_text("SECRET_CHILD_TOKEN\n", encoding="utf-8")
        try:
            (self.parent / "child-link").symlink_to(self.child, target_is_directory=True)
            self.has_symlink = True
        except OSError:
            self.has_symlink = False

        self.runtime = Runtime(
            self.bootstrap,
            extension_config=RuntimeConfig.defaults(enabled=()),
        )
        self.service = self.runtime.workspace_runtime_service
        self.parent_handle = self.service.create(
            self.parent,
            excluded_roots=(self.child,),
        )
        self.child_handle = self.service.create(self.child)

    def tearDown(self) -> None:
        self.runtime.close()
        self.temporary.cleanup()

    def parent_call(self, handler, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.service.invoke(self.parent_handle, handler, arguments)

    def test_direct_read_into_registered_child_is_rejected(self) -> None:
        with self.assertRaisesRegex(ToolFailure, "separately registered project") as raised:
            self.parent_call(self.runtime.read_file, {"path": "child/secret.txt"})
        self.assertEqual(raised.exception.code, "PATH_OUTSIDE_WORKSPACE")

    def test_write_resolution_into_registered_child_is_rejected(self) -> None:
        def resolve_write(_args: dict[str, Any]) -> dict[str, Any]:
            resolved = self.runtime.workspace.resolve_for_write("child/new.txt")
            return {"path": resolved.display}

        with self.assertRaisesRegex(ToolFailure, "separately registered project") as raised:
            self.parent_call(resolve_write, {})
        self.assertEqual(raised.exception.code, "PATH_OUTSIDE_WORKSPACE")

    def test_recursive_listing_prunes_registered_child_even_when_ignored_files_are_included(self) -> None:
        result = self.parent_call(
            self.runtime.list_dir,
            {
                "path": ".",
                "recursive": True,
                "max_depth": 4,
                "include_hidden": True,
                "include_ignored": True,
            },
        )
        paths = [str(item["path"]) for item in result["entries"]]
        self.assertIn("src/ok.txt", paths)
        self.assertFalse(any(path == "child" or path.startswith("child/") for path in paths), paths)

    def test_file_listing_prunes_registered_child_even_when_ignored_files_are_included(self) -> None:
        result = self.parent_call(
            self.runtime.list_files,
            {
                "path": ".",
                "patterns": ["**/*"],
                "include_hidden": True,
                "include_ignored": True,
            },
        )
        paths = [str(item["path"]) for item in result["files"]]
        self.assertIn("src/ok.txt", paths)
        self.assertNotIn("child/secret.txt", paths)

    def test_search_does_not_return_matches_from_registered_child(self) -> None:
        result = self.parent_call(
            self.runtime.search_text,
            {
                "query": "SECRET_CHILD_TOKEN",
                "path": ".",
                "include_hidden": True,
                "include_ignored": True,
            },
        )
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["total_matches"], 0)

    def test_symlink_into_registered_child_is_rejected(self) -> None:
        if not self.has_symlink:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ToolFailure, "separately registered project") as raised:
            self.parent_call(self.runtime.read_file, {"path": "child-link/secret.txt"})
        self.assertEqual(raised.exception.code, "PATH_OUTSIDE_WORKSPACE")

    def test_child_runtime_can_access_its_own_files(self) -> None:
        result = self.service.invoke(
            self.child_handle,
            self.runtime.read_file,
            {"path": "secret.txt"},
        )
        self.assertEqual(result["content"], "SECRET_CHILD_TOKEN\n")


if __name__ == "__main__":
    unittest.main()
