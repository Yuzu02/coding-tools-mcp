from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from coding_tools_mcp.errors import ToolFailure
from coding_tools_mcp.extensions.projects.registry import ProjectRegistry, RegisteredProject
from coding_tools_mcp.extensions.projects.runtime import (
    CommandOwnershipIndex,
    ProjectRuntimeManager,
)


@dataclass(frozen=True)
class FakeHandle:
    root: Path


class FakeWorkspaceRuntimeService:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.closed: list[FakeHandle] = []

    def validate_root(self, root: Path, *, require_exists: bool = True) -> Path:
        return root.resolve(strict=require_exists)

    def create(
        self,
        root: Path,
        *,
        excluded_roots: tuple[Path, ...] = (),
        on_command_registered=None,
        on_command_removed=None,
    ) -> FakeHandle:
        handle = FakeHandle(root.resolve())
        self.created.append(
            {
                "handle": handle,
                "excluded_roots": excluded_roots,
                "on_command_registered": on_command_registered,
                "on_command_removed": on_command_removed,
            }
        )
        return handle

    def invoke(self, handle, handler, arguments):
        return handler(arguments)

    def resolve_existing(self, handle, raw_path: str = "."):
        resolved = (handle.root / raw_path).resolve(strict=True)
        return SimpleNamespace(
            display=resolved.relative_to(handle.root).as_posix() or ".",
            path=resolved,
        )

    def close(self, handle) -> None:
        self.closed.append(handle)


class ProjectRuntimeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.parent = self._project("parent")
        self.child = self._project("parent/child")
        self.other = self._project("other")
        (self.parent / "AGENTS.md").write_text("PARENT RULES\n", encoding="utf-8")
        self.missing = self.root / "missing"
        self.registry = ProjectRegistry(
            (
                RegisteredProject("parent", self.parent.resolve(), ("pyproject.toml",), True),
                RegisteredProject("child", self.child.resolve(), ("pyproject.toml",), True),
                RegisteredProject("other", self.other.resolve(), ("pyproject.toml",), True),
                RegisteredProject(
                    "missing",
                    self.missing.resolve(strict=False),
                    (),
                    False,
                    ("unavailable",),
                ),
            )
        )
        self.service = FakeWorkspaceRuntimeService()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _project(self, relative: str) -> Path:
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            f"[project]\nname='{path.name}'\nversion='0'\n",
            encoding="utf-8",
        )
        return path

    def manager(self) -> ProjectRuntimeManager:
        return ProjectRuntimeManager(self.registry, self.service)

    def test_runtime_creation_is_lazy_and_cached_per_project(self) -> None:
        manager = self.manager()
        self.assertEqual(self.service.created, [])

        first = manager.require("parent")
        second = manager.require("parent")

        self.assertIs(first, second)
        self.assertEqual(len(self.service.created), 1)
        self.assertEqual(first.workspace.root, self.parent.resolve())
        self.assertIs(first.skills.project_catalog, first.catalog)
        self.assertEqual(first.project.root, self.parent.resolve())
        self.assertEqual(first.project_context.root_files[0].path, "AGENTS.md")

    def test_concurrent_require_creates_one_runtime(self) -> None:
        manager = self.manager()
        with ThreadPoolExecutor(max_workers=16) as pool:
            runtimes = list(pool.map(lambda _: manager.require("parent"), range(100)))

        self.assertEqual(len({id(runtime) for runtime in runtimes}), 1)
        self.assertEqual(len(self.service.created), 1)

    def test_distinct_projects_receive_distinct_workspace_handles(self) -> None:
        manager = self.manager()
        parent = manager.require("parent")
        other = manager.require("other")

        self.assertIsNot(parent.workspace, other.workspace)
        self.assertEqual(len(self.service.created), 2)

    def test_parent_runtime_excludes_nested_registered_child(self) -> None:
        manager = self.manager()
        manager.require("parent")

        self.assertEqual(
            self.service.created[0]["excluded_roots"],
            (self.child.resolve(),),
        )

    def test_unknown_and_unavailable_projects_are_typed_without_creating_handles(self) -> None:
        manager = self.manager()

        with self.assertRaises(ToolFailure) as missing:
            manager.require("unknown")
        self.assertEqual(missing.exception.code, "PROJECT_NOT_FOUND")

        with self.assertRaises(ToolFailure) as unavailable:
            manager.require("missing")
        self.assertEqual(unavailable.exception.code, "PROJECT_UNAVAILABLE")
        self.assertEqual(self.service.created, [])

    def test_command_callbacks_are_bound_to_project_identity(self) -> None:
        manager = self.manager()
        manager.require("parent")
        registration = self.service.created[0]["on_command_registered"]
        removal = self.service.created[0]["on_command_removed"]
        assert callable(registration)
        assert callable(removal)

        registration("command-1")
        self.assertEqual(manager.command_owners.owner("command-1"), "parent")
        removal("command-1")
        with self.assertRaises(ToolFailure) as missing:
            manager.command_owners.owner("command-1")
        self.assertEqual(missing.exception.code, "COMMAND_NOT_FOUND")

    def test_command_ownership_collision_is_rejected(self) -> None:
        owners = CommandOwnershipIndex()
        owners.register("parent", "command-1")
        with self.assertRaisesRegex(RuntimeError, "ownership collision"):
            owners.register("other", "command-1")

    def test_close_closes_each_created_runtime_once_and_is_idempotent(self) -> None:
        manager = self.manager()
        parent = manager.require("parent")
        other = manager.require("other")

        self.assertEqual(manager.close(), ())
        self.assertEqual(manager.close(), ())

        self.assertCountEqual(self.service.closed, [parent.workspace, other.workspace])
        self.assertEqual(len(self.service.closed), 2)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            manager.require("child")


if __name__ == "__main__":
    unittest.main()
