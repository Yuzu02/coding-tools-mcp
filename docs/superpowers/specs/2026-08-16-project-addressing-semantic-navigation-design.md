# Project Addressing + Semantic Navigation Design

**Date:** 2026-08-16
**Status:** Architecture approved and revalidated; Phase 0 extension foundation is implemented and verified, Phase A project addressing is ready for implementation
**Target:** fork-owned multi-project and semantic extensions composed through the internal ExtensionHost

**Repository validation snapshot (2026-08-17, refreshed after Phase 0):**

- fork `main`: `f5bf954`, synchronized on top of current `xyTom/main`
- current original upstream `xyTom/main`: `66b3f19`
- fork/upstream relation at validation time: `xyTom/main...main = 0 18`; upstream is an ancestor of fork `main`
- Phase 0 is implemented: layered TOML config, static extension registry/DAG, typed services, contributions/decorators, `ExtensionHost`, mother-core bridge, and the first `projects` extension are live and verified
- current operational core remains single-workspace per handler invocation: `Runtime`, `WorkspaceCommandManager`, and `Workspace` still expose one active workspace state, which Phase A must generalize without mutating shared request state
- current `ProjectCatalog` uses display-path-derived `project_id` values (`"."` or relative paths), confirming the need to separate stable configured identity from structural discovery
- current default composed catalog exposes 22 tools; disabling the `projects` extension exposes 20, proving startup composition is already active
- Phase 0 acceptance was re-run on this snapshot: extension tests 78/78, project/skills regression 16/16, full compliance 128/128, `mise run verify`, schema drift, dispatch-input, Ruff, mypy, integration, npm launcher, public/private config boundary, and upstream-bridge compatibility all pass

## 1. Objective

Move Coding Tools MCP from a single-workspace runtime to one stateless MCP endpoint that can address multiple configured projects explicitly, and add first-class semantic code navigation without exposing Serena's project/session model to MCP clients.

This design is implemented **after** [`2026-08-17-extension-architecture-config-design.md`](2026-08-17-extension-architecture-config-design.md). `projects` and `semantic` are internal fork extensions, not feature-specific mutations spread through the mother core.

The target deployment model is:

```text
systemd
  └── coding-tools-mcp.service
        ├── one MCP endpoint
        ├── global project registry
        └── semantic workers/backends created lazily per project
```

A client never selects a mutable "current project". Every project-scoped request carries a stable `project_id`.

## 2. Core invariants

1. The MCP endpoint remains stateless from the client's perspective.
2. No request depends on a previous `activate_project`, `cd`, session-local cwd, or current-project mutation.
3. The namespace for project-local resources is `(project_id, relative_path)`.
4. `project_id` is a stable configured identifier, not a filesystem path and not derived from the current directory name.
5. All paths supplied to project-scoped tools are resolved relative to the selected project root and must remain inside that root after canonicalization.
6. One Coding Tools MCP process may keep internal caches, project metadata, command state, language-server processes, and Serena workers. Internal state is permitted as long as request semantics are explicit and concurrency-safe.
7. Serena is an implementation detail behind a Coding Tools semantic adapter. Serena tool names, schemas, project activation semantics, and editing tools are never part of the public Coding Tools MCP contract.
8. `apply_patch` remains the structured direct-edit primitive for agent-authored source changes. `exec_command` may continue to modify a selected project through canonical workspace tools such as formatters, generators, package managers, migrations, and autofix workflows. Semantic navigation is read-only in the first phase.

### 2.1 Relationship to the broader vNext design

This document is authoritative for **project addressing** and **semantic navigation**. It refines the broader `2026-08-16-development-runtime-gateway-hooks-work-coordination-design.md` where that document still assumes one runtime per workspace.

Where the two designs conflict, this document makes these narrower replacements:

