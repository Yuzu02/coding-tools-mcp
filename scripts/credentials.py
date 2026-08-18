#!/usr/bin/env python3
"""Host-only credential broker administration CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from coding_tools_mcp.credential_admin import CredentialAdmin, CredentialAdminError, ProvisionRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="credentials")
    parser.add_argument("--registry-dir", type=Path, required=True)
    parser.add_argument("--broker-dir", type=Path, required=True)
    parser.add_argument("--service-uid", type=int)
    parser.add_argument("--service-gid", type=int)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--system", action="store_true")
    provision = commands.add_parser("provision")
    provision.add_argument("--name", required=True)
    provision.add_argument("--command", dest="commands", action="append", required=True)
    provision.add_argument("--source", type=Path, required=True)
    provision.add_argument("--read-root", dest="read_roots", action="append", default=[])
    provision.add_argument("--write-root", dest="write_roots", action="append", default=["state"])
    provision.add_argument("--apply", action="store_true")
    remove = commands.add_parser("remove")
    remove.add_argument("name")
    remove.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    admin = CredentialAdmin(args.registry_dir, args.broker_dir, service_uid=args.service_uid, service_gid=args.service_gid)
    try:
        if args.command == "list":
            report = admin.list()
        elif args.command == "doctor":
            report = admin.doctor(system=args.system)
        elif args.command == "provision":
            request = ProvisionRequest(args.name, tuple(args.commands), args.source, tuple(args.read_roots), tuple(args.write_roots))
            report = admin.provision(request, apply=args.apply)
        else:
            report = admin.remove(args.name, apply=args.apply)
    except CredentialAdminError as exc:
        build_parser().error(str(exc))
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
