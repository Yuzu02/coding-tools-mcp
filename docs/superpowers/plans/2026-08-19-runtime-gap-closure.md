# Runtime Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every currently reproduced runtime-contract, Landlock/Mise, transport, telemetry, and schema-test gap until the literal `mise run verify` gate is green on the current `main` worktree.

**Architecture:** Preserve the existing execution architecture: one canonical `ChildOperationTarget` flows through command policy, provider selection, environment construction, and Landlock; Mise remains layered system/user/project; credential isolation stays fail-closed. Treat stale tests/docs as contract drift rather than restoring deliberately removed aliases, and change production only when a focused failing test proves a real runtime defect.

**Tech Stack:** Python 3.13/3.14, unittest, Ruff, Mise, uv, Linux Landlock, MCP 2026/legacy protocol compatibility, npm launcher gate.

**Spec:** `docs/superpowers/specs/2026-08-18-pre-worktree-runtime-modernization-design.md`

## Global Constraints

- Work on the explicitly authorized current `main` worktree; do not create a worktree during the pre-worktree modernization gate.
- Preserve unrelated in-progress source changes; patch only reproduced gaps.
- Do not weaken credential-broker file isolation or grant the personal home root.
- Mise must remain cumulative: system-wide `/etc/mise`, user `MISE_CONFIG_DIR`/`MISE_DATA_DIR`, and project `mise.toml`.
- `git_status.path` is not a workdir alias in the V1 contract; `workdir` is canonical.
- Run the literal `mise run verify` gate before completion.

---

### Task 1: Landlock test/runtime contract alignment

**Files:**
- Modify: `tests/test_credential_landlock.py`
- Production only if required by a focused RED: `coding_tools_mcp/server.py`, `coding_tools_mcp/child_operation.py`

**Interfaces:**
- Consumes: `child_operation_target(root, workdir)`, `Runtime._command_env(...)`, `Runtime._credential_landlock_roots(command, target, environment=..., provider=...)`.
- Produces: deterministic Landlock tests that do not place a synthetic credential broker beneath `/tmp` or `/var/tmp` while testing `global_tmp_write=allowed`.

- [ ] Re-run the failing credential-Landlock test module and record the exact current failures.
- [ ] Add a deterministic per-test temp base outside `/tmp` and `/var/tmp` without exposing or modifying credential stores.
- [ ] Update direct private-helper tests to construct the same canonical child target/environment/provider tuple used by `exec_command`.
- [ ] Run `tests.test_credential_landlock` and require all tests to pass.

### Task 2: Public contract and documentation drift

**Files:**
- Modify: `tests/extensions/test_project_addressing_integration.py`
- Modify: `tests/extensions/test_project_addressing_tools.py`
- Modify: `tests/extensions/test_upstream_compatibility.py`
- Modify: `tests/test_git_workdir_resolution.py`
- Modify: `tests/test_telemetry.py`
- Modify: `docs/telemetry.md`

**Interfaces:**
- Consumes: projects-extension tools `list_projects`, `resolve_project`, `list_skills`, `read_skill`, `project_context`, `doctor`; canonical Git `workdir`; telemetry `tool_operation`.
- Produces: drift tests matching the current documented V1 contract without reviving removed aliases.

- [ ] Update the expected projects-extension catalog delta from four to six tools and assert the two new names explicitly.
- [ ] Replace the obsolete `git_status.path` alias test with a canonical-workdir/schema assertion matching the modernization spec.
- [ ] Document `tool_operation` and duration buckets, then update the telemetry drift test to require that event.
- [ ] Run the affected addressing/Git/telemetry tests and require green.

### Task 3: Modern transport cancellation test doubles

**Files:**
- Modify: `tests/test_transport_http.py`
- Modify: `tests/test_transport_stdio.py`

**Interfaces:**
- Consumes: modern protocol `_call_tool` contract including `input_responses` and `request_state` keyword arguments.
- Produces: test runtimes that implement the same call signature as `Runtime.call_tool`, allowing cancellation behavior itself to be tested.

- [ ] Keep the current failing HTTP/stdio cancellation tests as RED evidence.
- [ ] Extend only the test doubles' `call_tool` signatures with the modern continuation inputs.
- [ ] Re-run both cancellation tests and require cancellation to be observed and responses suppressed as specified.

### Task 4: Full gate and residual-gap loop

**Files:**
- Modify only files implicated by newly reproduced failures.

**Interfaces:**
- Consumes: all changes from Tasks 1-3.
- Produces: green repository gate and a clean diagnosis of any remaining non-Mise issue.

- [ ] Run Ruff plus focused regression suites.
- [ ] Run `git diff --check`.
- [ ] Run literal `mise run verify` with no wrapper.
- [ ] If a new failure remains, reproduce it alone, identify root cause, add/retain a focused RED test, make the minimum fix, and repeat the gate.
- [ ] Verify system/user/project Mise resolution in both `coding-tools` and `sicotilab` project contexts.
- [ ] Review the final diff for accidental credential, home-root, or unrelated-worktree changes before any commit/push decision.