1. `single-runtime-per-workspace` becomes one runtime serving multiple explicitly registered projects that share the same deployment trust policy.
2. Durable per-project state should key by stable configured `project_id` plus deployment/state namespace, not by canonical root path alone. Repository moves therefore do not silently create a new logical project.
3. The broad future LSP adapter becomes the backend-neutral `SemanticBackend` contract defined here, with Serena as the initial implementation.
4. `cross-workspace orchestration` remains a non-goal: one request targets exactly one `project_id`; no semantic or mutation request spans projects implicitly.

Other vNext areas—configuration layering, hooks, Work Items, gateway policy, authentication, telemetry, and permission ceilings—remain unchanged unless a later design explicitly revises them.

### 2.2 Fork/upstream synchronization constraint

This fork is upstream-syncable relative to the original `xyTom/coding-tools-mcp` mainline; it is not constrained to remain behavior-identical or purely additive.

Before implementation or release work for this design:

- synchronize the latest original `xyTom/main` through the repository's established `sync/upstream-main` integration workflow;
- resolve conflicts deliberately, preserving fork architecture where divergence is intentional;
- keep fork-only capabilities behind the internal extension boundary so routine upstream merges remain reviewable;
- prefer adapting existing upstream primitives over duplicating them;
- rerun bridge compatibility tests, relevant upstream tests, and fork extension tests after every upstream integration.

The 2026-08-17 validation confirms `66b3f19` is already integrated and the fork is exactly four commits ahead at `a5173ab`.

### 2.3 Extension architecture prerequisite

Project addressing and semantic navigation are consumers of the internal ExtensionHost defined by the Extension Architecture + TOML Configuration design.

```text
ExtensionHost
    ├── projects extension
    │     ├── ProjectRegistry service
    │     ├── list_projects / resolve_project contributions
    │     └── project-addressing decorators for mother-core tools
    │
    └── semantic extension
          ├── requires projects
          ├── consumes ProjectRegistry
          ├── SemanticBackend service
          └── semantic tool contributions
```

The implementation must not reintroduce direct extension-specific mutation of `TOOL_REGISTRY`, runtime monkey-patching, or feature-specific conditionals scattered across mother-core dispatch.

## 3. Project identity and configuration

### 3.1 Stable IDs

Projects are explicitly registered inside the `projects` extension configuration. Phase 0 established that extension-owned configuration must remain under `[extensions.<name>]`; Phase A therefore does not introduce a parallel top-level `[projects]` namespace:

```toml
[extensions.projects.registry.coding-tools]
root = "/srv/projects/coding-tools-mcp"

[extensions.projects.registry.app]
root = "/srv/projects/app"

[extensions.projects.registry.api]
root = "/srv/projects/api"
```

The table key is the canonical `project_id`.

Requirements for `project_id`:

- non-empty
- stable across server restarts
- unique in one server configuration
- recommended grammar: `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`
- must not be interpreted as a path
- may be renamed only as an explicit configuration migration

Moving a project root on disk does not change its ID.

### 3.2 Registry model

The `projects` extension introduces a process-wide immutable `ProjectRegistry` built at startup from layered TOML configuration.

Conceptual record:

```python
@dataclass(frozen=True)
class RegisteredProject:
    project_id: str
    root: Path
    markers: tuple[str, ...]
    available: bool
    warnings: tuple[str, ...]
```

`ProjectRegistry` owns identity and root lookup. Existing `ProjectCatalog` continues to own structural discovery inside a registered project: main project markers, nested subprojects, and scope-chain resolution.

Identity and structure are intentionally distinct:

```text
ProjectRegistry
  coding-tools -> /srv/projects/coding-tools-mcp
                     │
                     ▼
                ProjectCatalog
                     │
                     ├── main project
                     └── nested subprojects / scope chain
```

The current behavior where `ProjectRecord.project_id` can effectively be a display path should be separated from the configured stable ID. Nested discovered units should use a separate `scope_id` or `display_root`; they must not shadow the top-level registered `project_id`.

### 3.3 Per-project runtime state

Multi-project routing must also separate the state that is workspace-local today. A global registry alone is insufficient because the current implementation keeps command ownership, project context, skills, patch baselines, and caches on one `Runtime`/workspace.

