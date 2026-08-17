from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions.config import ConfigError, list_of, load_runtime_config, scalar, table


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class ConfigValidationTests(unittest.TestCase):
    def test_extension_config_reexports_shared_schema_types(self) -> None:
        from coding_tools_mcp import config_schema
        from coding_tools_mcp.extensions import config as extension_config

        self.assertIs(extension_config.ConfigError, config_schema.ConfigError)
        self.assertIs(extension_config.ConfigNode, config_schema.ConfigNode)

    def test_unknown_root_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", "config_version = 1\ntypo = true\n")

            with self.assertRaisesRegex(ConfigError, "unknown configuration key: config.typo"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_unknown_extension_table_fails_even_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                "config_version = 1\n[extensions]\nenabled = []\n[extensions.missing]\n",
            )

            with self.assertRaisesRegex(ConfigError, "unknown extension: missing"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({})},
                    default_enabled=(),
                    environ={},
                )

    def test_public_and_local_versions_must_match_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", "config_version = 1\n[extensions]\nenabled = []\n")
            write(root / "coding-tools.local.toml", "config_version = 2\n[extensions]\nenabled = []\n")

            with self.assertRaisesRegex(ConfigError, "config_version"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_list_replaces_instead_of_concatenating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\npaths = ["public-a", "public-b"]\n',
            )
            write(
                root / "coding-tools.local.toml",
                'config_version = 1\n[extensions.fake]\npaths = ["local-only"]\n',
            )

            config = load_runtime_config(
                cwd=root,
                extension_schemas={"fake": table({"paths": list_of(scalar(str))})},
                default_enabled=(),
                environ={},
            )

            self.assertEqual(config.extension("fake")["paths"], ("local-only",))

    def test_unknown_nested_key_does_not_participate_in_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\nbackend = "ok"\ntypo = true\n',
            )

            with self.assertRaisesRegex(ConfigError, "extensions.fake.typo"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({"backend": scalar(str)})},
                    default_enabled=(),
                    environ={},
                )

    def test_malformed_toml_fails_with_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", "config_version = 1\n[extensions\n")

            with self.assertRaisesRegex(ConfigError, "could not read config"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_non_integer_config_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", 'config_version = "1"\n[extensions]\nenabled = []\n')

            with self.assertRaisesRegex(ConfigError, "config_version must be 1"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_duplicate_enabled_names_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake", "fake"]\n',
            )

            with self.assertRaisesRegex(ConfigError, "duplicate enabled extension: fake"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({})},
                    default_enabled=(),
                    environ={},
                )

    def test_invalid_extension_name_fails(self) -> None:
        config_text = 'config_version = 1\n[extensions]\nenabled = ["bad.name"]\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", config_text)

            with self.assertRaisesRegex(ConfigError, "invalid extension name: bad.name"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"bad.name": table({})},
                    default_enabled=(),
                    environ={},
                )

    def test_enabled_name_without_registered_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", 'config_version = 1\n[extensions]\nenabled = ["missing"]\n')

            with self.assertRaisesRegex(ConfigError, "unknown extension: missing"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_bool_is_rejected_for_integer_scalar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\nworkers = true\n',
            )

            with self.assertRaisesRegex(ConfigError, "extensions.fake.workers must be one of: int"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({"workers": scalar(int)})},
                    default_enabled=(),
                    environ={},
                )

    def test_environment_extension_list_rejects_empty_middle_name(self) -> None:
        with self.assertRaisesRegex(ConfigError, "extension list contains an empty name"):
            load_runtime_config(
                cwd=Path.cwd(),
                extension_schemas={"a": table({}), "b": table({})},
                default_enabled=(),
                environ={"CODING_TOOLS_MCP_EXTENSIONS": "a,,b"},
                public_path=False,
                local_path=False,
            )


if __name__ == "__main__":
    unittest.main()
