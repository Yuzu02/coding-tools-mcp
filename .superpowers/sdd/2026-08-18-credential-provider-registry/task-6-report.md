# Task 6 report — operator migration documentation

## Result

Commit: `62c261e` (docs: document credential provider migration)
Follow-up: `bc1b2db` (docs: clarify credential service identity)

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
Rollback deliberately leaves all credential fragments and broker subtrees
untouched. Because the block has no reservation/locking mechanism, it cannot
safely distinguish its writes from pre-existing or concurrent state. Any
cleanup therefore requires a separate, manually reviewed `doctor` plus
dry-run/explicit `remove --apply` operation.

The design spec and implementation plan now use the same wording: unit/drop-in
rollback is deterministic, while credential provisioning is not blindly
rerunnable and rollback never deletes credential state.

## Documentation-safe checks

- `uv run --locked python scripts/credentials.py --help` — passed; all four
  commands (`list`, `doctor`, `provision`, `remove`) are present.
- `uv run --locked python scripts/credentials.py --registry-dir '<registry-dir>' --broker-dir '<broker-dir>' --help` — passed.
- Documentation-safe `doctor --help` and `provision --help` checks with generic
  service UID/GID placeholders — passed.
- `git diff --check` — passed.
- Migration document contains one fenced block (the required migration/
  rollback block) and no unused `HOST_CONFIG` variable.
- Reviewed the current CLI after the Task 5 parent-ownership fix (`07c6ead`):
  `doctor` exposes redacted `checks.*.safe` results and `doctor --system` keeps
  its explicit root gate. The documentation records the intended nonzero exit
  contract for unsafe checks; no host or migration command was executed.
- `uv run python -m unittest tests.test_public_fork_hygiene -v` — passed (2
  tests); removed private host/repository markers from the implementation plan.
- Reviewed owned docs for stale `security.exec_credentials` authority and
  secret/tunnel-specific migration values — none found in the new provider
  guidance or migration block.

## Limitations and safety

No migration, deployment, systemd, root, host, tunnel, or external operation
was run. The documented block remains an operator-run procedure and requires
review of every placeholder and exact legacy bind entry before use. Concurrent
non-owned changes in `coding_tools_mcp/credential_admin.py`,
`scripts/credentials.py`, tests, and design/plan files were preserved.
