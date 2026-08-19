# Work Items + Worktree Coordination Design

**Date:** 2026-08-18  
**Status:** PROPOSED / NOT IMPLEMENTED  
**Target:** fork-owned `work` extension composed through the existing
`ExtensionHost`, with Worktrunk as the preferred worktree lifecycle provider.

This specification is the canonical design for **Work Items, claims, leases,
worktree discovery/routing, and Worktrunk integration**. It narrows and
supersedes the Work Items/worktree portions of:

- `docs/work-items-design.md`; and
- `docs/superpowers/specs/2026-08-16-development-runtime-gateway-hooks-work-coordination-design.md`.

The older documents remain useful historical/design context. Where they
conflict with this document, this document wins for Work Items and worktrees.
Hooks and Gateway remain separate proposed subsystems.

The implemented prerequisite runtime is the current v0.4 architecture:

- HostConfig v2 / ProjectConfig v1 authority;
- one same-trust MCP deployment serving multiple stable `project_id` values;
- stateless HTTP request semantics;
- project-scoped filesystem, command, Git, skill, and semantic routing;
- recoverable command handles; and
- the `projects` and `semantic` extensions.

The separate
[`2026-08-18-pre-worktree-runtime-modernization-design.md`](2026-08-18-pre-worktree-runtime-modernization-design.md)
was implemented and freshly accepted on 2026-08-19. **WT0 is therefore
unblocked.** Worktrees must still not be used as the place to fix protocol-era
drift, upstream-sync topology, `cwd`/`workdir` schema ambiguity, request
cancellation, context bloat, or provider policy seams. Those remain foundation
responsibilities, not Worktree responsibilities.

No part of this document changes the deployed v0.4 contract until a later
implementation plan is accepted and implemented.

---

## 1. Objective

Enable several independent chats, coding agents, CI jobs, or humans to work on
the same logical project in parallel without relying on conversation state,
mutable server sessions, hidden cwd, or ad-hoc filesystem conventions.

The design has two related responsibilities:

1. **Work coordination** — durable Work Items, exclusive claims, expiring
   leases, scopes, checkpoints, and audit history.
2. **Execution-root coordination** — deterministic addressing and lifecycle of
   multiple Git worktrees belonging to one stable `project_id`.

The resulting model must keep model-facing requests compact. A client should
normally need only stable handles such as:

```text
project_id
worktree_id          # optional; omitted means the registered default worktree
work_id              # when coordinating durable work
lease_token          # only when an operation requires claim authority
relative path/workdir
```

Repository paths, Git administrative paths, Worktrunk configuration, branch
metadata, state database locations, and sandbox mount paths are resolved by the
runtime rather than repeated by the model.

---

## 2. Core invariants

1. `project_id` remains the stable **logical project identity**.
2. A Git worktree is an **execution root**, not a new project.
3. Work Items belong to `project_id`, not to a filesystem checkout.
4. Omitting `worktree_id` preserves current behavior by routing to the
   HostConfig-registered project root, called the **default worktree** in this
   design.
5. The default worktree is not assumed to be a branch literally named `main`.
   A deployment may register `main`, `dev`, or another worktree as its default.
6. `worktree_id` is an opaque routing identity. It is not a branch name, path,
   directory basename, or transport-session handle.
7. Worktrunk is the preferred authority for **worktree lifecycle and workflow
   hooks**. Coding Tools does not reimplement Worktrunk.
8. Git is the final authority for **repository membership**. A path returned by
   Worktrunk is not accepted as a worktree for a project until Git proves that
   it shares that project's common repository.
9. Coding Tools owns **authorization, routing, confinement, durable Work state,
   and MCP schemas**.
10. Worktree discovery is programmatic. Clients never register arbitrary
    worktree roots as part of normal tool calls.
11. Durable state is external to Git worktrees and keyed by stable project
    identity plus deployment/state namespace.
12. Work Items and path scopes are coordination primitives, not hidden
    filesystem ACLs.
13. The feature remains useful without Worktrunk: read-only Git worktree
    discovery and Work Items still work; lifecycle automation is unavailable.
14. V1 is a single-runtime-per-deployment/state-namespace design. Distributed
    multi-replica coordination is future scope.
15. No Work API claims to spawn, own, or message another ChatGPT/Codex/Claude
    conversation.

---

## 3. Identity model

### 3.1 `project_id`: logical project identity

The existing stable configured `project_id` continues to identify the logical
project:

```text
coding-tools
application
data-pipeline
```

It owns:

- project policy;
- durable Work Items;
- Work Item scopes and dependencies;
- the default worktree selection;
- the authorized repository/worktree container ceiling;
- project-owned instructions/skills/configuration; and
- worktree-aware runtime resources.

Repository moves do not intentionally create a new logical project merely
because an absolute path changed.

### 3.2 Default worktree

The HostConfig-registered project `root` remains the compatibility default.

