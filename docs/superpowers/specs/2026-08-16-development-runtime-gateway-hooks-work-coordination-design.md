# Development Runtime Gateway, Hooks, and Work Coordination Design

Status: **Proposed design**

Date: 2026-08-16

Repository: `coding-tools-mcp`

**Supersession note:** its proposed global/workspace configuration precedence
and `${ENV:NAME}` secret-reference syntax are superseded by the implemented
HostConfig v2 / ProjectConfig v1 authority model. This document remains
proposed for Hooks, Work Items, and Gateway only; it does not authorize those
features or redefine the deployed v0.4 runtime.

This document specifies an additive vNext architecture for `coding-tools-mcp`.
It preserves the current v0.3 coding runtime while defining the next layer of
workspace configuration, deterministic hooks, durable work coordination, MCP
gateway/proxy capabilities, and optional code/research integrations.

It is a design specification, not an implementation plan. The current runtime
contract remains [`SPEC.md`](../../../SPEC.md) and
[`docs/runtime-contract-v0.3.md`](../../runtime-contract-v0.3.md) until an
implementation is explicitly accepted and versioned.

The earlier [`docs/work-items-design.md`](../../work-items-design.md) remains a
focused design note. This document incorporates its decisions and broadens the
architecture around them; it does not silently redefine the current v0.3
contract.

## 1. Goals

The vNext runtime should make one workspace-facing MCP server sufficient for a
large class of development, analysis, and research workflows without turning
`coding-tools-mcp` into an LLM runtime or reimplementing specialist tools that
already expose MCP.

Primary goals:

1. Preserve the v0.3 core: workspace inspection, `apply_patch`, command
   lifecycle, Git, permissions, instructions, skills, and confinement.
2. Make workspace behavior reproducible through typed project configuration
   while keeping secrets and user defaults outside Git.
3. Add deterministic post-edit verification so format/lint/typecheck/tests can
   run after a successful mutation independent of the host application's hook
   support.
4. Coordinate independently-running clients through durable Work Items,
   claims, leases, checkpoints, and optional worktree bindings without
   pretending the MCP server can create another ChatGPT/Codex/Claude chat.
5. Aggregate specialist MCP servers such as mise, moon, Context7, GitHub, and
   Playwright through policy-controlled namespaces instead of cloning them.
6. Improve code intelligence and verification with structured workspace
   context, diagnostics, and optional language-aware navigation.

## 2. Non-goals

V1 must not:

- add `agent_spawn` or claim to create another model conversation;
- embed an LLM provider, model router, billing system, or autonomous subagent
  scheduler;
- replace mise, moon, Worktrunk, Context7, GitHub MCP, Playwright MCP, or
  language servers with local clones;
- expose unrestricted `mcp_call(server, tool, arbitrary_json)` as the normal
  model-facing interface;
- require MCP Tasks support for Work Items;
- infer authority from self-reported actor/client metadata;
- introduce new transport-session semantics;
- roll back an applied patch merely because a post-mutation hook fails;
- execute repository-configured shell strings through an implicit shell by
  default;
- expose every tool from every upstream by default.

## 3. Protocol baseline: MCP 2026-07-28

The design targets MCP `2026-07-28` as the modern era while retaining the
repository's explicit compatibility with `2025-11-25` and `2025-06-18`.

### 3.1 Stateless protocol does not mean stateless application

MCP `2026-07-28` removes `initialize`/`initialized` and `Mcp-Session-Id` from
the modern wire protocol. Every request carries protocol/client metadata in
`params._meta`. Servers implementing the revision implement
`server/discover`; clients may probe it before normal requests.

This is wire-level statelessness. Durable application state remains valid when
subsequent requests address it with explicit handles such as `command_id`,
`work_id`, `claim_id`, or `hook_run_id` instead of relying on connection
continuity.

Consequences:

- every stateful operation uses an explicit opaque identifier;
- no feature obtains authority from a transport session;
- restart/reconnect recovery is part of the contract where persistence is
  promised;
- new permission persistence should use explicit grants/leases with IDs and
  TTLs, not ambiguous session scope;
