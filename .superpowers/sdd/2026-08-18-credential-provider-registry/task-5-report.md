# Task 5 report

Commits: `c7a0e6fc9ce2e1a135dc29b5685d98eef77b400a`, `b875a862eaba0968e7c52f45166d7d51abf87e7e`, `40fb9d24c32e42466eee8afccfb152b4e9fee47d`, `07c6eadafdbb850c72930c19147d88169fc579bc`, `3e388d85ee51b02366bcde79323ac414a6c54f78`

Files:

- `coding_tools_mcp/credential_admin.py`
- `scripts/credentials.py`
- `tests/test_credential_admin.py`

Validation:

- RED: `uv run python -m unittest tests.test_credential_admin -v` failed at import because `credential_admin` did not yet exist.
- GREEN: same command passed, 16 tests.
- GREEN: `uv run ruff check coding_tools_mcp/credential_admin.py scripts/credentials.py tests/test_credential_admin.py` passed.
- CLI smoke: `uv run python scripts/credentials.py --help` lists `list`, `doctor`, `provision`, and `remove`.
- Dry-run validates malformed fragments through `CredentialProviderRegistry` without persistence.
- Apply requires explicit service UID/GID and stages ownership/modes accordingly.
- Doctor recursively checks modes/ownership and `doctor --system` uses bounded `systemctl show` output via an injectable runner.
- Dry-run/apply validate the complete effective registry, preserving existing fragments and replacing only the candidate.
- Service identities reject root, non-positive, and unknown UID/GID values; systemctl calls have a five-second timeout.
- Broker/registry parents are root-owned with service-group traversal/read access; provider stores remain service-owned 0700/0600 and fragments root-owned 0640.
- Doctor returns `ok=false` for unsafe layouts and the CLI exits non-zero.
- Broker doctor audit distinguishes the root parent from service-owned provider descendants; provisioned stores are not falsely flagged.

Limitations: tests inject effective UIDs and never perform host deployment; production apply remains root-gated.