Conceptually:

```text
project_id="coding-tools"
```

means:

```text
project_id="coding-tools"
worktree_id="default"
```

`default` is a reserved alias, not necessarily the canonical persisted
`worktree_id` and not necessarily the Git default branch.

This rule avoids a migration merely to add worktree support and handles
existing deployments such as SICOTI where the currently registered worktree
may be `dev`.

### 3.3 `worktree_id`: physical checkout identity

Every discovered checkout receives a compact opaque identity.

The identity is derived from Git administrative identity, not from branch or
path. The conceptual input is:

```text
project_id
git_admin_key
```

where `git_admin_key` is the normalized path of the worktree-specific Git
administrative directory relative to the canonical Git common directory.

Examples:

```text
normal clone main worktree:  git_admin_key = "."
linked worktree:             git_admin_key = "worktrees/feature-auth"
bare-repo linked worktree:   git_admin_key = "worktrees/example.git.dev"
```

The public form SHOULD be a bounded opaque value such as:

```text
wt_<short deterministic digest>
```

The digest algorithm and length are implementation details but must be stable
for the lifetime of the corresponding Git administrative slot and must not
leak absolute host paths.

Branch is metadata. Worktrunk supports detached worktrees and can report
duplicate-branch conditions, so branch cannot be the identity key.

### 3.4 Repository identity

Repository identity is internal and programmatic.

For each candidate worktree, Coding Tools obtains and canonicalizes at least:

```text
git rev-parse --absolute-git-dir
git rev-parse --git-common-dir
git rev-parse --show-toplevel
```

The common directory is normalized to an absolute canonical path before
comparison.

A candidate belongs to a registered project only if:

1. Git recognizes the candidate as a worktree;
2. the candidate's canonical Git common directory equals the canonical common
   directory derived from the registered default worktree;
3. the candidate worktree root is inside a HostConfig-authorized filesystem
   ceiling; and
4. path canonicalization does not escape through symlinks.

Worktrunk output is useful discovery evidence but does not override these Git
checks.

### 3.5 `scope_id` remains separate

Existing structural `scope_id` semantics for nested subprojects remain
independent from worktree identity.

The full addressing tuple becomes:

```text
(project_id, worktree_id, scope_id, relative_path)
```

where `worktree_id` defaults deterministically and `scope_id` continues to be
derived structurally inside the selected worktree.

---

## 4. Supported repository layouts

Worktree routing must normalize more than one physical repository layout into
the identity model above.

### 4.1 Normal clone

Supported for compatibility:

```text
/repos/tools/
├── coding-tools-mcp/                  # registered/default worktree
└── coding-tools-mcp.worktrees/
    ├── feature-auth/
    └── gateway/
```

or traditional siblings:

```text
/repos/tools/
├── coding-tools-mcp/
├── coding-tools-mcp.feature-auth/
└── coding-tools-mcp.gateway/
```

The main normal-clone worktree contains the shared `.git` directory and linked
worktrees point back to `.git/worktrees/<id>`.

Normal clones remain valid, but dynamic worktree creation under a strict
systemd sandbox requires an explicitly authorized parent/arena that does not
accidentally expose unrelated repositories.

### 4.2 Bare repository with a dedicated project container — preferred

This is the preferred layout for projects expected to use parallel worktrees:

```text
/repos/tools/coding-tools-mcp/
├── .git/                 # bare repository/object store
├── main/                 # default worktree if configured so
├── hooks/
├── work-items/
└── gateway/
```

All worktrees are peers from the application's perspective; the registered
default worktree is simply the compatibility target for omitted
`worktree_id`.

This layout is preferred because one project-specific container can contain:

- the Git common repository;
- every current/future worktree; and
- Worktrunk's Git-local state.

The systemd sandbox can therefore expose one bounded project container without
opening the parent directory that contains unrelated repositories.

### 4.3 Existing bare repository with sibling worktrees — supported

Existing bare + sibling layouts are first-class:

```text
/srv/projects/example/
├── example.git/              # bare common repository
├── example.git.dev/          # linked worktree
├── example.git.feature-a/    # linked worktree
└── ...
```

A representative registered root is:

```text
/srv/projects/example/example.git.dev
```

and its `.git` gitfile points to:

```text
/srv/projects/example/example.git/worktrees/example.git.dev
```

Therefore the project container, not only the worktree root, must be visible to
the runtime for Git operations to work correctly.

### 4.4 Why bare is preferred, not required

Bare repositories are a deployment/layout preference, not an MCP protocol
requirement.

The Work API and routing API must not expose `bare=true` as a normal client
concern. The runtime detects repository topology through Git.

Projects can migrate individually when the operational benefit justifies it.
The self-hosting Coding Tools repository should not be migrated merely to land
the first implementation; support for both layouts should be proven first.

---

## 5. Worktree discovery

