# Pre-Worktree Runtime Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PW0-PW6 GREEN so the fork reaches Worktree implementation with upstream ancestry restored, exact MCP 2026-07-28 behavior, one canonical execution-target model, compact diagnostic/context primitives, expanded read-only semantic IDE support, and shared policy/observability seams.

**Architecture:** Preserve `Runtime` as the process-wide protocol/auth host but move new reusable concepts into focused modules rather than expanding `server.py` indefinitely. `ExecutionTarget` becomes the single project/workdir resolution primitive; `OperationContext` owns request identity/deadline/cancellation; the `projects` extension adds bounded `project_context`/`doctor`; the `semantic` extension adds implementations/diagnostics; protocol changes stay in `protocol.py` plus transport adapters. All fork behavior continues through the ExtensionHost/generic core bridge so future `xyTom/main` syncs remain reviewable.

**Tech Stack:** Python 3.13 runtime via `UV_PYTHON=3.13.12`, stdlib threading/async-safe primitives, JSON-RPC/MCP 2026-07-28 + legacy 2025-11-25/2025-06-18 compatibility, existing ExtensionHost/HostConfig v2/ProjectConfig v1, Serena 1.5.3, Git, Mise/uv, `unittest`, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-18-pre-worktree-runtime-modernization-design.md`

## Global Constraints

- Work only in `project_id=coding-tools`; every connector call that accepts `project_id` must pass it explicitly.
- Implement on `main`; do not create a Git worktree before the Worktree phase itself.
- Do not push, tag, release, force-push, or create a PR unless explicitly requested after PW6.
- Preserve the existing mother-core/ExtensionHost boundary. Mother core must not import private `projects`, `semantic`, future `work`, hooks, or gateway implementations.
- Keep HostConfig v2 / ProjectConfig v1 as configuration authority. Do not create a second configuration plane for these features.
- Preserve stateless client semantics: no current-project, current-worktree, cwd, or cancellation state may depend on transport session history.
- `project_id` alone selects the registered project root; omitted `workdir` means `.` relative to that root.
- `workdir` never selects another project and never escapes the selected execution root through traversal or symlinks.
- Opaque handles (`command_id`, `output_ref`) carry ownership internally; do not add redundant model-facing scope parameters to handle operations.
- Modern and legacy protocol behavior remain explicit and separately tested. Do not silently backport legacy methods into MCP 2026-07-28.
- `apply_patch` remains the only direct text-edit primitive. Do not expose Serena editing/refactoring in PW0-PW6.
- Every behavioral change follows red -> minimal green -> focused regression -> broader gate.
- Preserve public-fork hygiene: no real host paths, service units, tunnel IDs, credentials, private provider contents, or local deployment state in tracked files.
- Before staging any commit, re-run `git status --short --branch`; stage only the task's files.
- Existing dirty credential/Landlock work is not silently absorbed into PW commits. Reconcile it as Task 0 first.

---

## File / Responsibility Map

```text
coding_tools_mcp/protocol.py
    protocol-era method matrix, modern metadata, MRTR wire shapes, result caching metadata

coding_tools_mcp/transport_stdio.py
    concurrent/in-flight stdio request ownership and cancellation notification delivery

coding_tools_mcp/server.py
    Runtime composition, Streamable HTTP request lifecycle, core tool definitions,
    command lifecycle, server_info summary/detail selection

coding_tools_mcp/execution_target.py                 [new]
    canonical (project_id, workdir) logical target model and reusable target metadata

coding_tools_mcp/operation_context.py                [new]
    operation id, request id, deadline, cancellation token, bounded operation metadata

coding_tools_mcp/tool_results.py
    shared recovery rendering and continuation shaping

coding_tools_mcp/extensions/contributions.py
    optional precise per-tool output schema metadata

coding_tools_mcp/extensions/services.py
    generic execution-target / operation-context service protocols when extension access is needed

coding_tools_mcp/extensions/projects/runtime.py
    project runtime accessors consumed by ExecutionTarget/project context

coding_tools_mcp/extensions/projects/extension.py
    normalized routing decorators, project_context and doctor tools

coding_tools_mcp/extensions/semantic/model.py
    implementation/diagnostic backend-neutral request/result types

