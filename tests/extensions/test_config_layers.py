from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions.config import load_runtime_config, scalar, table


class ConfigLayerTests(unittest.TestCase):
    def test_local_overlay_replaces_scalar_without_copying_public_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                """config_version = 1
[extensions]
enabled = ["fake"]
[extensions.fake]
backend = "public"
lazy = true
""",
                encoding="utf-8",
            )
            (root / "coding-tools.local.toml").write_text(
                """config_version = 1
[extensions.fake]
backend = "local"
""",
                encoding="utf-8",
            )
            schema = {"fake": table({"backend": scalar(str), "lazy": scalar(bool)})}

            config = load_runtime_config(
                cwd=root,
                extension_schemas=schema,
                default_enabled=(),
                environ={},
            )

            self.assertEqual(config.enabled_extensions, ("fake",))
            self.assertEqual(config.extension("fake"), {"backend": "local", "lazy": True})

    def test_environment_extensions_replace_toml_enabled_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                'config_version = 1\n[extensions]\nenabled = ["a"]\n',
                encoding="utf-8",
            )
            schemas = {"a": table({}), "b": table({})}

            config = load_runtime_config(
                cwd=root,
                extension_schemas=schemas,
                default_enabled=(),
                environ={"CODING_TOOLS_MCP_EXTENSIONS": "b"},
            )

            self.assertEqual(config.enabled_extensions, ("b",))

    def test_cli_extensions_override_environment(self) -> None:
        config = load_runtime_config(
            cwd=Path.cwd(),
            extension_schemas={"a": table({}), "b": table({})},
            default_enabled=("a",),
            environ={"CODING_TOOLS_MCP_EXTENSIONS": "a"},
            cli_extensions=("b",),
            public_path=False,
            local_path=False,
        )

        self.assertEqual(config.enabled_extensions, ("b",))

    def test_explicit_public_config_uses_sibling_local_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "cwd"
            config_dir = root / "config"
            cwd.mkdir()
            config_dir.mkdir()
            public = config_dir / "custom.toml"
            public.write_text(
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\nbackend = "public"\n',
                encoding="utf-8",
            )
            (config_dir / "coding-tools.local.toml").write_text(
                'config_version = 1\n[extensions.fake]\nbackend = "local"\n',
                encoding="utf-8",
            )

            config = load_runtime_config(
                cwd=cwd,
                extension_schemas={"fake": table({"backend": scalar(str)})},
                default_enabled=(),
                environ={},
                public_path=public,
            )

            self.assertEqual(config.extension("fake")["backend"], "local")
            self.assertEqual(config.sources, (public.resolve(), (config_dir / "coding-tools.local.toml").resolve()))

    def test_false_paths_disable_default_file_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n',
                encoding="utf-8",
            )

            config = load_runtime_config(
                cwd=root,
                extension_schemas={"fake": table({})},
                default_enabled=(),
                environ={},
                public_path=False,
                local_path=False,
            )

            self.assertEqual(config.enabled_extensions, ())
            self.assertEqual(config.sources, ())


if __name__ == "__main__":
    unittest.main()
