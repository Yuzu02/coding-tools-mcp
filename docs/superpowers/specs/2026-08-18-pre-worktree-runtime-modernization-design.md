# Pre-Worktree Runtime Modernization Design

**Date:** 2026-08-18
**Status:** IMPLEMENTED / VERIFIED 2026-08-19
**Target:** fork-owned runtime/extension foundation that must be GREEN before
Worktree routing, Work Items, Hooks, or Gateway implementation begins.

This specification defines the final **pre-Worktree modernization gate** for
the personal `coding-tools-mcp` fork.

The fork is intentionally free to improve its own MCP contract, remove awkward
compatibility aliases, add fork-owned tools, and adopt newer protocol behavior.
Behavioral parity with `xyTom/coding-tools-mcp` is **not** a design ceiling.

The non-negotiable compatibility requirement is narrower and more valuable:
the fork must remain **upstream-syncable**. New fork behavior should stay behind
small bridge/runtime seams so useful changes from `xyTom/main` can continue to
be fetched, reviewed, integrated, and regression-tested without reconstructing
the fork by hand.

This document is a prerequisite of:

- `2026-08-18-work-items-worktree-coordination-design.md`;
- the future Hook Engine implementation;
- the future MCP Gateway implementation; and
- any later higher-level coordination/UI layer.

The accepted implementation plan has now been completed through PW6. The
pre-Worktree runtime gate is GREEN and WT0 may begin.

### 2026-08-19 verification evidence

- implementation tip before this evidence-only documentation update:
  `c0bbf233a13ea0e5bdb70c1365f066016c0570f4`;
- freshly fetched upstream tip: `ed85e41999b0cf840d6e45f2bed11ac7f52eab3f`;
- `xyTom/main` is an ancestor of the implementation tip, with
  `git rev-list --left-right --count xyTom/main...HEAD` reporting `0 227` at
  verification time;
- PW5 policy/telemetry/security gate: 61 tests GREEN;
- focused core architectural gate: 110 tests GREEN;
- Serena-backed semantic integration gate: 13 tests GREEN using the pinned
  semantic extra;
- `mise run verify`: 736 tests GREEN with 7 intentional skips, plus the npm
  launcher/package checks;
- Ruff GREEN and mypy GREEN across 46 source files;
- committed composed catalog: 32 tools;
- deployed composed catalog: 32 tools;
- committed and deployed tool-name fingerprint:
  `474f9d9c60ca0f275e9c954a44d9f8a3f708c378eda200361c5baaa4a47611b3`;
- deployed project registry: exactly four available projects;
- live `project_context` and `doctor` returned bounded project/policy health
  views; live `get_diagnostics` routed through Serena; live
  `find_implementations` succeeded against a TypeScript semantic backend and
  correctly returned a structured backend error where the Python language
  server did not implement `textDocument/implementation`;
- live `exec_command` succeeded with only `project_id`, and `get_command`
  recovered the result by `client_request_id`.

One client-side operational caveat was observed during the same long-running
ChatGPT conversation: its already-materialized connector tool schemas remained
at the earlier 28-tool catalog even after the live MCP endpoint moved to 32.
Fresh raw `tools/list` against the deployed endpoint returned all 32 tools,
including `project_context`, `doctor`, `find_implementations`, and
`get_diagnostics`, and the live fingerprint matched the committed runtime.
This is treated as connector-host/session schema caching: a fresh connector
session/reconnect must refresh the tool schemas. It is not a server catalog or
runtime-contract mismatch.

---

## 1. Objective

Reach Worktree implementation with no known architectural guard left behind in
the current runtime.

The target server should be usable from a remote MCP chat as a complete coding
runtime without requiring the model or user to remember host paths, mutable
session state, duplicated routing arguments, or shell-only recovery procedures.

The pre-Worktree work therefore focuses on six foundations:

1. **upstream-sync integrity**;
2. **exact MCP 2026-07-28 behavior**;
3. **one canonical execution-target/addressing model**;
4. **correct request lifecycle, cancellation, and future MRTR support**;
5. **small, high-value context/diagnostic tools instead of repeated probing**;
6. **uniform policy, observability, and recovery contracts for every future
   subsystem**.

This phase deliberately does **not** implement Worktrees, Work Items, Hooks, or
Gateway functionality. It makes those later phases smaller and safer.

---

## 2. Core invariants

1. Every project-root operation is explicitly scoped by stable `project_id`.
2. There is no mutable current-project or transport-session cwd.
3. `project_id` alone selects the registered project execution root.
4. Optional `workdir` means a subdirectory under that selected execution root;
   omitted `workdir` means `"."`.
