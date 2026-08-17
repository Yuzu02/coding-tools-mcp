#!/usr/bin/env python3
"""Start and supervise coding-tools-mcp plus an optional OpenAI tunnel."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.launcher.app import run_services  # noqa: E402
from scripts.launcher.config import ConfigError, resolve_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        config = resolve_config(arguments, repo_root=ROOT)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if config.permission_mode == "dangerous":
        print(
            "WARNING: dangerous permission mode disables command permission gates.",
            file=sys.stderr,
        )
    print(json.dumps(config.redacted_summary(), indent=2, sort_keys=True))
    return run_services(config)


if __name__ == "__main__":
    raise SystemExit(main())