Introduce an internal `ProjectRuntime` (exact class name may vary) owned by the `projects` extension:

```text
RegisteredProject
      │
      ▼
ProjectRuntime
      ├── Workspace/path resolver
      ├── WorkspaceCommandManager
      ├── ProjectContext
      ├── ProjectCatalog
      ├── SkillCatalog
      ├── patch baselines/locks
      └── project-local caches
```

It does **not** own MCP transport, authentication, protocol negotiation, or the global ExtensionHost. Those remain process-wide.

Project runtimes may be created lazily, but the configured `ProjectRegistry` is immutable after startup. Command handles are globally routable through an internal `command_id -> project_id` ownership index, while `client_request_id` idempotency remains keyed by `(project_id, client_request_id)`.

Phase A reuses the existing mother-core tool implementations rather than copying them into the extension. The implementation introduces a generic workspace-runtime state/lease seam in the mother core and a concurrency-safe scoped binding used only while one explicit request is executing. A `ProjectRuntime` owns one such state plus its project-local catalogs/context. A projects-extension decorator resolves `project_id`, binds the corresponding workspace state, invokes the existing core handler, and always restores the previous binding. It must never implement routing by assigning to shared `Runtime.workspace`, `Runtime.command_manager`, patch state, runtime directories, or another process-wide mutable "current project" field.

The scoped binding is an internal dependency-injection mechanism, not client-visible session state: its value is derived exclusively from the current tool arguments, is reset at the end of the call, supports nested calls safely, and must be isolated across concurrent threads/tasks by tests.

## 4. Public project tools

### 4.1 `list_projects`

Global, read-only, idempotent. Takes no `project_id`.

Purpose: enumerate configured projects available through the endpoint.

Suggested input:

```json
{}
```

Suggested output:

```json
{
  "projects": [
    {
      "id": "coding-tools",
      "root": "/srv/projects/coding-tools-mcp",
      "markers": [".git", "pyproject.toml"],
      "available": true,
      "warnings": []
    }
  ],
  "project_count": 1
}
```

Notes:

- Root is operator metadata and may be omitted/redacted later for remote/untrusted modes if necessary.
- The ID is the value clients persist and send on later calls.
- Missing roots do not crash startup if configuration policy allows unavailable projects; they are listed with `available=false` and a bounded warning. The default policy should fail startup on malformed/duplicate IDs but tolerate a configured path that temporarily does not exist only if explicitly enabled.

### 4.2 `resolve_project`

Global, read-only, idempotent. Does not require `project_id` because its purpose is discovery.

The initial contract accepts one absolute path and resolves it to the configured project that contains it. The registry performs longest-root matching and the selected project's `ProjectCatalog` resolves its structural scope.

Suggested schema:

```json
{
  "path": "/absolute/path/inside/a/configured/project"
}
```

A future extension may add explicit `project_id` + relative-path validation, but that is deliberately outside the first public contract to keep discovery unambiguous.

Suggested output:

```json
{
  "project_id": "coding-tools",
  "project_root": "/srv/projects/coding-tools-mcp",
  "relative_path": "coding_tools_mcp/server.py",
  "scope_chain": [
    {
      "scope_root": ".",
      "kind": "main",
      "markers": [".git", "pyproject.toml"]
    }
  ]
}
```

Error cases must be typed and deterministic:

- `PROJECT_NOT_FOUND`: path is not inside a configured project
- `PROJECT_UNAVAILABLE`: configured project root cannot be accessed
- `INVALID_PROJECT_PATH`: path cannot be canonicalized safely

## 5. Project-scoped tool contract

All tools that operate on a project filesystem or execute against a project require `project_id`.

Initial required set:

```text
read_file
list_dir
list_files
search_text
list_skills
read_skill
apply_patch
exec_command
git_status
git_diff
git_log
git_show
git_blame
view_image
```

The new semantic tools also require it:

```text
list_symbols
find_symbol
find_definition
find_references
```

Command lifecycle tools do not need `project_id` when an opaque handle already embeds/owns project identity:

```text
write_stdin
kill_command
read_output
```