5. Future `worktree_id` inserts one additional deterministic execution-root
   selection step without changing those semantics.
6. Programmatically derivable state is derived by the server rather than
   repeated by the model.
7. Opaque server handles (`command_id`, `output_ref`, future `work_id`, etc.)
   carry their own ownership internally; callers do not repeat scope fields
   merely to reconstruct state the server already owns.
8. Ambiguous discovery/lookups that are not keyed by an opaque handle remain
   explicitly project-scoped.
9. A protocol-era feature is advertised only when the server implements its
   actual semantics for that era.
10. New tools use stable, bounded structured results and concise model-facing
    summaries.
11. New mutating capabilities reuse the same permission, credential,
    confinement, child-environment, cancellation, and audit boundaries as the
    existing runtime.
12. Fork-specific capabilities remain behind the existing extension/core
    bridge or a deliberately generic mother-core seam.
13. Upstream synchronization is proven by Git topology plus bridge/full gates,
    not by a documentation claim.
14. Large convenience APIs are rejected when a smaller primitive or server-side
    derivation gives the same result with less model context.

---

## 3. 2026-08-18 design/code-review findings

The following findings motivated this gate. They are evidence from the current
fork, not permanent deployment facts; implementation must revalidate them.

### 3.1 Upstream history topology is not currently a sufficient guard

A fresh local audit found:

- `sync/upstream-main` matches the locally fetched `xyTom/main` tip;
- the current upstream tip is **not** an ancestor of fork `main`;
- a previously integrated upstream-equivalent tip has the exact same Git tree;
  and
- the corresponding feature commits have identical stable patch IDs despite
  different commit identities/parentage.

This is a history-topology drift case: content-equivalent upstream work is
present, but the current upstream graph is not connected to fork `main` in the
way the documented sync policy expects.

Therefore tree/patch equivalence is useful **diagnostic evidence**, but it is not
the final synchronization invariant. Before Worktree implementation starts,
current `xyTom/main` must again be deliberately integrated into the fork graph.

### 3.2 Modern protocol dispatch still carries one removed core method

The current modern method table accepts `ping` for protocol `2026-07-28`.
The 2026-07-28 specification removes `ping` from the modern core. Legacy-era
support may continue where required, but modern dispatch should not claim or
silently preserve a removed method.

### 3.3 Cancellation is acknowledged but not request-cancelling

The current protocol dispatcher accepts `notifications/cancelled` and stays
silent, but it does not bind cancellation to an in-flight request operation.

For 2026-07-28:

- stdio uses `notifications/cancelled` for client request cancellation;
- Streamable HTTP cancels by closing the request's response stream; and
- modern Streamable HTTP does not define client-to-server cancellation
  notification POSTs as the normal cancellation path.

The runtime needs a real request cancellation token/registry rather than a
notification no-op before longer-lived Worktree/Gateway operations exist.

### 3.4 Public addressing aliases are semantically inconsistent

Current schemas expose examples of redundant or overloaded vocabulary:

- `exec_command` accepts both `workdir` and legacy alias `cwd`;
- `git_status.path` behaves as a workdir alias; while
- `git_diff`, `git_log`, `git_show`, and `git_blame` use `path`/`paths` as Git
  path filters.

This becomes dangerous once `worktree_id` is added. Addressing vocabulary must
be normalized first.

### 3.5 `server_info` is diagnostic-sized for a normal bootstrap call

The current `server_info` can include configuration fingerprints, extension
inventory, every tool name, credential-provider filesystem paths, Landlock
metadata, output retention settings, protocol versions, and other operator
details.

That is useful for debugging but unnecessarily expensive as a routine model
orientation call.

### 3.6 The runtime lacks one compact project-orientation primitive

A newly connected model often needs several calls to determine:

- selected project identity;
- Git branch/HEAD/dirty state;
- semantic capability/health;
- execution-policy state; and
- later, worktree/work-item state.

The older vNext design proposed `workspace_context`; the implemented
multi-project runtime should instead expose a project-addressed bounded
`project_context` aggregation view.

### 3.7 Modern list caching is intentionally disabled

The protocol layer currently shapes cacheable modern methods with:

```text
ttlMs = 0
cacheScope = private
```

This is conservative and correct, but `tools/list` is now deterministic and
the runtime already owns tool/config fingerprints. A bounded private cache can
be safe once invalidation identity is explicit.

### 3.8 Modern protocol capability surface is tools-only