- workspace identity and authenticated principal are isolation boundaries.

### 3.2 Discovery and legacy upstreams

Gateway clients should prefer `server/discover` and fall back to the legacy
`initialize` era when an upstream does not support modern discovery.

```text
server/discover
    |-- supported --> MCP 2026-07-28 adapter
    `-- unsupported -> legacy initialize adapter
```

Discovery caches must be partitioned by upstream identity plus authentication
and configuration context whenever those affect the visible catalog.

### 3.3 MCP Tasks are not Work Items

The 2026 release moved Tasks out of the core protocol into the
`io.modelcontextprotocol/tasks` extension for durable long-running operation
state. The reference extension remains experimental, and the Python SDK v2
currently documents that it does not yet implement the new Tasks extension.

Therefore:

- **MCP Task** = protocol-level long-running operation state;
- **Work Item** = repository/workspace coordination record owned by this
  product domain.

The names, schemas, and lifecycles remain distinct. A future adapter may map a
long-running operation to MCP Tasks when support is mature, but V1 does not
depend on it.

### 3.4 Deprecated 2026 features

Roots, Sampling, and Logging are deprecated in the 2026 revision. New vNext
features must not require them. Multi-round-trip requests are a protocol
compatibility concern, not a mechanism for spawning chats or coordinating Work
Items.

## 4. Product architecture

The recommended product is a **development runtime plus MCP gateway**, not a
transparent proxy and not an agent runtime.

```text
             ChatGPT / Codex / Claude / IDE / CI
                          |
                          | MCP
                          v
 +-------------------------------------------------------+
 |                  coding-tools-mcp                     |
 |                                                       |
 |  Core runtime                                         |
 |    filesystem / patch / commands / git / policy       |
 |                                                       |
 |  Development services                                 |
 |    config / hooks / Work Items / diagnostics          |
 |    checkpoints / Worktrunk adapter                    |
 |                                                       |
 |  MCP gateway                                          |
 |    upstream registry / client pool / namespaces       |
 |    policy filters / discovery cache / routing         |
 |                                                       |
 |  Durable operational state                            |
 |    work / leases / hook summaries / metadata          |
 +--------------------------+----------------------------+
                            |
                mise / moon / Context7 / GitHub / ...
```

The gateway does not make downstream services trusted automatically. Each
upstream has its own transport, credentials, namespace, exposure policy,
timeouts, and lifecycle.

## 5. Configuration model

### 5.1 Files and precedence

This section is historical proposal material. The implemented configuration
authority is HostConfig v2, with `env:NAME` and `file:/absolute/path` secret
references; it replaces the precedence and interpolation syntax below.

Use two configuration layers:

1. User/global config outside repositories:
   `~/.config/coding-tools-mcp/config.toml` or platform equivalent.
2. Trusted project config inside the workspace:
   `.coding-tools-mcp.toml`.

Precedence:

```text
built-in defaults
    < global/user config
    < workspace config for project-owned keys
    < explicit deployment/CLI overrides
```

The merge is schema-driven. Workspace config cannot weaken global security
ceilings, inject literal secrets, disable mandatory confinement, or escalate
deployment-level permissions.

### 5.2 Secrets

Repository config stores references, never secret values. A narrow initial
syntax is sufficient:

```toml
token = "${ENV:GITHUB_TOKEN}"
```

Resolved secrets are redacted from config tools, logs, Work metadata, hook
records, and unrelated upstream environments.

### 5.3 Example

```toml
version = 1

[workspace]
explicit_workdir = true

[state]
backend = "sqlite"

[hooks]
enabled = true

[[hooks.after_patch]]
id = "ruff"
match = ["**/*.py"]
argv = ["mise", "run", "lint-python", "--", "{files}"]
timeout_ms = 30000
failure = "report"

[[hooks.after_patch]]
id = "typescript-check"
match = ["**/*.ts", "**/*.tsx"]
argv = ["moon", "run", ":lint", "--affected"]
timeout_ms = 60000
failure = "report"