coding_tools_mcp/extensions/semantic/backend.py
    new read-only SemanticBackend methods

coding_tools_mcp/extensions/semantic/protocol.py
    worker operation names/validation

coding_tools_mcp/extensions/semantic/serena_worker.py
    Serena/LSP adapter calls for implementations and file diagnostics

coding_tools_mcp/extensions/semantic/serena.py
    worker request/result conversion and backend API

coding_tools_mcp/extensions/semantic/extension.py
    public find_implementations/get_diagnostics contributions

coding_tools_mcp/telemetry.py
    bounded operation-level non-secret correlation fields

tests/compliance/test_dual_era.py
tests/compliance/test_mcp_contract.py
    modern/legacy protocol, cancellation, MRTR, cache semantics

tests/extensions/test_upstream_compatibility.py
    mother-core bridge and sync guard

tests/extensions/test_project_tool_routing.py
tests/extensions/test_project_server_context.py
    ExecutionTarget, compact context, schema normalization

tests/extensions/test_semantic_model.py
tests/extensions/test_semantic_extension.py
tests/extensions/test_semantic_worker_protocol.py
tests/extensions/test_semantic_serena_backend.py
tests/extensions/test_semantic_serena_integration.py
    semantic capability expansion

tests/test_telemetry.py
tests/compliance/test_schema_drift.py
tests/compliance/test_tool_golden.py
    observability/catalog/schema budgets

docs/runtime-contract-v0.4.md
docs/tools-and-schemas.md
docs/extensions.md
docs/telemetry.md
    current public contract after each accepted behavior change
```

---

### Task 0: Reconcile the Existing Credential/Landlock WIP Before PW Changes

**Files:**
- Existing dirty: `coding_tools_mcp/server.py`
- Existing dirty: `docs/services-launcher.md`
- Existing dirty: `tests/compliance/test_runtime_helpers.py`
- Existing dirty: `tests/test_credential_landlock.py`
- Reference: `docs/superpowers/plans/2026-08-18-credential-provider-registry.md`

**Interfaces:**
- Consumes: current credential-provider registry, Landlock root builder, Mise child environment.
- Produces: either a separately verified clean credential checkpoint, or a documented blocker before any PW task touches the overlapping files.

- [ ] **Step 1: Review the exact dirty diff and classify every hunk against the credential-provider plan**

Run:

```bash
git status --short --branch
git diff -- coding_tools_mcp/server.py docs/services-launcher.md tests/compliance/test_runtime_helpers.py tests/test_credential_landlock.py
```

Expected: only credential/Landlock/Mise child-runtime changes. If unrelated PW code appears, stop and separate it before continuing.

- [ ] **Step 2: Run the focused credential/Landlock tests before changing the WIP**

Run:

```bash
uv run --locked python -m unittest tests.test_credential_landlock tests.compliance.test_runtime_helpers -v
```

Expected: either GREEN, or a reproducible failure whose root cause is resolved under `superpowers:systematic-debugging` before staging.

- [ ] **Step 3: Run the credential-provider gate set**

Run:

```bash
uv run --locked python -m unittest tests.test_credential_providers tests.test_credential_admin tests.test_host_config -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp tests scripts
git diff --check
```

Expected: all exit zero. Do not weaken broker isolation merely to make toolchains work.

- [ ] **Step 4: Commit only the pre-existing WIP if and only if all focused gates are GREEN**

```bash
git add coding_tools_mcp/server.py docs/services-launcher.md tests/compliance/test_runtime_helpers.py tests/test_credential_landlock.py
git diff --cached --check
git commit -m "fix: complete credential command sandbox compatibility"
```

Expected: clean working tree except this plan/spec work already committed. If the WIP cannot be made independently correct, leave it unstaged and stop before Task 1 because later tasks overlap `server.py`.

---

### Task 1: PW0 — Restore Current Upstream Ancestry and Harden the Sync Guard

**Files:**
- Modify: `tests/extensions/test_upstream_compatibility.py`
- Modify: `docs/extensions.md`
- Modify: `docs/superpowers/specs/2026-08-18-pre-worktree-runtime-modernization-design.md` only for final evidence/status notes
- Git refs/history: `xyTom/main`, `sync/upstream-main`, fork `main`

**Interfaces:**
- Consumes: existing ExtensionHost bridge tests and `sync/upstream-main` lane.
- Produces: `git merge-base --is-ancestor xyTom/main HEAD == 0` plus focused tests that keep the bridge localized.

- [ ] **Step 1: Add/strengthen the portable bridge regression before touching history**

Add an assertion that fork-private feature packages remain absent from mother-core imports and that composed tools are still sourced through `core_tool_contracts` + ExtensionHost rather than a second registry. Keep the test independent of a local `xyTom` ref so CI remains portable.

```python
def test_mother_core_bridge_remains_feature_neutral(self) -> None:
    for private in ("extensions.projects", "extensions.semantic", "extensions.work", "extensions.gateway"):
        self.assertNotIn(private, SERVER)
    self.assertIn("core_tool_contracts", SERVER)