The server currently implements the modern tool path plus discovery. It does
not implement MCP Resources, Prompts, Completions, subscriptions, or MRTR
input-required flows.

That is not itself a defect. These surfaces should be added only when they
solve a concrete Coding Tools problem; protocol breadth is not a goal.

### 3.9 Tool output schemas are repeated but intentionally generic

The current catalog advertises the same generic `outputSchema` for every tool.
It guarantees the shared `ok/error` envelope while allowing arbitrary
additional result fields.

That is wire-valid but has two costs:

- it repeats largely identical schema bytes for every tool; and
- it gives clients little useful information about the actual structured
  result.

Before the catalog grows further, output-schema publication should become
truthful and selective rather than universally generic.

---

## 4. Upstream synchronization gate

### 4.1 Policy

The fork is **upstream-syncable, not upstream-compatible**.

Acceptable fork changes include:

- a different tool count;
- different schemas;
- newer MCP protocol behavior;
- multi-project routing;
- semantic navigation;
- Worktree/Work coordination;
- fork-specific credential/runtime policy; and
- removal of legacy convenience aliases.

What must remain small and reviewable is the integration surface between the
mother core and fork extensions/runtime services.

### 4.2 Required Git lane

The synchronization lane remains:

```text
xyTom/main
   ↓ fetch/revalidate
sync/upstream-main
   ↓ deliberate review/integration
fork main
```

`sync/upstream-main` must equal the freshly fetched upstream ref before an
integration is evaluated.

### 4.3 Required ancestry invariant

Before any architectural phase such as Worktrees begins:

```text
git merge-base --is-ancestor xyTom/main HEAD
```

must succeed.

If it does not:

1. compare merge base, range counts, trees, and stable patch IDs;
2. determine whether content is missing or history was rewritten/reparented;
3. use equivalence only as diagnosis;
4. integrate the current upstream graph deliberately, normally with an
   explicit merge/integration commit rather than rewriting the fork merely for
   aesthetic linearity; and
5. rerun bridge/full gates.

Do not silently declare an equivalent tree sufficient forever: disconnected
history makes the next upstream sync harder.

### 4.4 Bridge guard

Upstream synchronization tests must prove at minimum:

- mother-core modules do not import fork-private `projects`, `semantic`,
  `work`, hooks, or gateway implementations;
- fork tool contributions flow through the ExtensionHost/contribution seam;
- tool decorators/schemas are applied through one composed catalog path;
- project routing is injected through generic workspace/runtime seams;
- upstream changes to core schemas/dispatch fail focused bridge tests instead
  of silently bypassing fork behavior; and
- a sync does not require editing fork feature internals merely to restore
  imports.

### 4.5 Publication gate

Every future normal publication should re-check:

```text
fresh xyTom/main
sync/upstream-main == xyTom/main
xyTom/main is ancestor of HEAD
origin/main is ancestor of HEAD before normal push
bridge gates GREEN
full relevant gates GREEN
```

No force-push is part of the normal sync model.

---

## 5. Exact MCP 2026-07-28 gate

### 5.1 Era separation

Keep two explicit behavior families:

```text
legacy: 2025-11-25 / 2025-06-18
modern: 2026-07-28
```

Era differences live in one protocol compatibility matrix. Avoid scattering
"if modern" behavior throughout business handlers.

### 5.2 `server/discover`

The modern runtime already implements `server/discover`; acceptance must prove
that it:

- is available for modern requests;
- lists only supported modern protocol versions;
- advertises only implemented capabilities;
- returns deterministic instructions/capability identity; and
- uses the modern result envelope.

### 5.3 Remove modern `ping`

`ping` remains a legacy compatibility method only if legacy clients still
require it. It is not accepted as a `2026-07-28` core method.

### 5.4 Transport-correct cancellation

Implement cancellation at the request-operation layer.

Required behavior:

```text
stdio modern:
  notifications/cancelled(requestId)
      -> cancel matching in-flight request operation

Streamable HTTP modern:
  response stream closed
      -> cancel matching in-flight request operation
```

The cancellation token must be visible to expensive request-owned work:

- filesystem/search loops where practical;
- semantic backend calls;
- provider discovery/lifecycle calls;
- future Gateway calls; and
- future Work/Hook orchestration.

Command processes need one explicit rule:

- before a command has been published as a recoverable `command_id`, request
  cancellation cleans up the in-progress start/wait operation;
- after a command handle has been published, the command is durable runtime
  work and cancellation of the original MCP request does not implicitly kill
  it; `kill_command` owns that lifecycle.

This preserves stateless command recovery without leaking orphan starts.

