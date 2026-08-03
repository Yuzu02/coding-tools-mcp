# Reliable Command Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `exec_command` safely retryable after a lost MCP response and let fresh MCP sessions discover and recover workspace-owned commands.

**Architecture:** Extend the existing workspace-scoped `WorkspaceCommandManager`; do not introduce transport-session state or persistence outside the server process. An optional `client_request_id` reserves one execution fingerprint and maps it to exactly one `command_id`. Read-only `list_commands` and `get_command` expose bounded metadata and retained output handles without exposing raw command text, environment values, or secrets.

**Tech Stack:** Python 3.11+, standard library threading/hashlib/json, existing MCP schemas and unittest/compliance harness.

## Global Constraints

- Base branch is `contrib/windows-runtime-portability` at `12522fa`.
- Work remains in the user's fork; do not open or update an upstream PR.
- Preserve the existing sessionless `WorkspaceCommandManager`, PowerShell 7 support, and Windows portability changes.
- `client_request_id` is optional and backwards compatible.
- Never persist or return raw environment values, stdin, or command text from discovery tools.
- `list_commands` and `get_command` are read-only and idempotent.
- Retry deduplication must be atomic across concurrent HTTP runtimes sharing the same command manager.

---

### Task 1: Pin the command recovery contract

**Files:**
- Modify: `tests/compliance/test_runtime_helpers.py`
- Modify: `tests/compliance/test_mcp_contract.py`
- Modify: `tests/compliance/mcp_client.py`

**Interfaces:**
- Consumes: existing `Runtime.exec_command`, shared HTTP command manager.
- Produces: executable tests for `client_request_id`, `list_commands`, and `get_command`.

- [ ] Add a failing unit test proving two equivalent `exec_command` calls with the same `client_request_id` return the same `command_id`.
- [ ] Add a failing unit test proving the same identifier with a different execution fingerprint returns `IDEMPOTENCY_CONFLICT`.
- [ ] Add failing tests proving `get_command` is non-consuming and `list_commands` omits raw command/environment data.
- [ ] Add a failing HTTP compliance test proving two fresh MCP sessions deduplicate one command.
- [ ] Run the focused tests and confirm they fail because the new schema/tools are absent.

### Task 2: Add workspace-owned idempotency records

**Files:**
- Modify: `coding_tools_mcp/processes.py`
- Modify: `coding_tools_mcp/server.py`

**Interfaces:**
- Produces: `ClientRequestBinding`, workspace-level reservation/lookup/removal helpers, and command metadata.
- `exec_command(args)` accepts optional `client_request_id: str`.

- [ ] Add an optional non-secret `client_request_id` field to `CommandRun`.
- [ ] Add workspace-owned pending/completed bindings keyed by `client_request_id`.
- [ ] Compute a SHA-256 fingerprint from execution-affecting inputs without retaining their plaintext.
- [ ] Atomically reserve the identifier before process spawn.
- [ ] Return the existing command for an equivalent duplicate.
- [ ] Reject conflicting reuse and release failed-start reservations.
- [ ] Remove bindings when retained command records expire or are evicted.
- [ ] Run focused unit tests.

### Task 3: Expose bounded recovery tools

**Files:**
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/tool_results.py`

**Interfaces:**
- Produces: `list_commands(args)` and `get_command(args)`.
- `get_command` accepts exactly one of `command_id` or `client_request_id`.

- [ ] Add read-only/idempotent tool registrations and schemas.
- [ ] Implement non-consuming retained snapshots with output references.
- [ ] Implement bounded newest-first command listing with status filters.
- [ ] Add concise model-text rendering.
- [ ] Run focused unit and schema tests.

### Task 4: Document and verify the fork-only experiment

**Files:**
- Modify: `README.md`
- Modify: `SPEC.md`
- Modify: `docs/tools-and-schemas.md`
- Modify: `docs/runtime-contract-v0.2.md`
- Modify: `docs/troubleshooting-exec.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Documents the optional recovery workflow and conflict semantics.

- [ ] Document generating one stable non-secret `client_request_id` per intended execution.
- [ ] Document recovery using `get_command(client_request_id=...)` before repeating uncertain mutations.
- [ ] Run lint, typecheck, unit, MCP contract, tool golden, security, schema drift, and `git diff --check`.
- [ ] Keep the branch local or push only to remote `fork`; do not create a PR.
