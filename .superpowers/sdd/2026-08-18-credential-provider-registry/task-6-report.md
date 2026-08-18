# Task 6 report — operator migration documentation

## Result

Commit: `62c261e` (docs: document credential provider migration)

Owned files:

- `docs/services-launcher.md`
- `docs/credential-provider-migration.md`
- this report

The launcher guide now treats `credentials.d` beside HostConfig and
`<state-root>/credentials` as the dynamic provider authority. It documents the
provider TOML contract without secret values, the no-personal-home invariant,
lazy generation reload and fail-closed invalidation, all-command Landlock
enforcement (including the `dangerous` trade-off), CLI dry-run defaults, and
the relevant `server_info` fields. The migration page contains exactly one
root-only migration/rollback block, uses placeholders only, and is explicitly
labelled `NEVER RUN`.

The migration block now passes explicit service UID/GID to `provision --apply`
and `doctor`, documents broker ownership checks and the separate root gate for
`doctor --system`, and triggers rollback automatically on verification failure.

## Documentation-safe checks

- `uv run --locked python scripts/credentials.py --help` — passed; all four
  commands (`list`, `doctor`, `provision`, `remove`) are present.
- `uv run --locked python scripts/credentials.py --registry-dir '<registry-dir>' --broker-dir '<broker-dir>' --help` — passed.
- Documentation-safe `doctor --help` and `provision --help` checks with generic
  service UID/GID placeholders — passed.
- `git diff --check` — passed.
- Migration document contains one fenced block (the required migration/
  rollback block) and no unused `HOST_CONFIG` variable.
- Reviewed owned docs for stale `security.exec_credentials` authority and
  secret/tunnel-specific migration values — none found in the new provider
  guidance or migration block.

## Limitations and safety

No migration, deployment, systemd, root, host, tunnel, or external operation
was run. The documented block remains an operator-run procedure and requires
review of every placeholder and exact legacy bind entry before use. Concurrent
non-owned changes in `coding_tools_mcp/credential_admin.py`,
`scripts/credentials.py`, tests, and design/plan files were preserved.