```

- [ ] **Step 2: Run the bridge test RED/GREEN against the current source**

Run: `uv run --locked python -m unittest tests.extensions.test_upstream_compatibility -v`

Expected: GREEN unless the current tree already violates the intended seam. If RED, fix only the bridge regression before Git history integration.

- [ ] **Step 3: Freshly fetch and diagnose the remote graph**

Run:

```bash
git fetch --prune xyTom main
git update-ref refs/heads/sync/upstream-main refs/remotes/xyTom/main
git rev-parse xyTom/main sync/upstream-main HEAD
git rev-list --left-right --count xyTom/main...HEAD
git merge-base xyTom/main HEAD
git merge-base --is-ancestor xyTom/main HEAD
```

Expected before repair: ancestry may fail; the fetched tip and sync branch must be identical.

- [ ] **Step 4: Reconnect equivalent/reparented upstream history with an explicit integration merge**

If the freshly fetched upstream tip is **not** content-equivalent to an
already-integrated fork ancestor, use a normal merge commit, preserving fork
history instead of rebasing hundreds of fork commits:

```bash
git merge --no-ff --no-edit xyTom/main
```

If Git reports content conflicts, resolve each against the existing fork architecture; never discard fork functionality just to prefer upstream text. If the tips are tree-equivalent at the relevant boundary, the merge should primarily reconnect ancestry.

If a previous integrated upstream commit is already an ancestor of `HEAD` and
its complete tree is byte-identical to the freshly fetched upstream tip, a
normal three-way merge may manufacture conflicts solely because the upstream
parents were rewritten. In that proven case, preserve the current fork tree
and connect genealogy explicitly:

```bash
git merge-base --is-ancestor <prior-integrated-upstream-tip> HEAD
git diff --quiet <prior-integrated-upstream-tip> xyTom/main
git merge -s ours --no-ff --no-edit xyTom/main
```

The `ours` strategy is valid only when the first two checks succeed; it is not
a shortcut for ignoring real upstream content.

- [ ] **Step 5: Prove ancestry and bridge behavior**

Run:

```bash
git merge-base --is-ancestor xyTom/main HEAD
uv run --locked python -m unittest tests.extensions.test_upstream_compatibility -v
git diff --check HEAD^..HEAD
```

Expected: ancestry exit zero and bridge tests GREEN.

- [ ] **Step 6: Commit any bridge/docs changes separately from the merge when needed**

Commit message: `test: harden upstream synchronization bridge guard`.

---

### Task 2: PW1A — Exact MCP 2026 Method Matrix and Real Request Cancellation

**Files:**
- Create: `coding_tools_mcp/operation_context.py`
- Modify: `coding_tools_mcp/protocol.py`
- Modify: `coding_tools_mcp/transport_stdio.py`
- Modify: `coding_tools_mcp/server.py`
- Test: `tests/compliance/test_dual_era.py`
- Test: `tests/compliance/test_mcp_contract.py`

**Interfaces:**
- Produces: `CancellationToken`, `OperationContext`, request-scoped cancellation registry, modern method matrix without `ping`/client POST cancellation.
- Preserves: published `command_id` lifecycle remains owned by command tools, not request cancellation.

- [ ] **Step 1: Write protocol tests for removed modern methods**

```python
def test_modern_ping_is_method_not_found(self) -> None:
    response = self.stdio_rpc_allow_error(process, modern_request(1, "ping"))
    self.assertEqual(response["error"]["code"], -32601)