### 5.1 Preferred source: Worktrunk schema 2

When Worktrunk is configured and available, the adapter uses its structured
JSON interface rather than parsing the human table.

The adapter requires support for Worktrunk list JSON schema 2 and forces the
schema explicitly rather than inheriting a user default.

Conceptually:

```text
wt \
  -C <registered-default-worktree> \
  --config-set list.json-schema=2 \
  --config-set list.full=false \
  list --format=json
```

Required validation:

- top-level `schema == 2`;
- every physical worktree row has an absolute path;
- branch may be null for detached worktrees;
- locked/prunable/duplicate/mismatch state is preserved as metadata;
- `--full` is not used for ordinary discovery so CI/forge/LLM lookups are not
  pulled into the routing hot path.

Worktrunk's JSON output is a discovery/status source, not the security proof of
repository membership.

### 5.2 Git fallback

If Worktrunk is unavailable or disabled, discovery falls back to Git's stable
machine-readable format:

```text
git worktree list --porcelain -z
```

Git fallback provides routing/discovery but not Worktrunk lifecycle hooks,
merge workflow, approvals, or provider-specific status.

For a bare repository, Git's list may contain a `bare` repository record in
addition to linked worktrees. That bare record is repository metadata and is
never exposed as an execution `worktree_id`.

### 5.3 Host-owned Worktrunk executable

The Worktrunk provider is infrastructure, not a project dependency.

The adapter must not allow a target repository's `mise.toml` to silently pick,
install, or replace the Worktrunk binary used by the server.

Provider resolution therefore follows these rules:

1. resolve the provider executable from the deployment/bootstrap environment;
2. keep the provider process cwd at a stable bootstrap/runtime directory;
3. address the target repository with Worktrunk's `-C <path>` option;
4. invoke structured argv directly, never through an implicit shell; and
5. preflight the provider capability/version before enabling lifecycle tools.

This specifically avoids the observed failure mode where invoking `wt` from a
project worktree caused Mise to auto-install project-pinned tools and attempt
lockfile writes inside the hardened service environment.

### 5.4 Refresh and caching

The runtime may cache discovery for a short bounded interval, but every
operation that can mutate or rely on a specific worktree must revalidate the
selected worktree before execution.

The cache key includes at least:

```text
deployment/state namespace
project_id
repository common identity
```

The cache must be invalidated after any provider lifecycle operation and when
Git reports stale/prunable or missing administrative data.

No request correctness depends on transport-session continuity.

---

## 6. Worktree-aware routing

### 6.1 Public addressing

Every existing project-scoped operation that targets a filesystem/Git/semantic
execution root gains an optional `worktree_id` field where relevant.

Examples:

```text
read_file(project_id, worktree_id?, path)
apply_patch(project_id, worktree_id?, patch)
exec_command(project_id, worktree_id?, workdir=".", ...)
git_status(project_id, worktree_id?, workdir=".", ...)
git_diff(project_id, worktree_id?, workdir=".", ...)
list_symbols(project_id, worktree_id?, path)
find_symbol(project_id, worktree_id?, ...)
```

Omission always means the registered default worktree. No previous tool call can
change that default for a session.

`workdir` is also optional for operations that conceptually execute from a
directory. Omitting it means `"."` relative to the execution root selected by
`(project_id, worktree_id?)`; it never means process `cwd` and never depends on
a previous request.

The routing algorithm is normative:

```text
execution_root = resolve_worktree_root(
    project_id,
    worktree_id ?? registered_default_worktree,
)

effective_workdir = resolve_beneath(
    execution_root,
    workdir ?? ".",
)
```

Therefore the common case stays compact:

```text
git_status(project_id="coding-tools")
```

is equivalent to targeting the registered default worktree at its root, while:

```text
git_status(project_id="coding-tools", worktree_id="wt_abc123")
```

targets the root of that authorized worktree. `workdir` is supplied only when a
subdirectory is semantically required.

`workdir` is never a project/worktree selector. `..`, symlink traversal, or any
other resolution that escapes the selected execution root is rejected rather
than retargeting the request. This preserves explicit project/worktree
isolation while removing redundant arguments from ordinary calls.

Internally all filesystem, Git, command, skill/instruction, patch, and semantic
entry points should consume one canonical `ExecutionTarget`/resolver service
rather than reimplementing defaulting and path checks independently. The exact
type name is implementation-defined; the invariant is a single source of truth
for `(project_id, worktree_id?, workdir?) -> canonical execution root/workdir`.

### 6.2 Compact discovery API

`list_projects` remains compact and does not inline every worktree in every
project. It may expose only bounded capability metadata such as:

```text
worktrees_available
default_worktree_id
provider
```

Detailed worktree inventory belongs in a dedicated bounded tool:

```text
worktree_list(project_id)
```

This prevents ordinary project discovery from growing with the number of
parallel agents.

### 6.3 `resolve_project`