[work]
enabled = true
lease_ttl_seconds = 300

[work.worktrees]
provider = "worktrunk"

[gateway]
enabled = true
default_exposure = "deny"

[gateway.upstreams.mise]
transport = "stdio"
argv = ["mise", "mcp"]
namespace = "mise"
auto_enable_if = ["mise.toml"]

[gateway.upstreams.mise.env]
MISE_EXPERIMENTAL = "1"

[gateway.upstreams.moon]
transport = "stdio"
argv = ["moon", "mcp"]
namespace = "moon"
auto_enable_if = [".moon"]

[gateway.upstreams.context7]
transport = "streamable-http"
url = "https://mcp.context7.com/mcp"
namespace = "context7"
expose = ["*"]

[gateway.upstreams.context7.headers]
Authorization = "Bearer ${ENV:CONTEXT7_API_KEY}"

[gateway.upstreams.github]
transport = "stdio"
argv = ["github-mcp-server", "stdio", "--read-only", "--toolsets=repos,issues,pull_requests"]
namespace = "github"
expose = ["*"]
```

### 5.4 Configuration inspection

Candidate read-only surfaces:

- `config_status`: resolved layers, validation, sources, warnings, redacted
  values;
- `gateway_list_upstreams`: configured/active upstreams and health;
- `hooks_list`: hook IDs, matchers, policy, and status;
- `workspace_context`: consolidated runtime/workspace view.

Configuration mutation remains file-based through `apply_patch`; a second
imperative config-writing API is unnecessary in V1.

## 6. Deterministic Hook Engine

### 6.1 Purpose

Hooks enforce deterministic local automation around server-owned operations.
They complement host-specific agent hooks, but live inside `coding-tools-mcp`
so the same repository behavior applies from ChatGPT, Codex, Claude, an IDE,
or CI.

The primary V1 flow is:

```text
apply_patch succeeds
    |-- identify changed files
    |-- match after_patch hooks
    |-- execute formatter/linter/checker through runtime command policy
    `-- return mutation result + hook results
```

VS Code's current agent-hook model is useful precedent: deterministic hooks can
run around tool use and are explicitly recommended for formatters, linters,
security gates, and audit. The runtime should adopt the deterministic idea, not
copy a host-specific agent lifecycle verbatim.

### 6.2 V1 events

Keep the initial lifecycle intentionally small:

- `after_patch`: after a successful `apply_patch` commit;
- `after_command`: optional event after a bounded `exec_command` reaches a
  terminal state.

Potential `before_command`, `before_mutation`, and `after_work_complete` events
are later scope. Automatic linting does not require them.

### 6.3 Structured execution

Hook commands are argv arrays. The default path does not invoke a shell,
interpret pipes, expand command substitutions, or interpolate arbitrary
environment expressions.

Initial typed placeholders:

- `{files}`: changed files matching the hook;
- `{workspace}`: canonical workspace root;
- `{workdir}`: operation workdir.

Shell semantics, if ever needed, require explicit opt-in and the same permission
policy already used for shell expansion and inline scripts.

### 6.4 Shared environment isolation

Hooks and `exec_command` must use one child-environment builder. There must not
be a parallel subprocess stack for hooks.

This is also where the observed bootstrap-environment leak must be fixed. A
server-owned `VIRTUAL_ENV`, `UV_PROJECT_ENVIRONMENT`, `UV_PYTHON`, `UV_NO_SYNC`,
`UV_MANAGED_PYTHON`, or `UV_PYTHON_INSTALL_DIR` must not force workspace
commands to use the MCP server's private Python instead of the repository's own
toolchain.

### 6.5 Mutation versus verification state

A post-hook failure never makes an already-committed patch look unapplied.
Results distinguish the two states:

```json
{
  "mutation": {"status": "applied"},
  "verification": {"status": "failed"},
  "hooks": [
    {
      "id": "ruff",
      "status": "failed",
      "exit_code": 1,
      "command_id": "..."
    }
  ]
}
```

Default `failure = "report"` means preserve the mutation, surface the failure,
retain output through normal command-output references, and allow a subsequent
fix. Automatic rollback is outside V1.

