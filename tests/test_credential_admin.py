from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.credential_admin import (
    CredentialAdmin,
    CredentialAdminError,
    ProvisionRequest,
)


class CredentialAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = root / "credentials.d"
        self.broker = root / "broker"
        self.registry.mkdir()
        self.broker.mkdir()
        self.admin = CredentialAdmin(self.registry, self.broker, service_uid=os.geteuid(), service_gid=os.getegid())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, source: Path | None = None) -> ProvisionRequest:
        source = source or Path(self.tmp.name) / "store"
        source.mkdir(exist_ok=True)
        (source / "secret.txt").write_text("do-not-print", encoding="utf-8")
        return ProvisionRequest(
            name="example", commands=("example-cli",), source=source,
            read_roots=("read",), write_roots=("state",),
        )

    def test_provision_dry_run_does_not_publish_or_copy(self) -> None:
        report = self.admin.provision(self.request(), apply=False)
        self.assertEqual(report["action"], "provision")
        self.assertFalse((self.registry / "example.toml").exists())
        self.assertFalse((self.broker / "example").exists())

    def test_dry_run_rejects_malformed_command(self) -> None:
        request = self.request()
        malformed = ProvisionRequest(request.name, ("bad command",), request.source, request.read_roots, request.write_roots)
        with self.assertRaises(CredentialAdminError):
            self.admin.provision(malformed, apply=False)

    def test_dry_run_rejects_invalid_existing_registry(self) -> None:
        (self.registry / "broken.toml").write_text('name="broken"\ncommands=[]\n', encoding="utf-8")
        with self.assertRaisesRegex(CredentialAdminError, "invalid"):
            self.admin.provision(self.request(), apply=False)

    def test_apply_requires_explicit_service_account(self) -> None:
        admin = CredentialAdmin(self.registry, self.broker)
        with self.assertRaisesRegex(CredentialAdminError, "service UID and GID"):
            admin.provision(self.request(), apply=True, euid=0)

    def test_apply_rejects_root_or_unknown_service_identity(self) -> None:
        admin = CredentialAdmin(self.registry, self.broker, service_uid=0, service_gid=0)
        with self.assertRaisesRegex(CredentialAdminError, "non-root"):
            admin.provision(self.request(), apply=True, euid=0)

    def test_apply_requires_root(self) -> None:
        with self.assertRaisesRegex(CredentialAdminError, "explicit root"):
            self.admin.provision(self.request(), apply=True, euid=1000)

    def test_rejects_symlink_source_and_unsafe_remove(self) -> None:
        source = Path(self.tmp.name) / "source"
        source.mkdir()
        (source / "link").symlink_to(Path(self.tmp.name) / "outside")
        with self.assertRaises(CredentialAdminError):
            self.admin.provision(self.request(source), apply=False)
        with self.assertRaisesRegex(CredentialAdminError, "provider broker subtree"):
            self.admin.remove("../outside", apply=True)

    def test_apply_stages_modes_and_publishes_fragment(self) -> None:
        report = self.admin.provision(self.request(), apply=True, euid=0)
        self.assertEqual(report["action"], "provision")
        self.assertEqual((self.broker / "example" / "state" / "secret.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.broker / "example").stat().st_mode & 0o777, 0o700)
        self.assertIn('name = "example"', (self.registry / "example.toml").read_text())

    def test_remove_withdraws_fragment_before_broker_cleanup(self) -> None:
        self.admin.provision(self.request(), apply=True, euid=0)
        events: list[str] = []
        original = self.admin._remove_tree
        self.admin._remove_tree = lambda path: (events.append(f"tree:{path.exists()}"), original(path))[1]  # type: ignore[method-assign]
        self.admin.remove("example", apply=True, euid=0)
        self.assertEqual(events, ["tree:True"])
        self.assertFalse((self.registry / "example.toml").exists())

    def test_list_and_doctor_are_redacted(self) -> None:
        self.admin.provision(self.request(), apply=True, euid=0)
        output = json.dumps(self.admin.list()) + json.dumps(self.admin.doctor())
        self.assertNotIn("do-not-print", output)

    def test_doctor_reports_mode_and_owner_violations(self) -> None:
        self.admin.provision(self.request(), apply=True, euid=0)
        (self.broker / "example" / "state" / "secret.txt").chmod(0o644)
        report = self.admin.doctor()
        self.assertFalse(report["checks"]["broker"]["safe"])

    def test_doctor_system_uses_bounded_systemctl_show(self) -> None:
        calls: list[list[str]] = []
        class Result:
            returncode = 0
            stdout = "LoadState=loaded\nActiveState=active\nSubState=running\n"
        def runner(command: list[str], **_kwargs: object) -> Result:
            calls.append(command)
            return Result()
        report = self.admin.doctor(system=True, euid=0, systemctl_runner=runner)
        self.assertEqual(report["systemctl"]["returncode"], 0)
        self.assertEqual(calls[0][0:2], ["systemctl", "show"])

    def test_doctor_system_uses_timeout(self) -> None:
        seen: dict[str, object] = {}
        class Result:
            returncode = 0
            stdout = ""
        def runner(_command: list[str], **kwargs: object) -> Result:
            seen.update(kwargs)
            return Result()
        self.admin.doctor(system=True, euid=0, systemctl_runner=runner)
        self.assertEqual(seen["timeout"], 5)


class CredentialAdminCliTests(unittest.TestCase):
    def test_cli_help_lists_commands(self) -> None:
        from scripts.credentials import build_parser
        help_text = build_parser().format_help()
        for command in ("list", "doctor", "provision", "remove"):
            self.assertIn(command, help_text)


if __name__ == "__main__":
    unittest.main()
