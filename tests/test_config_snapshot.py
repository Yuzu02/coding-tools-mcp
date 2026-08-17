from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from coding_tools_mcp.config_schema import table
from coding_tools_mcp.extensions.projects.extension import ProjectsExtension
from coding_tools_mcp.host_config import build_host_snapshot, load_host_config


def _q(path: Path | str) -> str:
    return json.dumps(str(path))


def _schemas() -> dict[str, object]:
    return {
        "projects": ProjectsExtension.manifest.config_schema,
        "semantic": table({}),
    }


def _write_config(
    path: Path,
    *,
    roots: list[tuple[str, Path]],
    port: int = 8000,
    auth_token_ref: str | None = None,
    reverse_registry_order: bool = False,
) -> None:
    ordered = list(reversed(roots)) if reverse_registry_order else roots
    lines = [
        "config_version = 2",
        "[runtime]",
        f"bootstrap_workspace = {_q(roots[0][1])}",
        "[transport]",
        'kind = "http"',
        'host = "127.0.0.1"',
        f"port = {port}",
        "[security]",
        f'auth_mode = {json.dumps("bearer" if auth_token_ref else "noauth")}',
    ]
    if auth_token_ref is not None:
        lines.append(f"auth_token_ref = {json.dumps(auth_token_ref)}")
    lines.extend(("[extensions]", 'enabled = ["projects", "semantic"]'))
    for project_id, root in ordered:
        lines.extend(
            (
                f"[extensions.projects.registry.{project_id}]",
                f"root = {_q(root)}",
            )
        )
    path.write_text("\n".join((*lines, "")), encoding="utf-8")


def _snapshot(path: Path):
    return build_host_snapshot(
        load_host_config(
            path,
            extension_schemas=_schemas(),
            default_enabled=("projects",),
        )
    )


class ConfigSnapshotTests(unittest.TestCase):
    def test_snapshot_and_nested_project_mapping_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            path = root / "host.toml"
            _write_config(path, roots=[("app", app)])

            snapshot = _snapshot(path)

            with self.assertRaises(TypeError):
                snapshot.projects["other"] = snapshot.projects["app"]  # type: ignore[index]
            with self.assertRaises(FrozenInstanceError):
                snapshot.projects["app"].source = path  # type: ignore[misc]

    def test_same_effective_config_has_same_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            path = root / "host.toml"
            _write_config(path, roots=[("app", app)])

            self.assertEqual(_snapshot(path).fingerprint, _snapshot(path).fingerprint)

    def test_non_secret_setting_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            path = root / "host.toml"
            _write_config(path, roots=[("app", app)], port=8000)
            first = _snapshot(path)
            _write_config(path, roots=[("app", app)], port=8001)
            second = _snapshot(path)

            self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_secret_reference_identity_changes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            path = root / "host.toml"
            _write_config(path, roots=[("app", app)], auth_token_ref="env:TOKEN_A")
            first = _snapshot(path)
            _write_config(path, roots=[("app", app)], auth_token_ref="env:TOKEN_B")
            second = _snapshot(path)

            self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_environment_value_behind_same_secret_reference_does_not_change_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            app.mkdir()
            path = root / "host.toml"
            _write_config(path, roots=[("app", app)], auth_token_ref="env:SNAPSHOT_TOKEN")

            with mock.patch.dict(os.environ, {"SNAPSHOT_TOKEN": "first"}):
                first = _snapshot(path)
            with mock.patch.dict(os.environ, {"SNAPSHOT_TOKEN": "second"}):
                second = _snapshot(path)

            self.assertEqual(first.fingerprint, second.fingerprint)

    def test_mapping_insertion_order_does_not_change_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app"
            lib = root / "lib"
            app.mkdir()
            lib.mkdir()
            first_path = root / "first.toml"
            second_path = root / "second.toml"
            roots = [("app", app), ("lib", lib)]
            _write_config(first_path, roots=roots)
            _write_config(second_path, roots=roots, reverse_registry_order=True)

            self.assertEqual(_snapshot(first_path).fingerprint, _snapshot(second_path).fingerprint)


if __name__ == "__main__":
    unittest.main()