def test_legacy_ping_remains_available(self) -> None:
    response = self.stdio_rpc(process, legacy_request(1, "ping"))
    self.assertEqual(response["result"], {})
```

Also change modern HTTP cancellation-notification expectations so a modern `notifications/cancelled` POST is not treated as a supported core method.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run --locked python -m unittest tests.compliance.test_dual_era tests.compliance.test_mcp_contract -v`

Expected: modern `ping`/modern cancellation notification tests fail against the old method table.

- [ ] **Step 3: Add the operation/cancellation primitives**

`coding_tools_mcp/operation_context.py`:

```python
@dataclass(frozen=True)
class CancellationToken:
    _event: threading.Event

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled()

@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    request_id: str | int | None
    cancellation: CancellationToken
    deadline_monotonic: float | None = None
```

Keep the mutable `threading.Event` private to a request registry. The public/frozen context only references it.

- [ ] **Step 4: Make modern/legacy dispatch explicit**

Remove `ping` and `notifications/cancelled` from `MODERN_METHODS`; retain both only in legacy dispatch where contracted. Pass an optional `OperationContext` into `Runtime.call_tool` without storing it as global/session state.

- [ ] **Step 5: Add stdio in-flight request cancellation**

`serve_stdio` must be able to receive a cancellation notification while a request is executing. Use a bounded worker executor/request registry rather than blocking the input loop on one call. The cancellation notification looks up `params.requestId`, sets the matching event, and writes no response.

Add a test tool/fixture that waits cooperatively on `OperationContext.cancellation`; assert the request returns the typed cancellation error and a following request still succeeds.

- [ ] **Step 6: Add Streamable HTTP disconnect cancellation**

Bind one cancellation token to each HTTP request. If response writing raises a client disconnect/broken pipe before request-owned work completes, cancel that operation. Do not kill already-published command processes.

- [ ] **Step 7: Run protocol tests GREEN and commit**

Run:

```bash
uv run --locked python -m unittest tests.compliance.test_dual_era tests.compliance.test_mcp_contract -v
git diff --check
```

Commit: `feat: align modern request lifecycle with mcp 2026`.

---

### Task 3: PW1B — MRTR Foundation, Deterministic Catalog Caching, and Truthful Output Schemas

**Files:**
- Modify: `coding_tools_mcp/protocol.py`
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/extensions/contributions.py`
- Test: `tests/compliance/test_mcp_contract.py`
- Test: `tests/extensions/test_tool_contributions.py`
- Test: `tests/compliance/test_schema_drift.py`

**Interfaces:**
- Produces: typed `input_required` result/state helpers; catalog generation identity; optional per-tool `output_schema`.

- [ ] **Step 1: Write failing tests for MRTR validation**

Cover: client without elicitation/input capability, bounded `inputRequests`, opaque `requestState`, invalid/tampered state, too many rounds, response schema mismatch, decline/cancel.

Use a test-only tool contribution whose handler returns an internal `InputRequired` value; do not add a production feature dependency on MRTR yet.

- [ ] **Step 2: Write failing tests for selective output schemas**

```python
def test_tool_without_precise_output_schema_omits_output_schema(self) -> None:
    listed = runtime.list_tools()["tools"]
    self.assertNotIn("outputSchema", by_name["read_file"])
```

Then give one synthetic extension tool a precise output schema and assert it is emitted and structuredContent is validated against it.

- [ ] **Step 3: Implement optional output schema on ToolContribution/ComposedTool**

Add `output_schema: Mapping[str, Any] | None = None`; validate it at composition. `tool_definition()` includes `outputSchema` only when non-None. Remove the generic universal `tool_output_schema()` advertisement; keep internal tool error/result envelope validation separately.

- [ ] **Step 4: Implement MRTR helper/state validation in protocol layer**

Keep state bounded and tamper-evident. Use a server secret generated at process start to HMAC a compact JSON state containing operation/tool/round/payload fingerprint; never place credentials or arbitrary arguments in `requestState`.

- [ ] **Step 5: Add deterministic private tools/list caching metadata**

Calculate one immutable catalog fingerprint from ordered tool definitions plus configuration/tool visibility generation. `tools/list` may return a small positive private TTL only while this generation is unchanged. `server/discover` stays TTL 0 in this phase.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run --locked python -m unittest tests.compliance.test_mcp_contract tests.extensions.test_tool_contributions tests.compliance.test_schema_drift -v
```