`get_command` has two addressing modes:

- `command_id`: no `project_id` is needed because the opaque command handle owns project identity.
- `client_request_id`: `project_id` is required because idempotency keys are project-scoped.

`list_commands` gains an optional `project_id` filter.

Because `client_request_id` is project-scoped, `list_commands(client_request_id=...)` also requires `project_id` semantically. A bare `list_commands` with neither field aggregates retained command metadata across projects without creating mutable current-project state.

Command records must internally persist `project_id`. Idempotency bindings are keyed by `(project_id, client_request_id)`, preserving today's workspace-local semantics when several former workspaces share one process. A `client_request_id` reused in project A and project B is therefore independent, while reuse inside one project retains the existing fingerprint/conflict behavior.

`check_exec_environment` becomes project-scoped when the `projects` extension is enabled because its effective workspace/runtime/sandbox diagnostics depend on the selected project. It therefore requires `project_id` in the multi-project contract.

Global/server tools remain unscoped:

```text
server_info
list_projects
resolve_project
request_permissions
```

`request_permissions` receives and validates the same target arguments as the protected operation, so project-scoped permission requests indirectly include the target `project_id` inside `arguments`.

### 5.1 Project-neutral server instructions

The current single-workspace runtime injects one workspace's `ProjectContext.server_instructions()` into `initialize`/`server/discover`. A multi-project endpoint must not concatenate or implicitly choose one project's AGENTS/CLAUDE instructions.

When `projects` is enabled, handshake/discovery instructions are project-neutral and explain the explicit addressing flow. Project-local instructions remain discoverable through `list_skills(project_id, workdir)` and the returned instruction-file paths, which clients may read with project-scoped `read_file` before modifying that scope.

No previous request chooses which project's instructions apply.

## 6. Path and sandbox semantics

The server resolves every project-scoped call as:

```text
project_id
   ↓
ProjectRegistry.require(project_id)
   ↓
registered project root
   ↓
relative tool path/workdir
   ↓
canonical path validation
   ↓
operation
```

No project-scoped tool may fall back to the server process cwd or a previous request's workdir.

Existing `path` and `workdir` arguments remain explicit **project-relative** addressing fields. Adding `project_id` does not remove or weaken current explicit-workdir behavior and does not introduce a mutable cwd.

A path is rejected when:

- it resolves outside the registered project root
- it traverses through an unsafe symlink to another project/root
- it refers to a missing root when the operation requires an existing path

The Landlock/exec policy should be derived from the selected registered project root for that execution rather than from one process-wide workspace root.

## 7. Semantic navigation public API

The first semantic release is strictly read-only.

### 7.1 `list_symbols`

Purpose: return a bounded structural outline for a file.

Suggested input:

```json
{
  "project_id": "coding-tools",
  "path": "coding_tools_mcp/server.py",
  "depth": 1,
  "max_results": 500
}
```

Suggested normalized symbol:

```json
{
  "name": "Runtime",
  "name_path": "Runtime",
  "kind": "class",
  "path": "coding_tools_mcp/server.py",
  "range": {
    "start": {"line": 1500, "column": 0},
    "end": {"line": 4300, "column": 0}
  },
  "children": []
}
```

Coding Tools owns this normalized response model. Backend-specific fields may be placed under an optional bounded `backend_metadata` object only if required for troubleshooting; they are not part of stable semantics.

### 7.2 `find_symbol`

Purpose: semantic search for declarations by name/name-path.

Suggested input:

```json
{
  "project_id": "coding-tools",
  "query": "Runtime/exposed_tool_names",
  "path": "coding_tools_mcp",
  "include_body": false,
  "max_results": 50
}
```

The public argument is named `query`, not Serena's exact parameter naming, so the backend remains replaceable.

### 7.3 `find_definition`

Purpose: resolve the declaration/definition of the identifier at a concrete source position.

Suggested input:

```json
{
  "project_id": "coding-tools",
  "path": "coding_tools_mcp/server.py",
  "line": 1859,
  "column": 20
}
```