### 5.5 Multi Round-Trip Request foundation

Implement the generic 2026 `input_required` plumbing before Worktrunk approval
or future permission flows depend on it.

The foundation owns:

- bounded `inputRequests`;
- capability checks against per-request client metadata;
- opaque `requestState` generation/validation;
- tamper-resistant request-state sealing when server state must round-trip;
- bounded retry rounds;
- typed validation of `inputResponses`; and
- decline/cancel handling.

No feature is required to use MRTR immediately. This phase establishes the
transport/protocol seam so later features do not invent bespoke chat-state
handshakes.

### 5.6 Cacheable list results

Use modern caching only where invalidation is strong.

Recommended first step:

```text
tools/list:
  cacheScope = private
  ttlMs > 0 only while tool/config catalog fingerprint is unchanged

server/discover:
  remain ttlMs = 0 until every instruction/capability input has a deterministic
  generation/fingerprint
```

Changing enabled extensions, tool schema, annotations, permissions that shape
catalog visibility, or configuration generation invalidates the cached list.

### 5.7 Deterministic tool order

The composed catalog must keep a deterministic order across requests for the
same configuration generation. This helps client caching and LLM prompt-cache
reuse.

### 5.8 Header routing preparation

The modern HTTP protocol supports schema-declared `x-mcp-header` parameters.

After compatibility tests prove the client ecosystem behaves correctly, stable
routing identities such as `project_id` and future `worktree_id` may be marked
for header mirroring so a future Gateway/load balancer can route/observe them
without parsing JSON bodies.

The JSON body remains the source of truth and mismatched mirror headers must be
rejected. This is an optimization, not an authorization source.

### 5.9 Do not add protocol primitives without a product use

Before Worktrees, do **not** add Resources, Prompts, Completions, subscriptions,
or Tasks merely to advertise a wider MCP surface.

Current decisions:

- Files/Git/runtime context remain model-controlled tools.
- A `project_context` tool is a better fit than an application-controlled
  Resource for normal coding-agent orientation.
- Prompts are user-controlled templates and do not replace project
  instructions/skills.
- MCP Tasks are not Work Items.
- subscriptions become relevant only when a concrete resource/event consumer
  exists.

---

## 6. Canonical execution target and schema cleanup

### 6.1 One resolver

All project-root operations consume one internal target model:

```text
ExecutionTarget
  project_id
  worktree_id?       # future
  execution_root
  workdir
  scope_chain
  policy identity
```

Resolution is conceptually:

```text
project = ProjectRegistry.require(project_id)
root = resolve_worktree(project, worktree_id?)  # default root before WT ships
workdir = resolve_beneath(root, supplied_workdir ?? ".")
```

Filesystem, Git, command, skill/instruction, patch, credential, semantic, and
future provider entry points consume the resolved object rather than
reimplementing path defaults.

### 6.2 Public vocabulary

Use one word for one concept:

```text
project_id   logical project routing
worktree_id  physical execution-root routing (future)
workdir      relative execution subdirectory
path/paths   target file/path filters
```

### 6.3 Remove `cwd` from the new public contract

`exec_command.cwd` is redundant with `workdir` and reintroduces session/cwd
vocabulary that the stateless design intentionally removed.

The next fork runtime contract should expose only `workdir`.

If a narrow legacy decoder is retained temporarily, it is not advertised in
the modern tool schema and conflicting aliases are rejected.

### 6.4 Normalize Git path semantics

`git_status.path` must stop meaning "alternate workdir".

Across Git tools:

- `workdir` means where Git runs;
- `path`/`paths` mean repository-relative path filters when the operation
  supports filtering; and
- omission means no path filter.

If `git_status` does not need filtering in the accepted V1 contract, remove
`path` from that schema rather than preserve an overloaded alias.

### 6.5 Handle-scoped operations stay compact

Opaque handles already identify server-owned state. Do not require redundant
`project_id`/`worktree_id` on every handle operation solely for reconstruction.

Examples:

```text
read_output(output_ref)
kill_command(command_id)
write_stdin(command_id, ...)
```

The server validates the handle's stored project/worktree ownership internally.

Conversely, discovery by a non-opaque client key must be scoped enough to avoid
cross-project ambiguity. For example, future recovery by a caller-chosen
`client_request_id` must include project/worktree identity in its uniqueness
scope or require an explicit target when ambiguity is possible.

### 6.6 Continuations do not leak redundant physical routing

`next_action`/continuation payloads should carry the smallest stable addressing
state:

```text
project_id
worktree_id?  # when non-default
cursor/offset
relative filters
```

Do not return absolute host paths as continuation state when the runtime can
derive them from project/worktree identity.

### 6.7 Output-schema publication is selective and precise

`structuredContent` remains the machine-facing result for every tool, but MCP
`outputSchema` is optional.

The fork should stop repeating one broad generic schema on every tool.

Rules:

1. advertise `outputSchema` only when the registered schema describes the
   stable result usefully;
2. validate server-produced structured content against advertised schemas in
   tests;
3. omit the schema for highly variable/legacy results until a precise bounded
   contract exists; and
4. measure serialized catalog bytes before/after schema additions.

This reduces `tools/list` context while increasing the value of schemas that
remain.

---

## 7. Request/operation context foundation

### 7.1 Separate protocol context from operation context

The current request context correctly carries per-request era/version/client
metadata. Add a generic internal operation layer rather than putting more state
into transport sessions.

Conceptually:

```text
OperationContext
  request_id
  protocol era/version
  bounded client metadata
  client capabilities
  ExecutionTarget?       # for project-scoped tools
  cancellation token
  deadline/budget
  permission/policy view
  trace/operation id
```

This is immutable request state, not a session.

### 7.2 Deadlines

All provider/backend operations should receive one effective deadline derived
from the tool's bounded timeout and remaining request lifetime.

Nested operations must not each reset a fresh full timeout.

### 7.3 Output/response budgets

Keep bounded tool-specific results, but provide shared helpers for:

- model-text byte ceilings;
- structured collection caps;
- truncation metadata;
- continuation construction; and
- provider stderr/stdout bounding.

Future extensions must consume these helpers rather than invent new unbounded
payload conventions.

---

## 8. Context-window efficiency

### 8.1 `server_info` summary by default

Evolve `server_info` to a small default result:

```text
server/package/runtime-contract versions
supported protocol eras
configuration generation/fingerprint
tool catalog fingerprint/count
enabled extensions + compact health
project count
permission mode
auth enabled/disabled
```

Detailed operator state becomes opt-in, for example:

```text
server_info(detail="diagnostic", include_paths=true)
```

Diagnostic-only fields include credential-provider storage paths, full
Landlock details, output-retention internals, and other filesystem-oriented
metadata.

### 8.2 Add `project_context`

Add one read-only bounded tool:

```text
project_context(project_id, worktree_id?, detail="summary")
```

Before Worktrees ship, `worktree_id` is absent from the public schema; the
internal design reserves the dimension.

Default summary should include data already owned by existing services:

```text
project_id
Git branch / HEAD / dirty summary
project availability
semantic backend availability/health
applicable instruction/skill summary
execution policy summary
configuration generation
bounded active-command count/health
```

Later extensions enrich the same aggregation view with:

```text
selected/default worktree
active Work Item/claim summary
relevant Hook status
Gateway upstream health summary
```

`project_context` is an aggregation view only. Git, skills, commands, Work,
Hooks, and Gateway remain the sources of truth.

### 8.3 Add `doctor`

Add one deterministic read-only diagnostic tool:

```text
doctor(project_id?, detail="summary")
```

Without `project_id`, it checks server/deployment foundations. With a project,
it adds project-scoped checks.

Checks should be typed and bounded:

```text
id
status = pass | warn | fail
summary
details?          # bounded/diagnostic
recovery?         # structured hint, never auto-mutation
```

Server checks may include:

- configuration validity/generation;
- extension prepare/health;
- state/cache/runtime directory accessibility;
- credential registry health;
- confinement backend availability;
- protocol/catalog self-consistency.

Project checks may include:

- registered root visibility;
- Git repository health;
- child environment/toolchain resolution;
- credential-provider applicability;
- semantic backend availability;
- instruction/skill parse warnings; and later
- Worktrunk/common-dir/worktree sandbox health.

The tool does not repair state automatically.

### 8.4 Do not add generic batching

Reject a generic model-facing `batch`, `multi_tool`, or arbitrary operation
envelope. It creates large conditional schemas, blurs permission boundaries,
and makes partial failure/retry semantics harder.

Prefer compact aggregation tools (`project_context`, `doctor`) and existing
bounded file/search primitives.

### 8.5 Do not add `read_many` by default

Reading many arbitrary files in one result can increase context more than it
saves round trips. `search_text`, `list_files`, targeted `read_file`, and later
resource links are preferable until a measured workload proves otherwise.

---

## 9. Tool-surface decisions before Worktrees

### 9.1 Add now in the modernization phase