Commit: `feat: add modern mcp round-trip and catalog primitives`.

---

### Task 4: PW2 — Canonical ExecutionTarget and Public Schema Normalization

**Files:**
- Create: `coding_tools_mcp/execution_target.py`
- Modify: `coding_tools_mcp/extensions/services.py`
- Modify: `coding_tools_mcp/extensions/projects/runtime.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/tool_results.py`
- Test: `tests/extensions/test_project_tool_routing.py`
- Test: `tests/extensions/test_project_workspace_boundaries.py`
- Test: `tests/compliance/test_schema_drift.py`

**Interfaces:**
- Produces: `ExecutionTarget(project_id, root, workdir, relative_workdir)` and one resolver path reused by project decorators/core execution.

- [ ] **Step 1: Write failing routing/schema tests**

```python
def test_project_id_without_workdir_targets_project_root(self) -> None:
    result = runtime.call_tool("exec_command", {"project_id": "alpha", "cmd": pwd_command})
    self.assertEqual(Path(payload(result)["workdir"]), Path("."))

def test_exec_command_schema_has_no_cwd_alias(self) -> None:
    schema = by_name["exec_command"]["inputSchema"]
    self.assertNotIn("cwd", schema["properties"])

def test_git_status_path_is_not_a_workdir_alias(self) -> None:
    schema = by_name["git_status"]["inputSchema"]
    self.assertNotIn("path", schema["properties"])
```

Add boundary tests proving `workdir="../beta"` and symlink escape reject without retargeting another registered project.

- [ ] **Step 2: Implement ExecutionTarget**

```python
@dataclass(frozen=True)
class ExecutionTarget:
    project_id: str
    root: Path
    workdir: Path
    relative_workdir: str
```

The projects runtime resolves `project_id`; `workdir or "."` is canonicalized beneath that root. Future `worktree_id` will insert between project and workdir without changing the type's outward semantics.

- [ ] **Step 3: Route core project-scoped tools through the target resolver**

Decorators should remove only routing-only fields after resolving. Core handlers consume an already-selected workspace runtime and relative `workdir`; they must not infer project from paths.

- [ ] **Step 4: Normalize public schemas**

- `exec_command`: keep only `workdir`, remove `cwd` from advertised/accepted modern schema.
- Git: `workdir` means execution directory; `path`/`paths` are pathspec filters only. `git_status` needs no path filter, so drop its `path` alias.
- Continuation builders preserve `project_id` and only non-default logical routing fields; no absolute host workdir.

- [ ] **Step 5: Run routing/security/schema gates and commit**

Run:

```bash
uv run --locked python -m unittest tests.extensions.test_project_tool_routing tests.extensions.test_project_workspace_boundaries tests.compliance.test_schema_drift -v
```

Commit: `refactor: centralize project execution targets`.

---

### Task 5: PW3A — Compact server_info, project_context, and doctor