Position semantics are explicitly 1-based line and 1-based column at the MCP boundary. The adapter translates to backend/LSP indexing as needed.

Suggested output:

```json
{
  "definitions": [
    {
      "name": "server_info_payload",
      "kind": "method",
      "path": "coding_tools_mcp/server.py",
      "range": {
        "start": {"line": 1755, "column": 5},
        "end": {"line": 1800, "column": 1}
      }
    }
  ]
}
```

### 7.4 `find_references`

Purpose: locate semantic references to a symbol selected at a source position.

Suggested input:

```json
{
  "project_id": "coding-tools",
  "path": "coding_tools_mcp/server.py",
  "line": 1755,
  "column": 10,
  "include_declaration": false,
  "max_results": 500
}
```

Suggested result item:

```json
{
  "path": "coding_tools_mcp/server.py",
  "range": {
    "start": {"line": 1860, "column": 16},
    "end": {"line": 1860, "column": 35}
  },
  "containing_symbol": {
    "name": "server_info",
    "name_path": "Runtime/server_info",
    "kind": "method"
  }
}
```

## 8. Semantic backend abstraction

Introduce a Coding Tools-owned interface independent of Serena.

Conceptual interface:

```python
class SemanticBackend(Protocol):
    def list_symbols(self, project: RegisteredProject, request: ListSymbolsRequest) -> ListSymbolsResult: ...
    def find_symbol(self, project: RegisteredProject, request: FindSymbolRequest) -> FindSymbolResult: ...
    def find_definition(self, project: RegisteredProject, request: FindDefinitionRequest) -> FindDefinitionResult: ...
    def find_references(self, project: RegisteredProject, request: FindReferencesRequest) -> FindReferencesResult: ...
    def close_project(self, project_id: str) -> None: ...
    def close(self) -> None: ...
```

The runtime depends only on `SemanticBackend`.

Backends are replaceable without changing MCP schemas.

## 9. Serena integration

### 9.1 Why Serena

Validation against Serena main on 2026-08-17 confirms that its current distribution exposes symbol lookup, symbol overview, referencing-symbol lookup, declaration/definition lookup, diagnostics, and external-project querying. Its project-query feature uses dedicated queryable-project/project-server machinery rather than requiring callers to mutate one shared active project.

Current Serena exposes the exact read-only capabilities required for this phase through its semantic/LSP tool layer:

- symbol overview
- symbol lookup
- declaration/definition lookup
- referencing-symbol lookup
- diagnostics for later expansion

Serena also supports querying projects other than a currently active project through its Project Server architecture.

### 9.2 What we do not expose

Coding Tools does not expose or proxy these Serena concepts directly:

```text
activate_project
get_current_config
Serena memories
Serena editing tools
Serena shell tools
Serena onboarding
Serena MCP schemas
```

There is no MCP-visible mutable Serena current project.

### 9.3 Integration strategy

Recommended initial implementation: **managed Serena semantic sidecar/worker layer behind `SerenaSemanticBackend`**.

Do not make the Coding Tools MCP client talk to a second MCP server.

The runtime owns the mapping:

```text
project_id -> RegisteredProject -> semantic worker/project handle
```

Workers are created lazily on the first semantic request for a project and may remain warm for reuse.

The implementation must guarantee that project A and project B requests cannot change each other's semantic context. Acceptable mechanisms, in preference order:

1. Serena Project Server/query-external-project mechanism with explicit project target per request, if its programmatic API is sufficiently stable.
2. One pinned Serena worker process per active `project_id`, never calling `activate_project` between requests.
3. Direct Serena Python API only behind a version-pinned adapter and only if it gives a cleaner explicit-project API than the process boundary.

Do **not** use one shared Serena MCP instance plus `activate_project` before each request.

`SerenaSemanticBackend` is an optional, version-pinned adapter boundary. Coding Tools must pin and test a compatible Serena range rather than depending on unversioned internal APIs. Upgrading Serena requires rerunning adapter and two-project isolation tests before changing the supported range.

### 9.4 Lifecycle