Four new public tools are justified as platform primitives:

```text
project_context
doctor
find_implementations
get_diagnostics
```

`project_context` and `doctor` reduce repeated probing and make remote
operation/debugging practical.

`find_implementations` and `get_diagnostics` complete two high-value read-only
IDE capabilities already available through the pinned Serena/LSP backend. They
extend the existing `semantic` extension rather than creating another semantic
stack.

Recommended compact contracts:

```text
find_implementations(
  project_id,
  path,
  line,
  column,
  max_results=...
)

get_diagnostics(
  project_id,
  path,
  start_line?,
  end_line?,
  min_severity?,
  max_results=...
)
```

Keep diagnostics file-oriented in V1. Symbol diagnostics can be derived from a
symbol location plus the file diagnostic result instead of adding a second
nearly identical public tool.

Both operations remain read-only, bounded, backend-neutral, and later gain the
same optional `worktree_id` routing as the existing semantic tools.

### 9.2 Improve existing tools

Required improvements:

- compact/default `server_info`;
- normalized `workdir`/`path` vocabulary;
- remove modern `cwd` alias;
- smaller continuation arguments;
- real request cancellation;
- deterministic modern caching where safe;
- uniform structured recovery hints.

### 9.3 Keep `apply_patch` as sole direct text mutation primitive

Do not add `edit_file`, `write_file`, or ad-hoc search/replace tools merely for
convenience. `apply_patch` already owns staged multi-file add/update/delete/move
semantics and remains the direct mutation boundary.

Generated/binary workflows continue through explicit commands until a concrete
safe binary-write use case justifies a separate primitive.

### 9.4 Do not clone Moon/Mise task APIs

Do not add local `moon_*`, `mise_*`, package-manager, Context7, GitHub, or
Playwright clones before Gateway.

Use `exec_command` for host-local task execution today. Gateway later imports
specialist MCP capabilities through policy-controlled namespaces.

### 9.5 Structured Git mutation is deferred

`git_commit`, `git_push`, `git_fetch`, `git_merge`, staging, and branch mutation
could reduce shell use, but they expand index/ref/network semantics and overlap
with future Worktrunk lifecycle.

Keep Git mutation through `exec_command` until Worktree lifecycle is designed
and measured. Revisit only if shell-based Git mutation remains a high-frequency
source of errors after Worktrees.

### 9.6 `path_info` is optional, not a gate

A single-path metadata tool could be convenient, but `list_dir`, `read_file`,
and existing path validation already expose most required facts. Do not block
Worktrees on it.

### 9.7 Do not add semantic editing before Worktrees

Serena also supports semantic editing/refactoring capabilities, but exposing
them now would create a second direct mutation path beside `apply_patch` and a
new rollback/baseline model.

Pre-Worktree semantic expansion stays read-only. Rename/refactor operations are
revisited separately after Worktree isolation and mutation-policy semantics are
stable.

---

## 10. Error and recovery contract

### 10.1 Stable error taxonomy

Every tool/extension error continues to return:

```text
code
message
category
retryable
details
```

New subsystems extend a central registry rather than inventing unstructured
error strings.

### 10.2 Structured recovery hints

When the runtime knows the next safe action, add a bounded machine-readable
recovery shape in `details`, for example:

```text
recovery:
  kind: retry | inspect | call_tool | user_action
  tool?: get_command
  arguments?: { command_id: ... }
  reason: ...
```

The model-facing summary still explains the failure concisely.

Recovery hints never silently authorize a mutation.

### 10.3 Idempotency vocabulary

Use one caller-generated name for uncertain mutation retries in new APIs:

```text
request_id
```

Existing `exec_command.client_request_id` may remain until a deliberate
contract migration, but future Worktrunk/Work mutations should not invent a
third term.

Idempotency keys are always scoped by operation plus normalized logical target
and payload fingerprint.

Durable Worktree/Work idempotency that must survive restart is implemented with
the later durable state layer; this pre-Worktree phase establishes vocabulary
and helper interfaces only.

---

## 11. Policy, credentials, and confinement

### 11.1 One child-operation path

Every subprocess/provider backend must consume the canonical child-environment
builder and policy path.

No Worktrunk, Hook, Gateway stdio process, semantic worker, or future verifier
may construct its own ad-hoc environment with server bootstrap credentials.

### 11.2 Credential provider ownership

Credential applicability is selected from logical operation/project identity,
not because an executable happens to run from a particular physical checkout.

Future worktrees of one `project_id` therefore inherit the project's authorized
credential providers unless HostConfig defines a stricter ceiling.

