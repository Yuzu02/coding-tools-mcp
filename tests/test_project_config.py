from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.config_schema import ConfigError, table
from coding_tools_mcp.extensions.projects.extension import ProjectsExtension
from coding_tools_mcp.host_config import AuthorityKind, build_host_snapshot, load_host_config


def _q(path: Path | str) -> str:
    return json.dumps(str(path))


def _extension_schemas() -> dict[str, object]:
    return {
        "projects": ProjectsExtension.manifest.config_schema,
        "semantic": table({}),
    }


def _write_host_config(
    path: Path,
    *,
    bootstrap: Path,
    projects: dict[str, tuple[Path, str | None]],
    semantic: bool = True,
) -> None:
    enabled = '["projects", "semantic"]' if semantic else '["projects"]'
    lines = [
        "config_version = 2",
        "[runtime]",
        f"bootstrap_workspace = {_q(bootstrap)}",
        "[extensions]",
        f"enabled = {enabled}",
    ]
    for project_id, (root, project_config) in projects.items():
        lines.extend(
            (
                f"[extensions.projects.registry.{project_id}]",
                f"root = {_q(root)}",
            )
        )
        if project_config is not None:
            lines.append(f"project_config = {_q(project_config)}")
    path.write_text("\n".join((*lines, "")), encoding="utf-8")


def _load_snapshot(path: Path):
    config = load_host_config(
        path,
        extension_schemas=_extension_schemas(),
        default_enabled=("projects",),
    )
    return build_host_snapshot(config)


class ProjectConfigTests(unittest.TestCase):
    def test_authority_kind_exposes_all_approved_categories(self) -> None:
        self.assertEqual(
            {item.value for item in AuthorityKind},
            {
                "host-only",
                "project-select-from-host-set",
                "project-narrow-host-limit",
                "project-provide-data-under-host-policy",
            },
        )

    def test_missing_default_project_config_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, None)},
            )

            snapshot = _load_snapshot(host_path)

            self.assertIsNone(snapshot.projects["app"].source)
            self.assertIn("semantic", snapshot.projects["app"].enabled_capabilities)

    def test_missing_explicit_custom_project_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, "policy.toml")},
            )

            with self.assertRaisesRegex(ConfigError, "required project config does not exist"):
                _load_snapshot(host_path)

    def test_absolute_project_config_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            outside = root / "outside.toml"
            outside.write_text("project_config_version = 1\n", encoding="utf-8")
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, str(outside))},
            )

            with self.assertRaisesRegex(ConfigError, "project_config must be relative"):
                _load_snapshot(host_path)

    def test_project_config_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            outside = root / "outside.toml"
            outside.write_text("project_config_version = 1\n", encoding="utf-8")
            (app / "policy.toml").symlink_to(outside)
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, "policy.toml")},
            )

            with self.assertRaisesRegex(ConfigError, "escapes registered project root"):
                _load_snapshot(host_path)

    def test_parent_project_config_cannot_enter_registered_child_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent"
            child = parent / "child"
            child.mkdir(parents=True)
            (child / ".coding-tools-mcp.toml").write_text(
                "project_config_version = 1\n",
                encoding="utf-8",
            )
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=parent,
                projects={
                    "parent": (parent, "child/.coding-tools-mcp.toml"),
                    "child": (child, None),
                },
            )

            with self.assertRaisesRegex(ConfigError, "crosses registered project boundary"):
                _load_snapshot(host_path)

    def test_unknown_project_config_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            (app / ".coding-tools-mcp.toml").write_text(
                "project_config_version = 1\ntypo = true\n",
                encoding="utf-8",
            )
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, None)},
            )

            with self.assertRaisesRegex(ConfigError, "unknown configuration key: project.typo"):
                _load_snapshot(host_path)

    def test_project_can_disable_host_authorized_semantic_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            (app / ".coding-tools-mcp.toml").write_text(
                "\n".join(
                    (
                        "project_config_version = 1",
                        "[capabilities]",
                        'disabled = ["semantic"]',
                        "",
                    )
                ),
                encoding="utf-8",
            )
            host_path = root / "host.toml"
            _write_host_config(
                host_path,
                bootstrap=app,
                projects={"app": (app, None)},
            )

            snapshot = _load_snapshot(host_path)

            self.assertNotIn("semantic", snapshot.projects["app"].enabled_capabilities)

    def test_project_cannot_disable_unhosted_or_unknown_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            host_path = root / "host.toml"

            for capability, semantic in (("semantic", False), ("gateway", True)):
                with self.subTest(capability=capability, semantic=semantic):
                    (app / ".coding-tools-mcp.toml").write_text(
                        "\n".join(
                            (
                                "project_config_version = 1",
                                "[capabilities]",
                                f'disabled = ["{capability}"]',
                                "",
                            )
                        ),
                        encoding="utf-8",
                    )
                    _write_host_config(
                        host_path,
                        bootstrap=app,
                        projects={"app": (app, None)},
                        semantic=semantic,
                    )
                    with self.assertRaisesRegex(ConfigError, "not authorized by host"):
                        _load_snapshot(host_path)


if __name__ == "__main__":
    unittest.main()