Semantic workers are lazy and bounded.

Required runtime controls:

```text
max_semantic_projects
semantic_idle_timeout_seconds
semantic_start_timeout_seconds
semantic_request_timeout_seconds
```

When the active semantic project limit is reached, evict only an idle worker. Never terminate a worker servicing an in-flight request.

Each project gets independent startup/failure state. Failure to start semantic support for project A must not break filesystem/Git tools or semantic support for project B.

### 9.5 Concurrency

Concurrent calls to the same project may share one backend worker only if the backend supports it safely. Otherwise serialize per-project semantic requests with a project-local lock while allowing different projects to run concurrently.

There is never a global semantic lock unless required during backend process creation/eviction bookkeeping.

## 10. Semantic error model

Normalize backend failures into Coding Tools errors:

```text
SEMANTIC_BACKEND_UNAVAILABLE
SEMANTIC_PROJECT_START_FAILED
SEMANTIC_LANGUAGE_UNSUPPORTED
SEMANTIC_FILE_UNSUPPORTED
SEMANTIC_SYMBOL_NOT_FOUND
SEMANTIC_POSITION_INVALID
SEMANTIC_TIMEOUT
SEMANTIC_BACKEND_ERROR
```

Every error includes:

```json
{
  "project_id": "coding-tools",
  "retryable": false,
  "backend": "serena"
}
```

Only bounded, non-secret backend evidence is surfaced. Raw subprocess environment, absolute command lines containing secrets, or unbounded LSP logs are never returned.

`SEMANTIC_TIMEOUT`, worker startup races, and transient backend process loss are retryable. Invalid positions, unsupported files, and unknown symbols are not automatically retryable.

## 11. Fallback behavior

Semantic tools must not silently degrade to lexical `search_text` and pretend the result is semantic.

If Serena/LSP cannot answer:

- return a typed semantic error
- include a suggested next action such as using `search_text` when appropriate
- never mix lexical and semantic results in the same field without an explicit future `strategy` parameter

This preserves trust in tool semantics.

## 12. Server discovery and capability reporting

`server_info` should add project and semantic metadata:

```json
{
  "projects": {
    "count": 4,
    "ids": ["coding-tools", "app", "api", "web"]
  },
  "semantic": {
    "enabled": true,
    "backend": "serena",
    "tools": ["list_symbols", "find_symbol", "find_definition", "find_references"]
  }
}
```

The ExtensionHost-composed tool registry is the single source of truth for exposed tools and annotations.

Semantic tools are:

```text
read_only = true
idempotent = true
open_world = false
```

They are gated by semantic backend availability/configuration so a server can run without Serena installed.

The current v0.3 implementation is the starting mother-core contract, not a permanent restriction on the fork. Enabling `projects` and `semantic` may deliberately evolve the fork's schemas and tool catalog. Within one running process, however, the ExtensionHost composes and freezes the exposed catalog at startup; backend failure at runtime returns typed semantic errors rather than mutating that catalog.

## 13. One-unit deployment model

The normal deployment becomes one systemd unit for all trusted projects governed by the same policy:

```text
coding-tools-mcp.service
        │
        ├── global config
        ├── project registry
        ├── MCP HTTP server
        ├── process manager
        └── semantic backend manager
```

Multiple systemd units remain valid only for genuine isolation boundaries, including:

- different Unix users
- different credentials/secrets
- different network policies
- different sandbox/trust policies
- mutually untrusted tenants
- incompatible runtime versions

Project count alone is not a reason to create another service unit.

## 14. Migration and compatibility

This is a contract-level change because existing project-scoped schemas currently assume one server workspace.

Recommended migration: introduce it as the next explicit runtime-contract revision rather than silently changing v0.3 behavior.

### 14.1 Legacy launch compatibility

A legacy single `--workspace <root>` launch may be translated internally into:

```text
project_id = "default"
root       = <root>
```

This keeps operator startup simple, but **new contract clients still send `project_id`**.