### 6.6 Recursion prevention

Hooks do not recursively trigger themselves through their own subprocesses.
Each operation carries hook-origin metadata. V1 permits one hook layer;
`after_command` ignores commands whose origin is `hook`.

### 6.7 Observability

Persist bounded hook-run metadata:

- hook ID and configuration generation/hash;
- triggering operation/command ID;
- matched path set or bounded summary;
- timestamps and duration;
- exit/timeout/cancellation status;
- output references.

Never persist secret environment values or full environment dumps.

## 7. Work coordination: Work Items, Actors, Claims, and Leases

### 7.1 Boundary

Independent chats and coding agents can operate on the same repository, but the
MCP server cannot open another ChatGPT conversation merely because a tool was
called. Coordination therefore records existing actors rather than spawning
them.

Use `Actor` as trace metadata. An actor may describe a ChatGPT client, Codex
invocation, Claude session, CI job, human, or unknown MCP client. Actor metadata
does not grant authority.

### 7.2 Work Item model

```text
WorkItem
  id
  workspace_id
  title
  description
  status
  priority
  created_at
  updated_at
  dependencies[]
  scopes[]
  active_claim?
  checkpoints[]
  worktree_binding?
  metadata
```

Keep statuses small and explicit. A representative lifecycle is:

```text
open -> claimed -> in_progress -> completed
                    |-- blocked
                    `-- cancelled
```

Exact transition rules belong to the implementation plan and persistence
tests. Completion is never inferred from a transport disconnect.

### 7.3 Claims and authority

```text
Claim
  claim_id
  work_id
  lease_token_hash
  actor_metadata
  scopes[]
  acquired_at
  renewed_at
  expires_at