Absolute-path resolution becomes worktree-aware.

For a path inside an authorized linked worktree it returns at least:

```text
project_id
worktree_id
worktree_root
relative_path
scope_chain
```

Longest-root resolution may discover the physical target, but it never changes
the `project_id` explicitly supplied to another tool.

### 6.4 Runtime state keys

Any runtime resource whose semantics depend on checked-out files must include
worktree identity in its key.

At minimum:

```text
commands/recovery:       (project_id, worktree_id, command_id)
patch baseline/context:  (project_id, worktree_id)
semantic workers:        (project_id, worktree_id)
skill/instruction view:  (project_id, worktree_id, scope_id)
Git operations:          (project_id, worktree_id, workdir)
```

This prevents two agents working on different branches from accidentally
sharing a semantic worker or filesystem-relative cache that belongs to another
checkout.

### 6.5 Semantic worker ceiling

Semantic backends must remain bounded when many worktrees exist.

The semantic runtime should treat each active `(project_id, worktree_id)` as a
separate semantic root, while preserving the existing lazy creation, idle
timeout, serialization, and LRU principles.

HostConfig must provide a bounded ceiling for concurrently warm semantic
worktree roots. Merely discovering 20 worktrees must not start 20 Serena
workers.

---

## 7. Worktrunk lifecycle adapter

### 7.1 Authority split

The adapter boundary is strict:

```text
Worktrunk  -> create/select/remove/merge worktrees and run Worktrunk hooks
Git        -> prove repository/worktree identity and current Git state
Coding MCP -> authorize, confine, expose MCP tools, persist Work coordination
```

Coding Tools must not duplicate Worktrunk's branch/worktree lifecycle logic.

### 7.2 Provider operations

The internal provider interface should cover at least:

```text
discover(project)
create(project, branch, base?)
remove(project, worktree, force=false)
merge(project, worktree, target?, options?)
approval_state(project)
```

V1 public exposure may be narrower than this internal interface.

Creation uses Worktrunk's automation-oriented JSON mode and avoids shell
directory switching, conceptually:

```text
wt -C <default-root> switch --create <branch> --base <base?> --no-cd --format=json
```

Existing branches may also be materialized through `wt switch` without
`--create`.

Removal and merge use Worktrunk's structured JSON modes. Forceful removal must
never be the default.

### 7.3 Worktree path policy

Worktree placement is a deployment/security concern because systemd mount
visibility must be known before a new directory exists.

HostConfig therefore owns two ceilings for lifecycle-enabled projects:

```text
repository_container_root
worktree_path_template
```

The Worktrunk adapter passes the host-authorized path template as an inline
configuration override for lifecycle operations. A project/user Worktrunk
configuration may narrow workflow behavior, but it cannot redirect server-
created worktrees outside the host-authorized container.

This avoids accepting arbitrary model-provided paths and avoids needing to
trust an unconstrained user-level Worktrunk path template.

For a preferred bare wrapper, a template can keep linked worktrees inside the
project container while Worktrunk continues to own rendering/sanitization.

Recommended templates are explicit by layout:

```text
# Preferred bare wrapper
# repo_path = /repos/tools/coding-tools-mcp/.git
repository_container_root = /repos/tools/coding-tools-mcp
worktree_path_template = "{{ repo_path }}/../{{ branch | sanitize }}"

# Existing bare + sibling style, e.g. example.git + example.git.dev
# repo_path = /srv/projects/example/example.git
repository_container_root = /srv/projects/example
worktree_path_template = "{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}"

# Normal clone + dedicated sibling arena
# repo_path = /repos/tools/coding-tools-mcp
repository_container_root = /repos/tools
worktree_path_template = "{{ repo_path }}/../{{ repo }}.worktrees/{{ branch | sanitize }}"
```

The normal-clone example is acceptable only when the configured container root
is itself an acceptable trust boundary. If `/repos/tools` contains unrelated
repositories that must remain hidden, use a narrower dedicated arena or the
preferred bare-wrapper layout instead.

### 7.4 Hooks and approvals

Worktrunk project hooks remain Worktrunk's responsibility.

The server must query approval state before any unattended provider operation
that could execute project hooks:

```text
wt -C <root> config approvals list --format=json
```

If Worktrunk reports `approval_required`, the server returns a typed
`worktrunk_approval_required` result unless HostConfig explicitly authorizes a
different non-interactive policy.

The adapter must not silently add `--yes` merely to make automation pass.

Worktrunk hooks and future Coding Tools mutation hooks are different layers:

- Worktrunk hooks surround worktree lifecycle (`pre-start`, `post-start`,
  `pre-merge`, etc.).
- Coding Tools hooks surround MCP mutations (`after_patch`, future command
  verification, etc.).

Neither layer recursively impersonates the other.

### 7.5 Provider logs