If strict backward compatibility with old clients is required during a transition window, expose it as an explicit compatibility mode rather than making `project_id` conditionally optional based on project count. Conditional schemas create ambiguous client behavior and undermine the stateless addressing model.

### 14.2 Global configuration

Project mappings live in the layered TOML model defined by the Extension Architecture + TOML Configuration design. Public composition/defaults belong in `coding-tools.toml`; actual host roots belong in ignored `coding-tools.local.toml`. Supported environment and explicit CLI overrides remain higher-precedence layers and must not create mutable current-project state.

## 15. Security consequences

Moving from one workspace per process to multiple projects expands the process's aggregate filesystem authority. The server must maintain deterministic project routing and path validation. OS-level isolation is provided only to the extent promised by the active permission mode and deployment policy; this design does not turn trusted projects into mutually hostile tenants.

Required properties:

1. Project lookup happens before any path resolution or command execution.
2. Explicit `path`, `workdir`, and `cwd` addressing fields are resolved against the selected project and may not escape it through traversal or unsafe symlinks.
3. In `safe`/`trusted`, filesystem confinement for commands is derived from the selected project plus documented runtime/toolchain allowances. In `dangerous`, Landlock and MCP safety gates remain disabled as they are today; a command string may therefore intentionally access broader host resources even though its declared project/workdir routing still resolves from `project_id`.
4. Permission grants are scoped to both operation and project-target arguments.
5. Command/output handles carry project ownership internally.
6. Telemetry includes `project_id` but never secret environment values.
7. Semantic subprocesses receive only the selected project root and required environment subset.
8. If configured project roots are nested, project-aware path resolvers do not silently retarget a parent-project request into the separately registered child project; longest-root discovery determines identity but does not mutate the caller's `project_id`. Strong OS-level prevention of an intentionally broad `dangerous` command remains outside this guarantee.
9. Root paths exposed by discovery/status are operator metadata and may be redacted in remote/untrusted profiles without changing `project_id` semantics.

## 16. Testing strategy

### 16.1 Project registry unit tests

Cover:

- stable configured IDs independent of root basename
- duplicate ID rejection
- longest-root path resolution for nested configured roots
- unknown ID
- unavailable root
- path outside every configured project
- symlink escape rejection
- nested subproject scope-chain preservation

### 16.2 Tool contract tests

For every project-scoped tool:

- schema requires `project_id`
- unknown project returns `PROJECT_NOT_FOUND`
- project A cannot read/execute against project B through relative traversal
- same relative path in two projects resolves independently
- `dangerous` tests do not assert Landlock isolation that the mode intentionally disables; they still assert deterministic project/workdir routing

Global tools must not accidentally require `project_id`.

### 16.3 Command tests

- command records store project ownership
- `list_commands(project_id=...)` filters correctly
- `get_command(command_id=...)` recovers without `project_id`
- `get_command(client_request_id=...)` requires `project_id`
- the same `client_request_id` can be used independently in project A and project B
- reuse of one `client_request_id` with a different fingerprint inside the same project still returns the existing idempotency conflict
- `read_output` and other opaque-handle operations recover project-owned resources without a current project
- reconnect/stateless command recovery still works

### 16.4 Semantic adapter contract tests

Use a fake backend first. Test normalized output and error mapping independently of Serena.

For each semantic operation:

- correct `RegisteredProject` passed to backend
- 1-based MCP positions translated correctly
- results bounded by `max_results`
- absolute backend paths normalized to project-relative paths
- backend path escape rejected
- timeout normalized
- unsupported file normalized

### 16.5 Serena integration tests

Use small fixtures for at least Python and one second language already practical in CI.

Verify:

- symbols overview
- symbol lookup
- definition lookup
- references lookup
- two projects queried concurrently without cross-project contamination
- worker restart after crash
- semantic failure leaves normal filesystem tools functional

CI should skip Serena integration tests with an explicit reason when the optional backend is unavailable; fake-backend contract tests remain mandatory.

## 17. Implementation decomposition

The implementation follows the extension foundation and then splits into two independently reviewable feature phases.