**Files:**
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/extensions/projects/runtime.py`
- Modify: `coding_tools_mcp/tool_results.py`
- Test: `tests/extensions/test_project_server_context.py`
- Test: `tests/extensions/test_projects_extension.py`
- Test: `tests/compliance/test_schema_drift.py`
- Modify: `docs/runtime-contract-v0.4.md`
- Modify: `docs/tools-and-schemas.md`

**Interfaces:**
- Produces: `server_info(detail="summary"|"full")`, `project_context(project_id, detail="summary"|"full")`, `doctor(project_id?, detail="summary"|"full")`.

- [ ] **Step 1: Write byte-budget and redaction tests**

Summary contracts must exclude full credential paths, state/cache roots, tool-name arrays, arbitrary instruction contents, and command output. Set explicit serialized budgets in tests rather than subjective checks:

```python
self.assertLessEqual(len(json.dumps(payload, separators=(",", ":")).encode()), 4096)
```

Use 4 KiB for `server_info(summary)`, 8 KiB for `project_context(summary)`, and 12 KiB for `doctor(summary)` unless a focused test proves a smaller/larger bounded value is necessary.

- [ ] **Step 2: Make server_info summary-first**

Summary includes server/version/runtime contract, configuration fingerprint/warning count, enabled extension names, tool count/fingerprint, project count, permission/auth/confinement health, and credential-registry health. `detail="full"` retains current operator diagnostics but remains bounded.

- [ ] **Step 3: Add project_context to the projects extension**

Summary fields:

```text
project_id
available
git: branch, short_head, clean, ahead, behind
semantic: available, backend, backend_version
instructions: warning_count, skill_count (bounded/cheap)
execution: permission_mode, credential_provider_names applicable only as names
warnings[]
```

Do not inline AGENTS/CLAUDE content, file inventories, full Git log, command history, or absolute roots.

- [ ] **Step 4: Add doctor with deterministic read-only checks**

Each check returns `id`, `status=pass|warn|fail`, `summary`, optional bounded `details`, optional structured `recovery`. Server checks cover config/extensions/state/credential registry/confinement/catalog. Project checks cover root/Git/child environment/semantic/instruction warnings. Network checks are opt-in and absent from summary mode.

- [ ] **Step 5: Update live tool inventory/docs and run gates**

Because two tools are added, update `EXPECTED_STATELESS_TOOL_NAMES`, `docs/runtime-contract-v0.4.md`, `docs/tools-and-schemas.md`, README/SPEC tool counts if those docs intentionally track the default catalog.

Run:

```bash
uv run --locked python -m unittest tests.extensions.test_project_server_context tests.extensions.test_projects_extension tests.compliance.test_schema_drift tests.compliance.test_docs_required -v
```

Commit: `feat: add compact project context and diagnostics`.

---

### Task 6: PW3B — Add find_implementations and get_diagnostics Through the Semantic Adapter

**Files:**
- Modify: `coding_tools_mcp/extensions/semantic/model.py`
- Modify: `coding_tools_mcp/extensions/semantic/backend.py`
- Modify: `coding_tools_mcp/extensions/semantic/protocol.py`
- Modify: `coding_tools_mcp/extensions/semantic/serena_worker.py`
- Modify: `coding_tools_mcp/extensions/semantic/serena.py`
- Modify: `coding_tools_mcp/extensions/semantic/extension.py`
- Modify: `coding_tools_mcp/extensions/semantic/__init__.py`
- Test: semantic test files listed in the File Map
- Modify: current semantic/tool contract docs

**Interfaces:**
- Produces: `FindImplementationsRequest/Result`, `GetDiagnosticsRequest/Result`, `SemanticDiagnostic` and corresponding backend methods.

- [ ] **Step 1: Write backend-neutral model tests RED**

```python
@dataclass(frozen=True)
class SemanticDiagnostic:
    path: str
    range: SemanticRange
    severity: str
    message: str
    code: str | None = None
    source: str | None = None
```

`GetDiagnosticsRequest` is file-oriented with optional line range, severity threshold, and bounded `max_results`. `FindImplementationsRequest` uses one-based path/line/column and bounded result count.

- [ ] **Step 2: Extend FakeBackend/worker protocol tests**

Add exact operation names `find_implementations` and `get_diagnostics`; unknown ops still fail closed. Verify messages remain under worker byte bounds and diagnostics truncate deterministically.

- [ ] **Step 3: Implement Serena worker calls using Serena 1.5.3 APIs**

Call the pinned backend APIs without exposing Serena-native response shapes publicly. Normalize ranges to one-based Coding Tools positions, severity to a closed enum, message/code/source to bounded strings, and paths to project-relative paths.

- [ ] **Step 4: Register public tools in SemanticExtension**

Both tools require `project_id`, are read-only/idempotent, and use the same project capability ceiling and backend failure isolation as existing semantic tools.

- [ ] **Step 5: Run semantic unit + real integration gates**

Run:

```bash
uv run --locked python -m unittest tests.extensions.test_semantic_model tests.extensions.test_semantic_extension tests.extensions.test_semantic_worker_protocol tests.extensions.test_semantic_serena_backend tests.extensions.test_semantic_serena_integration tests.extensions.test_semantic_mcp_integration -v
```

Commit: `feat: add semantic implementations and diagnostics`.

---

### Task 7: PW4 — Operation Context, Recovery, Idempotency Vocabulary, and Continuation Helpers

**Files:**
- Modify: `coding_tools_mcp/operation_context.py`
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/tool_results.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Test: `tests/compliance/test_runtime_helpers.py`
- Test: `tests/extensions/test_project_command_routing.py`
- Test: `tests/test_reliable_command_recovery_http.py`

**Interfaces:**
- Produces: one operation id per tool call, shared structured recovery helper, logical continuation shaping, common future `request_id` validator.

- [ ] **Step 1: Write tests proving operation identity is server-owned and bounded**

No normal call requires the model to submit `operation_id`. Result `_meta` may carry it only in modern responses; model-facing text must not repeat it.

- [ ] **Step 2: Centralize recovery hints**

Replace ad-hoc `retry_hint` strings where the server knows the next structured step with:

```python
def recovery_call_tool(tool: str, arguments: Mapping[str, object], reason: str) -> dict[str, object]:
    return {"kind": "call_tool", "tool": tool, "arguments": dict(arguments), "reason": reason}