Credential values and full private filesystem locations do not appear in
normal model-facing context.

### 11.3 Confinement derives from `ExecutionTarget`

Filesystem/Landlock roots, generated/ignored-write checks, and destructive
command classification are computed from the selected target. There is no
independent path interpretation in credential or command code.

### 11.4 New tool annotations are truthful

Every public tool explicitly sets read-only, destructive, idempotent, and
open-world hints based on real behavior.

Annotations remain hints rather than authorization facts.

---

## 12. Observability and remote operation

### 12.1 Operation identity

Every tool call receives one internal `operation_id`/trace identity used in
logs and diagnostics.

Do not force the model to echo it on normal calls.

Where the transport/client supports non-model metadata, expose correlation in
`_meta` rather than verbose `content`.

### 12.2 Structured event fields

At minimum record bounded non-secret fields:

```text
operation_id
tool
project_id?
worktree_id?       # future
duration
status/error_code
input/output size class
backend/provider identity when relevant
```

Never log secret environment values, full command environments, lease tokens,
or arbitrary chat contents.

### 12.3 Modern logging direction

Do not invest in deprecated session-scoped MCP logging as a platform feature.
Keep operational logs on stderr/journald and prepare optional OpenTelemetry or
equivalent structured export behind deployment policy.

`doctor` is the model-visible diagnostic surface; logs remain operator data.

---

## 13. Performance requirements

1. `project_context(summary)` completes without enumerating every file,
   worktree, command output, or semantic symbol.
2. `doctor(summary)` runs bounded local checks and marks expensive/network
   checks as opt-in.
3. `server_info(summary)` stays small and path-light.
4. deterministic catalog/list caching never depends on transport session
   identity.
5. one request creates at most one canonical `ExecutionTarget` resolution per
   target unless revalidation is explicitly required.
6. cancellation checks are cheap enough to use inside long loops/backends.
7. diagnostic detail does not become the default result size.
8. adding future extensions does not force `list_projects` or
   `project_context` to inline their complete inventories.

---

## 14. Pre-Worktree implementation phases

### PW0 — Upstream synchronization integrity

- refresh `xyTom/main`;
- align `sync/upstream-main`;
- diagnose the current equivalent-but-disconnected history;
- reconnect current upstream ancestry deliberately;
- rerun bridge compatibility gates;
- make ancestry a required acceptance check.

### PW1 — MCP 2026 exactness

- remove modern `ping`;
- make HTTP/stdio cancellation transport-correct;
- add in-flight request cancellation tokens;
- add MRTR `input_required` protocol plumbing;
- verify modern header/body validation;
- keep capability advertisement exact;
- add safe deterministic list caching where fingerprint coverage permits.

### PW2 — Addressing/schema normalization

- implement canonical `ExecutionTarget` service;
- remove public `cwd` alias from the new contract;
- normalize Git `workdir` versus path-filter semantics;
- shrink continuation routing state;
- prove nested-project/symlink isolation remains intact.

### PW3 — Context and doctor tools

- add compact `project_context`;
- add bounded `doctor`;
- add backend-neutral `find_implementations`;
- add bounded file-oriented `get_diagnostics`;
- make `server_info` summary-first;
- keep detailed paths/operator internals opt-in;
- add regression budgets for serialized response size.

### PW4 — Operation context / recovery

- add request operation identity/deadline/cancellation propagation;
- centralize structured recovery hints;
- centralize output/truncation/continuation helpers;
- establish common `request_id` convention for future mutation APIs.

### PW5 — Policy/observability convergence

- prove credentials/child-environment/confinement use common operation target;
- add structured operation telemetry fields;
- add diagnostic coverage for provider/policy health;
- verify no normal result leaks secret/path-heavy state unnecessarily.

### PW6 — Final pre-Worktree acceptance

- full protocol/conformance gates;
- full unit/integration/compliance gates;
- serialized tool/catalog budget checks;
- four-project isolation acceptance;
- semantic/command recovery acceptance;
- live connector acceptance;
- fresh upstream ancestry/bridge proof.

Only after PW0-PW6 are GREEN may
`2026-08-18-work-items-worktree-coordination-design.md` start WT0.

---

## 15. Testing strategy

### 15.1 Upstream bridge

- sync branch equals fetched upstream;
- upstream ancestry guard;
- a synthetic history-equivalent-but-disconnected fixture fails ancestry while
  patch/tree diagnostics explain why;
- mother-core bridge import/dispatch/contribution tests remain portable in CI.

### 15.2 Modern protocol

