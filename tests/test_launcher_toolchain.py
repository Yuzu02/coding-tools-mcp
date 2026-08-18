from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherToolchainTests(unittest.TestCase):
    def load_mise(self) -> dict[str, object]:
        return tomllib.loads((ROOT / "mise.toml").read_text(encoding="utf-8"))

    def test_mise_pins_required_tools_and_uv_environment(self) -> None:
        config = self.load_mise()
        tools = config["tools"]
        environment = config["env"]

        self.assertEqual(tools["python"], "3.14.7")
        self.assertEqual(tools["uv"], "0.12.5")
        self.assertEqual(tools["node"], "24.19.0")
        self.assertEqual(tools["rust"], "1.97.1")
        self.assertEqual(tools["gh"], "2.97.0")
        self.assertEqual(tools["github:openai/tunnel-client"], "0.0.11")
        self.assertEqual(environment["UV_PYTHON"], "3.13.12")

    def test_mise_tasks_use_locked_uv_and_reference_existing_launcher_tests(self) -> None:
        config = self.load_mise()
        tasks = config["tasks"]

        self.assertEqual(tasks["setup"]["run"], "uv sync --locked")
        self.assertEqual(tasks["setup-dev"]["run"], "uv sync --locked --extra dev")
        self.assertIn(
            "uv run --locked python scripts/start_services.py",
            tasks["start"]["run"],
        )

        launcher_command = tasks["test-launcher"]["run"]
        required_modules = (
            "tests.test_launcher_toolchain",
            "tests.test_launcher_config",
            "tests.test_launcher_diagnostics",
            "tests.test_launcher_tunnel",
            "tests.test_launcher_processes",
            "tests.test_launcher_integration",
        )
        for module in required_modules:
            with self.subTest(module=module):
                self.assertIn(module, launcher_command)
                relative = Path(*module.split("."))
                self.assertTrue((ROOT / relative).with_suffix(".py").is_file())

    def test_runtime_directory_is_ignored(self) -> None:
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".runtime/", lines)


if __name__ == "__main__":
    unittest.main()