Worktrunk stores its own bounded caches/logs under Git administrative state.
Coding Tools may reference provider outcome/log metadata from checkpoints but
must not duplicate full provider logs or persist secrets in Work Item records.

---

## 8. Systemd and filesystem visibility

### 8.1 Why the current project-root bind is insufficient

A linked worktree can be visible while its Git common repository is hidden.

An inspected bare-layout deployment demonstrates this failure mode:

```text
visible:
  /srv/projects/example/example.git.dev

required by its .git gitfile but currently outside the bind:
  /srv/projects/example/example.git/worktrees/example.git.dev
```

Inside the hardened service namespace, Git therefore reports the worktree as
not being a repository even though normal file reads succeed.

Worktree support must fix this structurally.

### 8.2 Repository container ceiling

For lifecycle-enabled worktree projects, HostConfig owns a dedicated
`repository_container_root` that is visible/writable inside the systemd
sandbox.

The container must include:

- the registered default worktree;
- the Git common repository; and
- every host-authorized location where Worktrunk may create a linked worktree.

It must not be widened automatically to an unrelated shared parent merely
because that is the filesystem lowest common ancestor.

Example good boundary:

```text
/srv/projects/example
```

when that directory is dedicated to one logical repository and its
worktrees.

Example undesirable boundary:

```text
/srv/projects
```

when it contains many unrelated projects.

### 8.3 Bare wrapper advantage

The preferred bare-wrapper layout naturally creates the ideal systemd bind:

```text
BindPaths=/srv/projects/coding-tools-mcp
```

where that one directory contains the bare Git store and every worktree.

New worktrees become visible without changing or restarting systemd because
they are created inside an already-authorized mount.

### 8.4 Normal clones

Normal clones remain supported, but dynamic lifecycle requires a bounded arena
already represented in HostConfig/systemd.

If a normal clone uses siblings under a parent that also contains unrelated
repositories, the operator must either:

1. configure a dedicated worktree arena; or
2. migrate that project to a dedicated/bare container.

The runtime must not silently broaden the sandbox for convenience.

---

## 9. Work Item model

### 9.1 Ownership

Every Work Item has exactly one `project_id`.

Conceptual model:

```text
WorkItem
  work_id
  project_id
  title
  description
  status
  priority
  created_at
  updated_at
  dependencies[]
  scopes[]
  active_claim?
  active_worktree_binding?
  metadata
```

A Work Item is durable even when no worktree exists.

### 9.2 State machine

V1 uses a deliberately small state machine:

```text
open
  -> claimed
      -> in_progress
          -> completed
          -> blocked
          -> cancelled

claimed -> open          # explicit release or lease expiry before progress
in_progress -> open      # lease expiry/release when policy allows resumption
blocked -> in_progress   # valid owner/claim explicitly resumes
blocked -> cancelled
```

`completed` and `cancelled` are terminal in V1. Reopening terminal work creates
a new Work Item or an explicit future-version operation; it is not an implicit
status edit.

Exact persistence transitions are transactional and reject invalid edges.

### 9.3 Claims and leases

At most one active claim exists per Work Item.

```text
Claim
  claim_id
  work_id
  lease_token_hash
  actor_metadata
  acquired_at
  renewed_at
  expires_at
```

The plaintext lease token is returned only to the successful claimant and is
never persisted. A strong random token is hashed before storage and compared
using constant-time verification.

Actor metadata is trace information only. It never substitutes for a valid
lease credential.

Lease expiry is evaluated against persisted UTC expiry timestamps inside the
same SQLite transaction that authorizes a mutation. A stale credential cannot
renew, bind, update, block, complete, cancel-as-owner, or checkpoint work.

### 9.4 Lease timing

HostConfig owns bounded lease policy:

```text
default_ttl_seconds
min_ttl_seconds
max_ttl_seconds
```

The client may request a TTL only inside those ceilings.

Renewal atomically computes a new expiry from server time. A renewal arriving
after the old expiry loses even if the old client still has the token.

No transport disconnect triggers implicit release.

---

## 10. Worktree binding to Work Items

### 10.1 Binding is optional

A Work Item can exist and be claimed without a worktree.

```text
Work Item -> project_id
         -> optional active worktree binding
```

This preserves the distinction between coordination state and physical
execution state.

### 10.2 Claim and create are not one cross-system transaction

SQLite and Worktrunk cannot form one atomic transaction. V1 must not pretend
otherwise.

The robust flow is:

```text
1. claim Work Item atomically in SQLite
2. create/select worktree through Worktrunk
3. Git revalidates project membership
4. bind the resulting worktree_id under the active lease
5. append binding event/checkpoint metadata
```

If step 2 fails, the claim remains valid without a worktree. The caller may
retry, bind an existing worktree, or release the claim.

### 10.3 Binding existing worktrees

`work_bind` associates an already-discovered worktree with a claimed Work Item.
It does **not** authorize an arbitrary path.