```

The lease credential is the authority. Actor metadata is not. A Work Item has
at most one active claim in V1; persistence enforces that atomically so two
simultaneous clients cannot both win.

### 7.4 Lease behavior

Leases are explicit and time-bounded. Operations requiring ownership receive
`work_id` plus a lease credential. Renewal extends the lease atomically.

If a chat or client disappears:

```text
active claim -> expires -> stale/reclaimable
```

The Work Item remains. Recovery is an application operation, not a session
cleanup callback.

### 7.5 Candidate Work tools

Keep names clearly separate from MCP Tasks:

- `work_create`
- `work_get`
- `work_list`
- `work_claim`
- `work_renew`
- `work_release`
- `work_update`
- `work_block`
- `work_complete`
- `work_cancel`
- `work_checkpoint`

Do not add `agent_spawn`, `agent_message`, or `agent_inbox` in V1.

### 7.6 Path scopes

Work Items may declare coordination scopes such as:

```text
packages/auth/**
apps/web/app/api/auth/**
```

V1 scopes are conflict-detection and coordination inputs, not hidden filesystem
ACLs. Mutation behavior on overlap must be an explicit policy (`warn`, `deny`,
or similar) rather than an accidental lock.

### 7.7 Persistence

Use SQLite outside the Git worktree, partitioned by stable workspace identity.
Do not create operational databases inside repositories by default.

SQLite covers Work Items, claims, leases, checkpoints, and a bounded append-only
event trail. Transactions and uniqueness constraints enforce claim
exclusivity. This does not require a full event-sourced architecture.

### 7.8 Checkpoints

A checkpoint captures enough evidence for another client to continue:

- Work Item/claim identity;
- Git HEAD, branch, and worktree identity;
- changed paths and Git status summary;
- hooks/checks executed and outcomes;
- a short actor-supplied structured note;
- timestamp.

Do not persist arbitrary chat transcripts or model chain of thought.

## 8. Worktree integration

Worktrunk remains the preferred worktree-lifecycle authority when enabled. It is
designed for parallel AI-agent worktrees and already provides creation,
switching, removal, status, hooks, and higher-level workflow automation.

`coding-tools-mcp` should provide an adapter, not a competing worktree manager.
Candidate operations include binding a Work Item to an existing Worktrunk
worktree, creating/selecting one through `wt` when policy permits, reporting its
status, and clearing bindings only after Worktrunk confirms cleanup.

If Worktrunk is absent, Work Items remain usable without worktree automation.
Native Git-worktree fallback is a separate design decision and must not silently
compete with Worktrunk ownership.

## 9. MCP gateway

### 9.1 Boundary

`coding-tools-mcp` acts as an MCP server toward hosts and an MCP client toward
configured downstream servers. This is product-level composition, not a magic
MCP `mount` primitive.

### 9.2 Upstream registry

Each configured upstream owns:

```text
id / namespace
transport
argv OR URL
workdir policy
environment/header secret references
protocol negotiation policy
timeout/lifecycle policy
exposure allow/deny rules
read-only/security policy
auto-enable conditions
```

Upstream IDs/namespaces are local trusted configuration identities, not values
accepted from a downstream server as authority.

### 9.3 Transports

V1 supports:

- stdio MCP upstreams;
- Streamable HTTP MCP upstreams.

Legacy HTTP+SSE is not a new requirement because MCP 2026 deprecates it.

### 9.4 Protocol-era adapter

Hide downstream differences behind one client abstraction:

```text
GatewayUpstreamClient
  |-- Modern2026Adapter
  `-- LegacyInitializeAdapter
```

The outer server's protocol era remains independent from each downstream era.

### 9.5 Namespaces and collisions

Every proxied primitive is namespaced. Never flatten upstream names into the
native catalog.

```text
mise.run_task
moon.get_tasks
context7.get_library_docs
github.issue_read
playwright.browser_navigate
```

If client/tool-name restrictions require an encoded form such as
`mise__run_task`, the internal canonical identity remains
`(upstream_id, primitive_kind, remote_name)`.

### 9.6 Exposure and context control

Default exposure is deny-by-default for remote write capabilities. Support:

- exact allowlists/denylists;
- upstream-native toolsets/filtering when available;
- upstream read-only mode when available;
- lazy discovery/catalog refresh;
- deterministic ordering;
- cache partitioning by upstream/auth/config context.

Do not materialize every remote tool simply because it exists. GitHub's MCP
server is useful precedent: it supports toolsets, exact tools, exclusion,
read-only mode, and dynamic discovery specifically to control capability and
context footprint.

### 9.7 Catalog changes

For modern upstreams, invalidate affected discovery/tool caches when supported
catalog-change notifications arrive. Legacy servers use bounded refresh policy.

The current v0.3 contract explicitly promises a fixed tool catalog and no
dynamic `tools/list_changed`. Therefore proxied tools require an explicit
vNext/profile boundary before they become externally visible; the gateway must
not silently violate v0.3.

### 9.8 Cancellation, lifecycle, and credentials

Outer cancellation propagates downward when supported. Stdio child ownership
and shutdown are explicit. HTTP cancellation follows the negotiated protocol
era.

Credentials are isolated per upstream. A GitHub token is never inherited by a
Context7 or Playwright process. Discovery/catalog caches are partitioned when
credential scope can change visible capabilities. Downstream annotations and
self-reported identity are descriptive, not trusted authorization facts.

### 9.9 Packaging

The project currently keeps the official `mcp>=2.0` Python package in its dev
dependencies. Gateway code should prefer the official MCP client rather than
reimplementing all client-era negotiation locally.

V1 preference: expose gateway support as an optional installation capability
(`gateway` extra or equivalent) so the existing lightweight core remains usable
without the client dependency.

## 10. Recommended upstream integrations

### 10.1 mise MCP

Mise exposes an experimental stdio server through `mise mcp`, with resources
for tools, tasks, environment, and configuration plus `run_task`. Consume that
interface instead of cloning mise semantics. Failure must degrade cleanly
because mise marks the MCP feature experimental.

### 10.2 moon MCP

Moon exposes `moon mcp` over stdio with project/task queries, changed-file
queries, templates, and project/workspace synchronization. Moon v2 documents
its MCP implementation as protocol `2025-11-25`, making it an ideal real-world
legacy-upstream compatibility fixture.

### 10.3 Context7

Context7 supports local stdio and remote Streamable HTTP. It is a useful
documentation/research upstream and a concrete test for secret header handling.
Keep it optional.

### 10.4 GitHub MCP

GitHub's official server provides local/remote deployment, toolsets, exact tool
selection, exclusions, read-only mode, and dynamic toolset discovery. Prefer
those controls, then apply the gateway's own exposure ceiling on top.

### 10.5 Playwright MCP

Playwright MCP provides structured browser automation through accessibility
snapshots over stdio or HTTP. It is useful for frontend testing and interactive
investigation but should not auto-enable for every repository because its tool
and context footprint is significant. Microsoft itself notes that CLI+skills
can be more token-efficient for many coding-agent workflows, while MCP remains
valuable for iterative stateful browser reasoning.

## 11. Code intelligence and diagnostics

Text search is necessary but insufficient for large repositories. The runtime
should add structured semantic code intelligence without turning itself into a
language server.

### 11.1 LSP adapter

Language Server Protocol already standardizes definition, references, hover,
document/workspace symbols, diagnostics, code actions, formatting, rename,
call hierarchy, and type hierarchy.

Candidate read-oriented MCP tools:

- `code_symbols`
- `code_definition`
- `code_references`
- `code_hover`
- `code_diagnostics`

V1 should prioritize comprehension and diagnostics. Mutating LSP operations
such as rename or code actions require a separate atomicity/workspace-edit
design because `apply_patch` is currently the only direct write primitive.

The adapter may speak LSP directly to configured language servers or proxy a
trusted LSP-focused MCP server. It must respect document synchronization and
capability negotiation instead of assuming every language server behaves the
same way.

### 11.2 Structured verification

After hooks prove stable, add a higher-level read/execute primitive:

```text
verify_changes
```

It runs configured verification gates against the changed scope and normalizes
results instead of making the model parse thousands of terminal lines.

```json
{
  "status": "failed",
  "checks": [
    {"name": "ruff", "status": "passed"},
    {
      "name": "pytest-focused",
      "status": "failed",
      "failures": [
        {"file": "tests/test_runtime.py", "line": 42, "message": "..."}
      ]
    }
  ]
}
```

Do not invent parsers where mise, moon, or test frameworks already provide
machine-readable output. Normalized results should retain raw command-output
references for audit and debugging.

## 12. `workspace_context`

Add a read-only aggregation tool to reduce repeated bootstrap calls in new
stateless conversations:

```text
workspace_context(workdir?)
```

It returns bounded structured information already owned by runtime services:

- canonical workspace and selected project/workdir;
- branch, HEAD, dirty/staged/unstaged/untracked counts;
- applicable instruction and skill metadata;
- runtime/server version and supported protocol eras;
- child toolchain/environment diagnostics without secrets;
- configured/active hooks;
- relevant active Work Item claims;
- configured gateway upstream health/capability summary;
- Worktrunk binding if present.

It is an aggregation view, not a second source of truth. Underlying services
remain callable when detailed output is needed.

## 13. Operational state and storage

### 13.1 State categories

Keep state categories explicit:

```text
ephemeral process state
  active subprocess handles / upstream stdio processes

retained runtime state
  bounded command output / recovery metadata

durable workspace operational state
  Work Items / claims / leases / checkpoints / hook summaries

cache state
  upstream discovery/catalog cache
```

These categories may have different TTLs and storage backends. Do not force all
state into SQLite when process-local ownership is already correct.

### 13.2 Stable workspace identity

Durable state is partitioned by canonical workspace identity, not cwd or
transport session. The implementation plan must define repository moves,
symlinks, bare repos, and multiple worktrees before persistence ships.

### 13.3 Multi-replica note

MCP 2026 allows requests to land on arbitrary server instances, but that does
not make local SQLite or process state distributed. V1 may explicitly remain a
single-runtime-per-workspace deployment contract. Multi-replica mode requires a
shared state backend plus ownership/recovery semantics and is future scope.

## 14. Security model

### 14.1 Trust layers

Keep four trust layers separate:

1. deployment/global policy;
2. trusted workspace configuration;
3. authenticated client/principal;
4. downstream upstream-server policy/credentials.

Workspace config may request behavior but cannot exceed deployment ceilings.

### 14.2 Hooks

Hooks use the same workspace confinement, timeout, output caps, environment
sanitization, permission policy, and Landlock enforcement as normal commands.
Automatic execution does not bypass command safeguards.

### 14.3 Upstreams

Upstreams are explicitly configured and deny-by-default for writes. Remote
write tools require both upstream authorization and local exposure policy.
Secret values are scoped to their target upstream. Unknown tool annotations
are treated as untrusted hints.

### 14.4 Work claims

Claim authority comes from the validated opaque lease credential plus workspace
authorization. Labels such as `chatgpt` or `codex` are not identities with
security authority.

### 14.5 Prompt-injection boundary

Content returned by downstream MCP servers is external input. The gateway must
not promote downstream text into trusted server instructions. Proxied content
remains attributable to its upstream and subject to normal output limits.

## 15. Compatibility with current v0.3

The implementation is additive by default.

Without `.coding-tools-mcp.toml`, global feature enablement, or optional gateway
dependencies, existing 22-tool v0.3 behavior remains unchanged except for
independent correctness fixes such as child-environment isolation.

The current `SPEC.md` guarantees a fixed catalog and no dynamic
`tools/list_changed`. Exposing proxied tools therefore requires an explicit
version/profile boundary. Acceptable implementation strategies include:

- a new runtime contract version with an intentionally extensible catalog; or
- a gateway entrypoint/profile distinct from the v0.3 fixed-catalog endpoint.

Do not silently break the existing fixed-tool guarantee.

## 16. Proposed delivery phases

### Phase 0 - prerequisite runtime correctness

- fix bootstrap `VIRTUAL_ENV`/`UV_*` leakage into workspace commands;
- centralize child environment construction for commands and future hooks;
- preserve current tests and v0.3 compatibility.

### Phase 1 - configuration foundation

- typed `.coding-tools-mcp.toml` schema;
- global/workspace precedence;
- secret references and redaction;
- config validation/status;
- workspace trust/security ceilings.

### Phase 2 - Hook Engine

- `after_patch` first;
- structured argv execution and file matching;
- reuse command lifecycle and bounded output;
- structured applied-vs-verified result;
- recursion prevention and hook audit summaries.

### Phase 3 - Work coordination

- SQLite workspace state;
- Work Items;
- atomic exclusive claims;
- lease expiry/renew/release;
- path scopes;
- checkpoints;
- Worktrunk adapter.

### Phase 4 - MCP gateway

- optional official MCP client dependency;
- stdio and Streamable HTTP upstreams;
- modern `server/discover` plus legacy initialize negotiation;
- namespaces and exposure policy;
- discovery/catalog cache;
- mise and moon as first integration fixtures;
- Context7 as HTTP fixture.

### Phase 5 - extended development intelligence

- GitHub/Playwright integration recipes;
- `workspace_context`;
- structured `verify_changes`;
- read-only LSP adapter.

### Later, explicitly not V1

- MCP Tasks adapter when extension/SDK support matures;
- distributed multi-replica Work state;
- external agent runners;
- richer dashboards/MCP Apps;
- cross-workspace orchestration.

## 17. Verification strategy

### 17.1 Configuration tests

- precedence and global security ceilings;
- invalid TOML/schema diagnostics;
- secret resolution/redaction;
- workspace config cannot weaken deployment policy;
- deterministic status output.

### 17.2 Hook tests

- exact matcher behavior;
- patch applies exactly once even when a hook fails;
- hook output uses normal command retention;
- no recursive trigger loops;
- timeout/cancellation behavior;
- repository toolchain wins over server bootstrap venv;
- Landlock/permission policy matches normal command execution.

### 17.3 Work tests

- concurrent claim race has exactly one winner;
- lease expiry makes work reclaimable;
- stale lease cannot update or complete work;
- actor metadata never substitutes for lease authority;
- dependency/status-transition validation;
- SQLite restart recovery;
- state separation across two workspaces.

### 17.4 Gateway contract tests

Provide hermetic fixture MCP servers for:

- modern 2026 `server/discover`;
- legacy `initialize`;
- stdio and Streamable HTTP;
- name collisions;
- changing catalogs;
- auth-context-specific catalogs;
- downstream timeout/cancellation;
- malicious/untrusted annotations;
- secret isolation.

Core tests must not require public internet or real GitHub/Context7 credentials.

### 17.5 Optional integration smoke tests

Opt-in jobs may exercise installed versions of:

- `mise mcp`;
- `moon mcp`;
- Context7 only when network/credentials are explicitly enabled;
- GitHub MCP in read-only mode;
- Playwright MCP against a local deterministic page.

## 18. Acceptance criteria

The design is successfully implemented when:

1. Existing v0.3 clients remain compatible with vNext features disabled.
2. Repository config can define safe hooks/upstreams without storing secrets.
3. Editing a matched file through `apply_patch` can automatically run a
   configured linter/checker with an unambiguous applied-vs-verified result.
4. Two independent clients can coordinate a Work Item with an atomic claim and
   expiring lease without relying on MCP sessions.
5. The server can proxy at least one modern and one legacy upstream through
   stable namespaces and exposure policy.
6. Mise and moon semantics are consumed through their MCP interfaces instead of
   reimplemented locally.
7. Downstream credentials do not leak across upstreams, hooks, command output,
   or model-visible configuration.
8. No API claims to create another ChatGPT/Codex/Claude conversation.
9. Tests cover restart/reconnect behavior for durable handles.
10. Gateway exposure has an explicit contract boundary and does not silently
    break the v0.3 fixed catalog.

## 19. Source references

Primary/official sources consulted for this design:

- MCP 2026-07-28 GA announcement:
  <https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/blog/content/posts/2026-07-28-spec-ga/index.md>
- MCP 2026-07-28 schema (`server/discover`, capabilities/extensions):
  <https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts>
- MCP 2026 Streamable HTTP transport, cancellation, and routing headers:
  <https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx>
- MCP tools specification:
  <https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx>
- MCP TypeScript SDK protocol-version negotiation:
  <https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/protocol-versions.md>
- MCP TypeScript SDK gateway example:
  <https://github.com/modelcontextprotocol/typescript-sdk/blob/main/examples/gateway/README.md>
- MCP Python SDK v2 notes, including the current Tasks-extension limitation:
  <https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md>
- MCP Tasks reference extension status:
  <https://github.com/modelcontextprotocol/ext-tasks>
- mise MCP docs and CLI reference:
  <https://mise.jdx.dev/mcp.html>
  <https://mise.jdx.dev/cli/mcp.html>
- moon MCP docs and v2 migration notes:
  <https://moonrepo.dev/docs/guides/mcp>
  <https://moonrepo.dev/docs/migrate/2.0>
- Context7 MCP developer docs:
  <https://github.com/upstash/context7/blob/master/docs/resources/developer.mdx>
- GitHub official MCP server:
  <https://github.com/github/github-mcp-server>
- Microsoft Playwright MCP:
  <https://github.com/microsoft/playwright-mcp>
- Language Server Protocol 3.18:
  <https://microsoft.github.io/language-server-protocol/specifications/lsp/3.18/specification/>
- Worktrunk:
  <https://github.com/max-sixty/worktrunk>
- VS Code agent-hook security/lifecycle rationale:
  <https://github.com/microsoft/vscode-docs/blob/main/docs/agents/security.md>

## 20. Decision summary

Recommended vNext direction:

```text
coding-tools-mcp
  = stable coding runtime
  + typed workspace configuration
  + deterministic server-side hooks
  + durable Work coordination
  + optional MCP gateway
  + optional semantic code/verification adapters
```

The server remains an operational substrate for whatever client is currently
driving it. It coordinates and exposes capabilities; it does not pretend to be
the host, the model, or another chat session.