```

Keep human-readable rendering in `tool_results.py`; never authorize the suggested mutation automatically.

- [ ] **Step 3: Centralize continuation shaping**

Continuation arguments use project/logical relative state. Remove redundant `workdir="."` and any absolute host path that can be reconstructed. Existing `read_output(output_ref)` remains handle-only.

- [ ] **Step 4: Establish `request_id` helper for new mutating APIs**

Do not rename `exec_command.client_request_id` in this task. Add one reusable validator/fingerprint helper so Worktrunk/Work Items later use `request_id` consistently rather than inventing another key.

- [ ] **Step 5: Run command recovery tests and commit**

Run:

```bash
uv run --locked python -m unittest tests.extensions.test_project_command_routing tests.test_reliable_command_recovery_http tests.compliance.test_runtime_helpers -v
```

Commit: `refactor: unify operation recovery and continuation state`.

---

### Task 8: PW5 — Converge Policy/Confinement and Add Bounded Operation Observability

**Files:**
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/telemetry.py`
- Modify: `coding_tools_mcp/extensions/services.py` if extensions need generic target/context capabilities
- Modify: `coding_tools_mcp/extensions/semantic/serena.py`
- Test: `tests/test_telemetry.py`
- Test: `tests/test_credential_landlock.py`
- Test: `tests/compliance/test_security.py`
- Modify: `docs/telemetry.md`
- Modify: `docs/services-launcher.md`

**Interfaces:**
- Consumes: ExecutionTarget + OperationContext.
- Produces: one shared child-operation target/environment/confinement path usable by semantic/Worktrunk/Hooks/Gateway later; non-secret operation telemetry.

- [ ] **Step 1: Write security tests proving credential selection cannot be changed by path tricks**

For a registered project, explicit `workdir` beneath the project must not switch credential provider applicability. Traversal/symlink escape rejects before environment/provider selection.

- [ ] **Step 2: Make child environment/confinement consume the resolved target**

Refactor helpers to accept canonical target/root paths rather than raw untrusted `workdir` strings. Preserve the independently verified credential/Landlock behavior from Task 0.

- [ ] **Step 3: Add bounded operation telemetry fields**

Allowed: sanitized tool name, project id, future worktree id, duration bucket, status/error code, input/output size class, backend/provider name. Forbidden: absolute paths, command strings, tool arguments, file contents, secret env, tokens.

- [ ] **Step 4: Add doctor coverage for policy/provider health**

Doctor summaries surface health states, not secret paths. Full detail may identify a generic failing component but still obey public/runtime redaction rules.

- [x] **Step 5: Run policy/telemetry gates and commit**

Run:

```bash
uv run --locked python -m unittest tests.test_telemetry tests.test_credential_landlock tests.compliance.test_security -v
```

Commit: `refactor: converge operation policy and observability`.

---

### Task 9: PW6 — Contract Reconciliation, Full Verification, Live Connector Acceptance

**Files:**
- Modify as required: `docs/runtime-contract-v0.4.md`, `docs/tools-and-schemas.md`, `README.md`, `README.zh-CN.md`, `SPEC.md`, `CHANGELOG.md` only where current catalog/runtime facts changed
- Modify: `docs/superpowers/specs/2026-08-18-pre-worktree-runtime-modernization-design.md` status/evidence
- Modify: `docs/superpowers/specs/2026-08-18-work-items-worktree-coordination-design.md` prerequisite status only after all gates are proven
- Test: all repository tests