The server accepts only a currently discovered `worktree_id` whose Git common
repository matches the Work Item's `project_id`.

### 10.4 Worktree removal

Removing a worktree does not delete the Work Item, claim history, checkpoints,
or audit trail.

An active binding becomes historical/stale after confirmed removal. The Work
Item may later bind another worktree under a valid claim.

Removal of a worktree actively bound to another unexpired claim is rejected by
default.

### 10.5 Core tools do not require Work Items

Work Items are coordination, not mandatory ACL wrappers around existing tools.

A caller may explicitly target an authorized `worktree_id` without creating a
Work Item. Host policy may separately enforce overlap/conflict rules for
mutations, but the existence of the Work subsystem does not silently change
filesystem authorization semantics.

---

## 11. Path scopes and parallel conflict detection

Work Items may declare bounded project-relative scopes such as:

```text
packages/auth/**
apps/web/app/api/auth/**
```

Scopes are normalized relative to the logical project and apply independently
of which worktree currently hosts the task.

V1 uses scopes for:

- overlap detection between active claims;
- operator/model visibility;
- optional mutation conflict policy; and
- checkpoint summaries.

They are not hidden filesystem permissions.

HostConfig selects one explicit mutation-overlap policy:

```text
off
warn
deny
```

`warn` is the compatibility-oriented default for first deployment. `deny`
requires a valid non-conflicting Work context before conflicting mutations and
must produce a typed error rather than silently retargeting work.

Scope matching must normalize separators, reject absolute paths and `..`, and
operate on canonical project-relative paths after worktree routing.

---

## 12. Durable SQLite state

### 12.1 Location and partitioning

Operational state lives outside repositories under the existing host-owned
state root.

Partition key:

```text
deployment/state namespace + project_id
```

Absolute checkout paths are metadata, not the durable project identity.

### 12.2 Required logical tables

V1 should model at least:

```text
work_items
work_dependencies
work_scopes
claims
worktree_instances
worktree_bindings
checkpoints
work_events
idempotency_keys
schema_migrations
```

`worktree_instances` is a bounded durable observation/history table, not an
alternative Git registry. Git/Worktrunk remain authoritative for current
existence.

### 12.3 SQLite concurrency

The implementation uses SQLite transactions and uniqueness constraints for
claim exclusivity.

The claim path must acquire a write transaction before evaluating/creating the
active claim so two concurrent request handlers cannot both win.

The implementation plan should use WAL mode where compatible with the state
filesystem, a bounded busy timeout, foreign keys, and deterministic retry/error
mapping rather than unbounded lock waits.

### 12.4 Schema migration

Database schema is explicitly versioned. Startup/preflight applies only known
forward migrations and fails closed on an unknown future schema version.

Migration does not delete user-visible Work history as a side effect.

### 12.5 Bounded retention

The database may retain completed/cancelled Work Items and audit events, but all
unbounded child collections require configurable limits/retention.

GC may remove expired idempotency records and old low-value event detail while
preserving Work Item terminal state and checkpoint identity.

---

## 13. Stateless MCP API shape

### 13.1 Work tools

Candidate V1 tools remain separate from MCP Tasks:

```text
work_create
work_get
work_list
work_claim
work_renew
work_release
work_update
work_block
work_complete
work_cancel
work_checkpoint
work_bind
```

Schemas remain small and handle-based. Detailed repository/worktree metadata is
queried separately when needed.

### 13.2 Worktree tools

Candidate worktree tools:

```text
worktree_list
worktree_get
worktree_create
worktree_remove
```

`worktree_create` accepts logical inputs such as project, branch, optional base,
and optional claimed `work_id`; it does not accept an arbitrary output path.

Merge automation can remain internal or a later public tool until its policy
surface is accepted separately.

### 13.3 Idempotency

HTTP is stateless and clients may retry uncertain requests.

Mutation tools that can create durable records or external lifecycle effects
must accept a bounded client-generated `request_id`/idempotency key.

At minimum:

```text
work_create
work_claim
work_checkpoint
worktree_create
```

must provide deterministic retry behavior.

An idempotency key is scoped to operation + `project_id` + actor/principal
context where applicable; replay with a different payload is rejected.

---

## 14. Checkpoints

A checkpoint records enough bounded evidence for another client to continue
without a transcript:

```text
work_id / claim_id
project_id / worktree_id
branch + HEAD
Git status summary
changed path summary
completed hook/check names + outcomes
short structured continuation note
timestamp
```

Checkpoints do not store arbitrary chat transcripts, hidden reasoning, full
environment dumps, or secrets.

Checkpoint creation requires a current lease when attached to active claimed
work.

---

## 15. Failure model

The public API needs stable typed failures rather than generic strings.

At minimum distinguish:

```text
work_not_found
work_terminal
invalid_work_transition
already_claimed
lease_expired
lease_mismatch
dependency_blocked
scope_conflict
worktree_not_found
worktree_stale
worktree_wrong_project
worktree_outside_authorized_container
worktree_bound_elsewhere
worktrunk_unavailable
worktrunk_schema_unsupported
worktrunk_approval_required
worktrunk_operation_failed
git_repository_identity_failed
state_busy
state_corrupt
state_schema_unsupported
idempotency_conflict
```

Provider stderr/output is bounded and attributable but does not replace the
stable top-level error code.

---

## 16. Security and authority

### 16.1 HostConfig owns ceilings

HostConfig owns:

- whether Work Items are enabled;
- whether lifecycle automation is enabled;
- provider selection;
- provider executable/capability policy;
- repository container roots;
- Worktrunk worktree path templates;
- lease TTL ceilings;
- SQLite state root/limits;
- scope conflict policy; and
- resource ceilings.

ProjectConfig may only select/narrow project-owned behavior permitted by
HostConfig. It cannot widen filesystem container roots or approve arbitrary
provider commands.

### 16.2 Worktrunk project commands

Committed `.config/wt.toml` hooks are project-provided executable behavior.
They remain subject to Worktrunk's approval model and the process sandbox.

Coding Tools must not transform project hook configuration into self-authorized
host policy.

### 16.3 Lease authority

Lease token + project authorization grants Work Item owner operations. Labels
such as `chatgpt`, `codex`, branch names, or worktree paths are not authorities.

### 16.4 Repository membership

Neither a client nor Worktrunk may cause a path from a different Git common
repository to be accepted under an existing `project_id`.

---

## 17. Performance and efficiency

Parallelism must not make every request expensive.

Required properties:

1. `list_projects` remains O(number of registered projects), not O(all
   worktrees + all Git status details).
2. Worktree discovery is lazy and bounded per requested project.
3. Worktrunk discovery uses local/non-`--full` information by default.
4. Git membership validation is cached briefly but rechecked before mutations.
5. SQLite connections use short transactions; no provider process runs while a
   claim transaction holds the database write lock.
6. Semantic workers are lazy and LRU-bounded by active worktree roots.
7. Checkpoints store summaries/references, not duplicated command output.
8. Work Item listing is paginated and bounded.
9. Event/audit payloads are bounded and GC-capable.
10. No model request repeats the full worktree catalog merely to target one
    checkout.

---

## 18. Migration and rollout

Implementation should be split so repository-layout support is proven before
durable coordination depends on it.

### Hard prerequisite — PW0-PW6 modernization gate

The full pre-Worktree gate defined in
`2026-08-18-pre-worktree-runtime-modernization-design.md` must be GREEN first.

Required proof includes:

- current `xyTom/main` is deliberately integrated and is an ancestor of fork
  `main`;
- MCP 2026-07-28 dispatch/cancellation behavior is exact;
- project operations use one canonical `ExecutionTarget` resolver;
- public `cwd`/`workdir`/Git path semantics are normalized;
- `server_info` is summary-first;
- `project_context` and `doctor` provide bounded orientation/diagnostics;
- future provider operations inherit one policy/credential/confinement path;
  and
- full protocol/bridge/runtime/live gates are fresh.

WT0 must not absorb unfinished work from that list.

### WT0 — Read-only repository/worktree identity

- add repository/common-dir discovery;
- add Worktrunk schema-2 adapter with Git fallback;
- add worktree-aware status/discovery types;
- fix sandbox visibility for existing bare + sibling layouts;
- prove normal clone + bare sibling + bare wrapper fixtures.

No Work Items yet.

### WT1 — Optional `worktree_id` routing

- add optional worktree selection to filesystem, command, Git, skill/context,
  and semantic routing;
- preserve omitted-`worktree_id` compatibility;
- key command recovery and semantic workers by worktree;
- prove cross-worktree isolation under concurrent requests.

### WT2 — Worktrunk lifecycle

- host-owned provider executable/capability preflight;
- HostConfig container/path-template ceilings;
- approval-aware create/list/remove integration;
- invalidate/revalidate discovery around lifecycle operations;
- no project-Mise auto-install side effects.

### WT3 — Durable Work Items

- SQLite schema/migrations;
- Work Items/dependencies/scopes;
- atomic claims and leases;
- checkpoints/events;
- optional worktree binding;
- idempotency.

### WT4 — Parallel coordination acceptance

- two or more independent clients claim different Work Items;
- Worktrunk creates separate worktrees inside one project container;
- concurrent filesystem/Git/Serena operations remain isolated by
  `(project_id, worktree_id)`;
- overlapping scopes produce the configured `off|warn|deny` behavior;
- lease expiry/reclaim survives process restart;
- worktree removal leaves durable Work history intact.

Only after WT0-WT4 are green should automatic worktree allocation be treated as
a stable foundation for higher-level Gateway/agent workflows.

---

## 19. Repository migration policy

