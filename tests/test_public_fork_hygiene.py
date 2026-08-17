from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def tracked_paths() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return tuple(path for path in result.stdout.decode().split("\0") if path)


class PublicForkHygieneTests(unittest.TestCase):
    def test_host_specific_deployment_state_is_not_tracked(self) -> None:
        paths = tracked_paths()

        real_units = [
            path
            for path in paths
            if path.startswith("deploy/systemd/") and path.endswith(".service")
        ]
        self.assertEqual(real_units, [])
        self.assertNotIn("docs/ops/deployed-instances.md", paths)
        self.assertNotIn("coding-tools.local.toml", paths)
        private_host_configs = [
            path
            for path in paths
            if path.startswith("deploy/host-config/") and path.endswith(".toml")
        ]
        self.assertEqual(private_host_configs, [])
        ignore_lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("deploy/systemd/*.service", ignore_lines)
        self.assertIn("deploy/host-config/*.toml", ignore_lines)
        self.assertIn("coding-tools.local.toml", ignore_lines)

    def test_tracked_text_has_no_private_host_markers(self) -> None:
        markers = (
            "/home/" + "yuzu",
            "SICOTI" + "Lab",
            "CON" + "OSCE",
            "SICOTI" + ".git.dev",
            "sicoti" + "-dev",
            "/etc/" + "sicoti",
            "/srv/" + "sicoti",
            "/opt/" + "sicoti",
        )
        real_tunnel_id = re.compile(r"\btunnel_6a[0-9a-f]{20,}\b")

        violations: list[str] = []
        for relative in tracked_paths():
            path = ROOT / relative
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for marker in markers:
                if marker in text:
                    violations.append(f"{relative}: contains private marker")
            if real_tunnel_id.search(text):
                violations.append(f"{relative}: contains a concrete tunnel id")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