- `server/discover` exact schema/capabilities;
- modern `ping` rejected with method-not-found;
- legacy `ping` behavior preserved only where contracted;
- stdio cancellation cancels in-flight operation;
- HTTP response-stream close cancels in-flight operation;
- modern HTTP does not depend on cancellation notification POSTs;
- MRTR request-state tamper/expiry/missing-response cases;
- cache TTL/fingerprint invalidation;
- deterministic tool order.

### 15.3 Addressing

- `project_id` with omitted `workdir` targets root;
- explicit `workdir` remains beneath root;
- `cwd` no longer appears in modern schema;
- Git path filters have consistent meaning;
- symlink/traversal/nested registered-project escapes reject;
- continuations contain logical, not absolute, routing state.

### 15.4 Context efficiency

- `server_info(summary)` byte ceiling;
- `project_context(summary)` byte ceiling;
- `doctor(summary)` byte/check-count ceiling;
- diagnostic severity/range/result bounds;
- implementations result bounds and backend error isolation;
- diagnostic mode may be larger but remains bounded;
- adding many commands/skills later does not expand summary without a cap.

### 15.5 Policy

- child environment identical across core/provider consumers where expected;
- credential provider selection cannot be changed by path escape;
- secret env/path values absent from normal summaries;
- annotations and permission classification are truthful.

---

## 16. Acceptance criteria

The pre-Worktree modernization gate is complete only when all of the following
are fresh and true:

1. `xyTom/main` is freshly fetched and is an ancestor of fork `main`.
2. `sync/upstream-main` equals the fetched upstream tip.
3. Upstream bridge compatibility tests are GREEN.
4. Modern dispatch matches the 2026-07-28 supported core; removed modern
   methods are not accepted merely for legacy convenience.
5. `server/discover` advertises only implemented capabilities.
6. Cancellation actually stops in-flight request-owned work through the proper
   transport mechanism.
7. Generic MRTR input-required plumbing is tested even if no production tool
   uses it yet.
8. Every project-scoped core operation consumes one canonical execution-target
   resolver.
9. `cwd` is not part of the new public command schema.
10. Git `workdir`/`path` semantics are consistent.
11. `server_info` defaults to a compact model-facing summary.
12. `project_context` gives a bounded one-call project orientation view.
13. `doctor` gives deterministic bounded server/project diagnostics without
    mutation.
14. `find_implementations` and `get_diagnostics` expose the pinned semantic
    backend's corresponding read-only IDE capabilities through Coding
    Tools-owned schemas.
15. Tool output schemas are either precise/tested or omitted; the catalog no
    longer repeats a low-information generic output schema on every tool.
16. Continuations/recovery hints do not force models to retain physical host
    paths.
17. Existing command recovery and semantic isolation still pass.
18. Credential/policy/confinement behavior is shared by future provider seams.
19. Full current verification and live connector acceptance are GREEN.

Only then is Worktree implementation allowed to start.

---

## 17. Explicit non-goals

This gate does not implement:

- Worktree discovery/routing/lifecycle;
- Work Items/claims/leases/checkpoints;
- Hook execution;
- Gateway upstream clients;
- MCP Tasks;
- generic batching;
- local clones of Moon/Mise/GitHub/Context7/Playwright MCP APIs;
- autonomous agents/subagent spawning;
- generic file-write alternatives to `apply_patch`; or
- a distributed multi-replica state backend.

---

## 18. Primary references

MCP 2026-07-28 references:

- specification index:
  `https://modelcontextprotocol.io/specification/2026-07-28`
- authoritative schema:
  `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts`
- discovery:
  `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/discover.mdx`
- tools, deterministic ordering, caching, structured content, output schemas,
  and `input_required`:
  `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx`
- Streamable HTTP routing/cancellation/custom parameter headers:
  `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx`
- stdio cancellation:
  `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/stdio.mdx`
- official TypeScript SDK migration guidance for MRTR/cancellation/era behavior:
  `https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/migration/support-2026-07-28.md`

Fork/upstream references:

- original project:
  `https://github.com/xyTom/coding-tools-mcp`
- local fork synchronization architecture:
  `docs/extensions.md`
- extension architecture authority:
  `docs/superpowers/specs/2026-08-17-extension-architecture-config-design.md`

Semantic backend references:

- Serena changelog documenting `find_implementations`,
  `get_diagnostics_for_file`, and `get_diagnostics_for_symbol`:
  `https://github.com/oraios/serena/blob/main/CHANGELOG.md`
- Serena project/source repository:
  `https://github.com/oraios/serena`