### Phase 0 — Extension Architecture + TOML Configuration

- implement [`2026-08-17-extension-architecture-config-design.md`](2026-08-17-extension-architecture-config-design.md);
- establish `coding-tools.toml` + ignored `coding-tools.local.toml` layered configuration;
- establish the static internal extension registry, dependency graph, lifecycle, service registry, tool contributions, and tool decorators;
- integrate the minimal ExtensionHost bridge with the mother core;
- prove the boundary by moving existing fork-owned project/skill discovery behind it where appropriate.

No multi-project or Serena feature implementation starts before this foundation is green.

### Phase A — Project addressing

Deliverables:

- global `ProjectRegistry`
- stable configured IDs
- `list_projects`
- `resolve_project`
- `project_id` on every project-scoped tool
- project-aware command ownership
- per-project path/exec confinement
- single-unit multi-project configuration
- updated contract/compliance tests

Implementation form: internal `projects` extension. It publishes `ProjectRegistry`, contributes project discovery tools, and decorates project-scoped mother-core operations through the extension API.

Acceptance criterion: four configured projects can be served by one MCP endpoint with no request-local/current-project state.

### Phase B — Semantic navigation

Deliverables:

- `SemanticBackend` protocol
- fake backend tests
- `SerenaSemanticBackend`
- lazy per-project worker lifecycle
- `list_symbols`
- `find_symbol`
- `find_definition`
- `find_references`
- typed semantic errors
- integration tests for project isolation/concurrency

Implementation form: internal `semantic` extension with `requires=("projects",)`. It consumes the published `ProjectRegistry` service and contributes its semantic tools without mother-core feature-specific patches.

Acceptance criterion: two projects can issue overlapping semantic requests concurrently and receive only results from their own project namespace.

## 18. Rejected alternatives

### One systemd unit per project

Rejected as the default because it duplicates MCP transport, auth, telemetry, configuration, deployment, ports, health checks, and upgrades. Keep multiple units only for distinct trust/administrative boundaries.

### Stateful `activate_project`

Rejected because it makes request behavior depend on mutable cross-request state and is unsafe under concurrent clients.

### Proxy Serena MCP schemas directly

Rejected because it exposes Serena lifecycle/configuration concepts, couples clients to its release cadence, and makes replacing the backend a protocol-breaking change.

### Implement LSP from scratch now

Rejected because Serena already supplies the required LSP-backed primitives and project-server machinery. Coding Tools should own normalization, routing, lifecycle, and policy rather than duplicating language intelligence.

### Silent regex fallback

Rejected because lexical matches are not equivalent to semantic definitions/references and would make tool results misleading.

## 19. Explicit non-goals for this iteration

- semantic edits / rename / replace-symbol-body
- Serena memory subsystem
- project activation API
- cross-project semantic references in one request
- dependency-source symbol navigation beyond what the backend naturally provides
- persistent semantic index format owned by Coding Tools
- language-server configuration UI
- dynamic addition/removal of project configuration through MCP writes

## 20. Success criteria

The design is successful when all of the following are true:

1. One service instance serves all configured projects under the same trust policy.
2. Every project-scoped request is independently reproducible from its arguments.
3. No request needs a previous project-selection call.
4. `list_projects` and `resolve_project` expose stable project addressing.
5. Filesystem/Git path addressing and command workdir routing are deterministically scoped to the selected project, while OS-level command confinement follows the configured permission mode.
6. Semantic tool schemas are Coding Tools-owned and backend-neutral.
7. Serena can be upgraded/replaced without changing the MCP contract.
8. Concurrent project A/B semantic operations cannot contaminate one another.
9. Backend failure is isolated and returned as a typed error.
10. `apply_patch` remains the structured direct-edit primitive while canonical `exec_command` workflows may mutate the selected project under runtime policy.
11. The fork remains synchronized through the explicit upstream integration workflow, with extension/core bridge conflicts localized and test-backed.
12. Project and semantic capabilities are implemented through the ExtensionHost foundation rather than scattered feature-specific mother-core edits.