**Interfaces:**
- Produces: one fresh evidence set proving PW0-PW6 GREEN and permitting WT0 to begin.

- [x] **Step 1: Reconcile current contract/tool-count docs from the live composed catalog**

Do not edit historical v0.3 snapshots. Update current docs only. Ensure all new error codes, input properties, annotations, and tool names are represented.

- [x] **Step 2: Run focused architectural gates**

```bash
uv run --locked python -m unittest tests.extensions.test_upstream_compatibility -v
uv run --locked python -m unittest tests.compliance.test_dual_era tests.compliance.test_mcp_contract tests.compliance.test_schema_drift -v
uv run --locked python -m unittest tests.extensions.test_project_tool_routing tests.extensions.test_project_server_context -v
uv run --locked python -m unittest tests.extensions.test_semantic_mcp_integration tests.extensions.test_semantic_serena_integration -v
```

- [x] **Step 3: Run static/full repository gates**

```bash
uv run --locked --extra dev python -m ruff check scripts tests coding_tools_mcp
uv run --locked --extra dev python -m mypy coding_tools_mcp
mise run verify
git diff --check
```

Expected: zero failures. If `mise run verify` exposes an environmental/deployment problem, investigate it rather than weakening tests.

- [x] **Step 4: Prove upstream topology freshly**

```bash
git fetch --prune xyTom main
git update-ref refs/heads/sync/upstream-main refs/remotes/xyTom/main
git merge-base --is-ancestor xyTom/main HEAD
git rev-list --left-right --count xyTom/main...HEAD
```

Expected: upstream ancestry exit zero and left-side count zero.

- [x] **Step 5: Exercise the deployed connector after code deployment/restart**

Using the canonical connector, verify:

```text
server_info()                       compact summary
list_projects()                     exactly registered projects
project_context(project_id=...)     project-specific bounded orientation
doctor(project_id=...)              bounded health checks
git_status(project_id=...)          project root without redundant workdir
find_implementations(...)           read-only semantic routing
get_diagnostics(...)                read-only semantic routing
exec_command(project_id=..., ...)   command start/recovery still works
```

Also verify the protocol catalog/tool count/fingerprint from the live service matches the committed runtime.

- [x] **Step 6: Mark the pre-Worktree spec implemented/verified only after fresh evidence**

Record final HEAD, upstream tip, tool count/fingerprint, relevant test totals, and live connector fingerprint as historical evidence. Do not claim WT0 is unblocked before this step.

- [ ] **Step 7: Final local checkpoint**

```bash
git status --short --branch
git log -8 --oneline --decorate
```

Expected: no unstaged/staged implementation changes and no unexpected untracked files. Do not push without separate authorization.

---

## Plan Self-review

- **Spec coverage:** Task 1 covers PW0. Tasks 2-3 cover PW1 exact modern protocol, transport cancellation, MRTR, caching, deterministic catalog, and output-schema cleanup. Task 4 covers PW2 addressing/schema normalization. Tasks 5-6 cover PW3 compact context/doctor/server info and the two semantic IDE primitives. Task 7 covers PW4 operation identity/recovery/idempotency/continuations. Task 8 covers PW5 policy/confinement/observability. Task 9 covers PW6 repository/live/upstream acceptance.
- **Current-tree safety:** Task 0 isolates the existing credential/Landlock WIP before any planned task edits `server.py`; no PW commit is allowed to absorb it accidentally.
- **Deferred-work scan:** no implementation step defers unnamed future work; every phase names concrete interfaces, tests, commands, and commit boundaries.
- **Type consistency:** `ExecutionTarget` is the sole logical project/workdir target introduced in Task 4; `OperationContext`/`CancellationToken` are introduced in Task 2 and extended in Task 7; semantic request/result types introduced in Task 6 match the backend/worker/extension names used there.
- **Scope control:** no Worktree/Work Item/Hook/Gateway implementation, no generic batch/read-many API, no semantic editing, and no local specialist-tool clones are introduced in PW0-PW6.
