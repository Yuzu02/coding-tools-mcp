# Task 7 report — integration regressions

## Scope

Updated only `tests/extensions/test_config_startup.py` and
`docs/runtime-contract-v0.4.md` for the Task 7 integration regressions.

## Reproduction

Command:

```text
uv run python -m unittest tests.extensions.test_config_startup
```

Before the fix, the focused suite reproduced the five specified failures:

- the startup fixture still used removed `security.exec_credentials`;
- host workspace startup lacked `runtime.state_root`;
- bearer HTTP startup failed because of the missing state root;
- OAuth HTTP startup failed because of the missing state root;
- the two HTTP tests consequently returned exit code 2 instead of 0.

## Changes

- Converted the host credential startup fixture to publish a dynamic
  `credentials.d/vercel.toml` fragment and assert registry-backed providers,
  while confirming no static credential authority is present.
- Made the host fixture provide an isolated `runtime.state_root` by default;
  explicit storage-root tests retain their own roots.
- Added `CREDENTIAL_SANDBOX_UNAVAILABLE` to the v0.4 public error catalog and
  documented its `security`, fail-closed, command-not-started semantics.

## Verification

```text
uv run python -m unittest tests.extensions.test_config_startup tests.test_host_config tests.test_credential_providers
Ran 37 tests ... OK

uv run python -m unittest tests.compliance.test_schema_drift
Ran 10 tests ... OK
```