Supporting bare repositories does not require immediate migration of all
registered projects.

The runtime must first support both layouts. Individual projects may then move
to the preferred bare-container layout when parallel worktree usage justifies
it.

A safe migration procedure must:

1. inventory committed, uncommitted, untracked, ignored, and private files;
2. preserve all required local refs/commits; pushing first is optional if the
   migration clones from the existing local repository;
3. create the bare/common repository without using `--mirror` as a normal
   development layout;
4. materialize the configured default worktree through Git/Worktrunk;
5. copy deliberately required ignored/private files rather than assuming Git
   contains them;
6. update HostConfig/systemd container visibility transactionally;
7. verify Git/Worktrunk/MCP routing before retiring the old checkout; and
8. retain rollback material until live connector acceptance is complete.

The self-hosting Coding Tools repository should be migrated only after the new
worktree-aware runtime can verify the target layout without depending on the
migration being successful.

---

## 20. Testing and acceptance

### 20.1 Repository/worktree identity

Hermetic fixtures cover:

- normal clone main worktree;
- normal clone linked sibling worktree;
- bare repository + sibling linked worktrees;
- bare wrapper + child worktrees;
- detached worktree;
- locked/prunable worktree;
- duplicate-branch condition;
- symlink/path escape;
- candidate from another Git common repository;
- moved repository repaired by Git;
- Worktrunk unavailable fallback.

### 20.2 Routing

For two worktrees of one `project_id`, verify independently:

- `read_file`;
- `apply_patch` dry-run/real routing;
- `exec_command` workdir;
- command recovery;
- Git status/diff/log;
- instructions/skills;
- Serena symbols/definition/references.

No result from one worktree may be returned as state for the other.

### 20.3 Worktrunk

- schema-2 parsing;
- host-owned binary resolution;
- no target-project Mise auto-install during provider discovery;
- approval-required state;
- create success/failure;
- provider returns path outside authorized container;
- Git membership mismatch after provider output;
- removal with dirty/bound worktree;
- bounded provider output/error mapping.

### 20.4 Work coordination

- concurrent claim race has exactly one winner;
- lease expiry makes work reclaimable;
- stale lease cannot mutate/complete/bind/checkpoint;
- actor metadata never grants authority;
- invalid state transitions reject atomically;
- dependencies block correctly;
- state survives server restart;
- project states are isolated;
- worktree binding survives transport reconnect;
- removed worktree does not remove Work history;
- idempotent retries do not duplicate Work Items/checkpoints/worktrees.

### 20.5 Deployment

Live acceptance must include at least one bare-layout project and prove:

- default worktree Git operations work inside the systemd sandbox;
- Git common repository is visible;
- a new provider-created worktree becomes visible without restarting systemd;
- the new worktree resolves to the same `project_id` and a distinct
  `worktree_id`;
- another registered project remains inaccessible through that project's
  container root;
- no unexpected dependency/provider installation occurs.

---

## 21. Current known evidence and gaps

As of the design date:

1. A registered bare-layout project inspected during design points to a linked
   worktree whose `.git` gitfile targets a sibling bare common repository.
2. The linked worktree and its common repository live under one dedicated
   project container but are separate sibling paths.
3. The current canonical systemd unit exposes the worktree root but not the
   sibling bare common repository, so MCP Git tools report `is_repo=false` for
   that project even though filesystem tools work.
4. Worktrunk is installed in the host environment and supports structured JSON
   discovery; the current host installation observed during design was
   `wt v0.74.0`.
5. Invoking project-scoped `wt` through the generic command environment can
   cause Mise to honor target-project pins and attempt installs/writes; the
   provider adapter must avoid that path.
6. No `work` extension, Work Item SQLite state, lease API, or worktree-aware
   routing is implemented yet.

These observations are motivation/evidence, not a frozen deployment snapshot;
implementation must revalidate live state.

---

## 22. External references

Primary references used for this design:

- Worktrunk `wt list` JSON schema and worktree metadata:
  `https://worktrunk.dev/list/`
- Worktrunk configuration, `worktree-path`, JSON schema selection, approvals,
  and default-branch state:
  `https://worktrunk.dev/config/`
- Worktrunk `wt switch` lifecycle/automation semantics:
  `https://worktrunk.dev/switch/`
- Worktrunk hooks and approval model:
  `https://worktrunk.dev/hook/`
- Worktrunk removal/merge automation:
  `https://worktrunk.dev/remove/`
  and `https://worktrunk.dev/merge/`
- Git worktree model, linked-worktree admin directories, common repository,
  porcelain format, and repair semantics:
  `https://git-scm.com/docs/git-worktree`
- Git repository layout and `$GIT_COMMON_DIR`:
  `https://git-scm.com/docs/gitrepository-layout`
- Git repository-path discovery primitives:
  `https://git-scm.com/docs/git-rev-parse`

