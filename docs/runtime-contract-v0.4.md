# Coding Tools MCP Runtime Contract v0.4

**Contract:** v0.4
**Default internal extension:** `projects` enabled
**Default composed catalog:** 24 tools
**Project addressing:** explicit `project_id`; no active/current project state

This is the current runtime contract for the Yuzu02 fork. The historical
[v0.3 contract](runtime-contract-v0.3.md) remains frozen as the 0.3-era record.
The fork composes mother-core definitions and enabled internal extensions once
at process startup, validates the resulting catalog, and then freezes it for
the lifetime of that process. `tools/list` therefore keeps
`listChanged: false`; extensions are never enabled or disabled by an MCP call.

## Runtime and project model

One process may serve multiple explicitly registered projects that belong to
the same deployment trust domain. Every project-scoped request independently
names a stable configured `project_id`. There is no `activate_project`,
`select_project`, mutable current project, session cwd, or request ordering
precondition.

The default `projects` extension contributes `list_projects`,
`resolve_project`, `list_skills`, and `read_skill`, and decorates the
project-scoped mother-core tools with a required `project_id`. A legacy
`--workspace /path/to/repo` launch with no explicit project registry synthesizes
one registered project with ID `default`; clients still address it explicitly
as `project_id: "default"`.

Configured IDs are logical identifiers, not derived from directory basenames.
Paths/workdirs passed to project-scoped tools remain relative to the selected
root and are canonicalized against it. A separately registered nested project
is excluded from its registered parent project's direct path namespace, so a
parent-scoped relative path cannot silently cross the child registration
boundary.

The exact project-scoped tools are:

```text
check_exec_environment
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

`server_info`, `list_projects`, and `resolve_project` are global discovery
operations. The process handshake and `server/discover` return project-neutral
instructions; they never concatenate the `AGENTS.md`/`CLAUDE.md` contents of
all registered projects. Project instruction files are discovered within the
selected project through project-scoped skill/file operations.

## Command ownership and opaque handles

Each configured project gets its own lazy workspace runtime state, command
manager, runtime directory, patch baselines, project catalog, skill catalog,
and project context. Command ownership is recorded globally only to route
opaque handles back to the owning project runtime.

- `exec_command(project_id=..., client_request_id=...)` scopes idempotency to
  that project.
- `get_command(command_id=...)` needs no `project_id`: the command ID is an
  opaque globally routable handle.
- `get_command(client_request_id=...)` requires `project_id`, because the same
  client request ID may legitimately exist in two projects.
- `list_commands(project_id=...)` filters one project; without `project_id` it
  aggregates retained command metadata and labels ownership.
- `write_stdin(command_id=...)` and `kill_command(command_id=...)` route by
  command ownership.
- `read_output(output_ref="command:<id>:stdout|stderr")` routes by the command
  embedded in the opaque output reference.

HTTP transport sessions do not own commands. Reconnecting to the same runtime
does not invalidate a retained command or output handle.

## Protocol eras and transport

The protocol behavior introduced by v0.3 remains in force:

- MCP `2026-07-28` is served request-by-request through `params._meta`.
- Handshake-era `2025-11-25` and `2025-06-18` remain supported.
- Streamable HTTP uses `/mcp`; stdio uses newline-delimited JSON-RPC.
- Neither era has an MCP transport session. HTTP does not issue
  `Mcp-Session-Id`, and `DELETE /mcp` is `405`.
- JSON-RPC batches are rejected.
- `notifications/cancelled` is silent and does not implicitly kill a retained
  command; use `kill_command`.
- Modern HTTP requests retain the v0.3 mirror-header and HTTP-status rules.

The v0.4 change is project addressing and extension composition, not a new wire
protocol era.

## Result contract

Successful tools return the ordinary MCP tool envelope:

```json
{
  "content": [{"type": "text", "text": "agent-readable bounded text"}],
  "structuredContent": {"ok": true},
  "isError": false
}
```

`structuredContent` is the complete machine interface. `content` is bounded
model-facing presentation and is not a JSON mirror. `view_image` may also
return one MCP image block; image base64 is not duplicated in
`structuredContent`.

Recoverable tool-domain failures preserve the envelope with `isError: true`
and a structured `error` containing `code`, `message`, `category`, `retryable`,
and `details`.

Known live `ToolFailure` codes are:

```json
["ABSOLUTE_PATH_DENIED", "BINARY_FILE", "COMMAND_CLOSED", "COMMAND_LIMIT_REACHED", "COMMAND_NOT_FOUND", "COMMAND_STARTING", "COMMAND_START_FAILED", "GIT_ERROR", "IDEMPOTENCY_CONFLICT", "INVALID_ARGUMENT", "IS_DIRECTORY", "NOT_A_DIRECTORY", "NOT_FOUND", "OUTPUT_TOO_LARGE", "PATCH_CONFLICT", "PATCH_CONTEXT_AMBIGUOUS", "PATCH_CONTEXT_NOT_FOUND", "PATCH_FAILED", "PATCH_HUNKS_OVERLAP", "PATCH_ROLLBACK_FAILED", "PATH_OUTSIDE_WORKSPACE", "PERMISSION_REQUIRED", "PROJECT_CAPABILITY_DISABLED", "PROJECT_NOT_FOUND", "RUNTIME_DIR_UNWRITABLE", "SANDBOX_UNAVAILABLE", "CREDENTIAL_SANDBOX_UNAVAILABLE", "SHELL_NOT_FOUND", "SHELL_VERSION_UNSUPPORTED", "SKILL_INVALID", "SKILL_NOT_FOUND", "SYMLINK_ESCAPE", "TTY_UNSUPPORTED", "UNSUPPORTED_ENCODING"]
```

`CREDENTIAL_SANDBOX_UNAVAILABLE` is a `security` command error. It is
fail-closed: when the credential-isolation sandbox cannot be established, the
command is not started. The error is non-retryable unless the deployment
environment is repaired; its `details.reason` is diagnostic only.

Project registry/runtime operations may additionally surface typed project
state such as an unavailable configured project through project-specific error
payloads. Callers must treat structured error codes as the authoritative
machine signal rather than parsing text.

## Permission and isolation semantics

`safe`, `trusted`, and `dangerous` are permission policies, not tool profiles;
they do not change the composed catalog after startup.

- `safe` keeps network/shell-expansion/inline-script/destructive-command gates
  and enables Linux Landlock when supported.
- `trusted` opens normal development network, expansion, and inline-script
  flows while retaining the remaining policy checks and Landlock.
- `dangerous` disables MCP permission gates and Landlock. It is intended only
  for an externally isolated trusted environment.

Project addressing/path validation still chooses the requested project in all
modes, but `dangerous` is **not** a tenant-isolation promise: arbitrary shell
commands run with the aggregate host authority granted to the server process.
Use separate OS/container/systemd boundaries when projects require different
trust policies.

`request_permissions` remains globally shaped because it describes a proposed
tool invocation. When the target tool is project-scoped, the target
`arguments` must carry the corresponding `project_id`; permission identity is
therefore bound to the actual addressed operation rather than to mutable
current-project state.

## Configuration identities and project configuration

Developer compatibility mode keeps the v1 layered identities. Public/default
composition belongs in `coding-tools.toml`; machine-specific developer roots
belong in ignored `coding-tools.local.toml`.

```toml
# coding-tools.toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

```toml
# coding-tools.local.toml -- ignored by Git
config_version = 1

[extensions.projects.registry.app]
root = "/srv/projects/app"

[extensions.projects.registry.api]
root = "/srv/projects/api"
```

Layer precedence is built-in defaults → public TOML → local TOML → supported
environment overrides → explicit CLI overrides. Configuration is strict and
versioned. Unknown extension/config keys, dependency cycles, invalid registry
roots, duplicate canonical roots, contribution collisions, and invalid
decorator composition fail startup before transport begins accepting normal
requests.

System deployment mode is separate. It selects one strict HostConfig v2 with
`--host-config PATH`; that mode does not merge or auto-load a sibling
`coding-tools.local.toml`. HostConfig owns machine/deployment authority such as
the bootstrap workspace, registered project roots, listener, permission and
network ceilings, runtime/state/cache roots, extension enablement, deployment
timeouts, and tunnel selection. The services launcher and MCP runtime consume
the same normalized HostConfig model and immutable configuration snapshot.

Each registered project may additionally contain `.coding-tools-mcp.toml`
(`project_config_version = 1`). Project configuration is parsed at startup and
may only select or reduce authority already granted by the host; it cannot
change listeners, auth, tunnels, systemd policy, global roots, project
registration, or expand host permission/network ceilings. Configuration
changes therefore require restart; heavy project/Serena resources remain lazy.

## Optional semantic composition

Semantic navigation is an optional internal extension layered on top of the
default `projects` extension. The contract states are explicit:

- default projects-only composition: 24 tools
- projects + semantic with Serena 1.5.3 available at startup: 28 tools
- semantic enabled but Serena unavailable at startup: process starts without semantic tools
- runtime semantic worker failure: semantic tools remain in the frozen catalog
- a project may reduce host authority with `capabilities.disabled = ["semantic"]`

Project capability reduction does not mutate the frozen catalog. When semantic
is globally enabled and available, all four semantic tools remain published.
A semantic request for a project that disabled the capability fails before
path resolution or backend work with `PROJECT_CAPABILITY_DISABLED`, category
`permission`, `retryable=false`, and bounded details containing only
`project_id` and `capability`. Other projects in the same runtime remain
unaffected.

The four optional semantic tools are read-only and project-addressed. Their
live schemas and annotations are drift-checked against these canonical lines:

- `list_symbols` properties=`depth,max_results,path,project_id` required=`path,project_id` readOnly=true destructive=false idempotent=true openWorld=false
- `find_symbol` properties=`include_body,max_results,path,project_id,query` required=`project_id,query` readOnly=true destructive=false idempotent=true openWorld=false
- `find_definition` properties=`column,line,path,project_id` required=`column,line,path,project_id` readOnly=true destructive=false idempotent=true openWorld=false
- `find_references` properties=`column,include_declaration,line,max_results,path,project_id` required=`column,line,path,project_id` readOnly=true destructive=false idempotent=true openWorld=false

Semantic failures use the normal MCP tool-error envelope with category
`semantic`. The complete semantic error catalog is:

- `SEMANTIC_BACKEND_UNAVAILABLE`
- `SEMANTIC_PROJECT_START_FAILED`
- `SEMANTIC_LANGUAGE_UNSUPPORTED`
- `SEMANTIC_FILE_UNSUPPORTED`
- `SEMANTIC_SYMBOL_NOT_FOUND`
- `SEMANTIC_POSITION_INVALID`
- `SEMANTIC_TIMEOUT`
- `SEMANTIC_BACKEND_ERROR`

The Serena 1.5.3 adapter uses one Serena worker per active project. Workers are
created lazily, bounded by `max_semantic_projects`, reaped after
`semantic_idle_timeout_seconds`, and evicted least-recently-used only while
idle. Different projects may run semantic requests concurrently; one worker
serializes its own requests. A runtime worker failure affects only the selected
project's semantic worker and does not mutate the frozen MCP tool catalog.

Serena/SolidLSP integration stays behind a private JSON-lines worker protocol.
Worker HOME, temp files, caches, and Serena project state live under the
project's runtime state directory rather than the source tree. Public semantic
positions are one-based and returned paths are project-relative.

Install the exact optional backend from a checkout with:

```bash
uv sync --extra semantic
```

Enable it explicitly:

```toml
[extensions]
enabled = ["projects", "semantic"]

[extensions.semantic]
backend = "serena"
max_semantic_projects = 4
semantic_idle_timeout_seconds = 900
semantic_start_timeout_seconds = 60
semantic_request_timeout_seconds = 60
allow_dependency_install = false
```

`allow_dependency_install = false` is the offline-safe default. Set it to
`true` only in host-local configuration when SolidLSP may bootstrap missing
`uvx`/npm language-server dependencies.

For Serena 1.5.3, Coding Tools deliberately uses one private worker per active
project instead of one shared Serena ProjectServer/current-project lifecycle.
The per-project process boundary gives every request an explicit immutable
project owner and avoids reintroducing Serena activation/session state into the
stateless MCP contract. Semantic operations are read-only and never mutate
source files.

## Stable tool inventory

The following 24 sections are checked against the live default composed
catalog by CI. Input-property names and annotation values are intentionally
spelled out so drift is reviewable.

### server_info

Global server/runtime/extension/project summary. Input properties: none.

Annotations: `"title": "Server info"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### check_exec_environment

Return effective execution/runtime-directory policy for one selected project.
Input properties: `"project_id"`. Required: `"project_id"`.

Annotations: `"title": "Check exec environment"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### read_file

Read a bounded UTF-8 range in one project. Input properties: `"encoding"`,
`"end_line"`, `"max_bytes"`, `"max_lines"`, `"path"`, `"project_id"`,
`"start_line"`. Required: `"path"`, `"project_id"`.

Annotations: `"title": "Read file"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### list_dir

List one project-relative directory, optionally recursively. Input properties:
`"include_hidden"`, `"include_ignored"`, `"max_depth"`, `"max_entries"`,
`"path"`, `"project_id"`, `"recursive"`, `"sort"`. Required:
`"project_id"`.

Annotations: `"title": "List directory"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### list_files

List bounded files inside one project. Input properties: `"exclude_patterns"`,
`"glob"`, `"include_hidden"`, `"include_ignored"`, `"max_results"`, `"path"`,
`"patterns"`, `"project_id"`, `"sort"`. Required: `"project_id"`.

Annotations: `"title": "List files"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### search_text

Search bounded text inside one project. Input properties: `"case_sensitive"`,
`"context_lines"`, `"exclude_globs"`, `"glob"`, `"include_globs"`,
`"max_preview_bytes"`, `"max_results"`, `"path"`, `"project_id"`, `"query"`,
`"regex"`. Required: `"query"`, `"project_id"`.

Annotations: `"title": "Search text"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### apply_patch

Validate and atomically apply a structured patch inside one project. Input
properties: `"dry_run"`, `"patch"`, `"project_id"`. Required: `"patch"`,
`"project_id"`.

Annotations: `"title": "Apply patch"`, `readOnlyHint=false`,
`destructiveHint=true`, `idempotentHint=false`, `openWorldHint=false`.

### exec_command

Run a bounded command in one selected project. Input properties:
`"client_request_id"`, `"cmd"`, `"cwd"`, `"env"`, `"max_output_bytes"`,
`"preview_bytes"`, `"project_id"`, `"stdin"`, `"timeout_ms"`, `"tty"`,
`"verbosity"`, `"workdir"`, `"yield_time_ms"`. Required: `"cmd"`,
`"project_id"`.

Annotations: `"title": "Execute command"`, `readOnlyHint=false`,
`destructiveHint=true`, `idempotentHint=false`, `openWorldHint=true`.

### list_commands

List retained command metadata. `"project_id"` is optional: when omitted, the
result aggregates configured projects and labels ownership. Input properties:
`"client_request_id"`, `"limit"`, `"project_id"`, `"status"`. Required: none.

Annotations: `"title": "List commands"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### get_command

Recover one retained command. Input properties: `"client_request_id"`,
`"command_id"`, `"max_output_bytes"`, `"preview_bytes"`, `"project_id"`,
`"verbosity"`. Required: none at schema level; runtime validation requires
exactly one lookup form, and `client_request_id` lookup also requires
`project_id`.

Annotations: `"title": "Get command"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### write_stdin

Poll or write to an opaque command handle. Input properties: `"chars"`,
`"command_id"`, `"max_output_bytes"`, `"preview_bytes"`, `"verbosity"`,
`"yield_time_ms"`. Required: `"command_id"`.

Annotations: `"title": "Write stdin"`, `readOnlyHint=false`,
`destructiveHint=false`, `idempotentHint=false`, `openWorldHint=false`.

### kill_command

Terminate an opaque command handle. Input properties: `"command_id"`,
`"kill_wait_ms"`, `"max_output_bytes"`, `"preview_bytes"`, `"signal"`,
`"verbosity"`, `"wait_ms"`. Required: `"command_id"`.

The status enum is exactly:

```json
["terminated", "killed", "exited", "terminating", "not_found"]
```

Annotations: `"title": "Kill command"`, `readOnlyHint=false`,
`destructiveHint=true`, `idempotentHint=false`, `openWorldHint=false`.

### read_output

Page retained stdout/stderr by opaque output reference. Input properties:
`"limit"`, `"offset"`, `"output_ref"`, `"stream"`. Required: `"output_ref"`.

Annotations: `"title": "Read output"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### git_status

Return bounded structured status for one project. Input properties:
`"include_untracked"`, `"max_entries"`, `"path"`, `"project_id"`, `"workdir"`.
Required: `"project_id"`.

Annotations: `"title": "Git status"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### git_diff

Return bounded staged/unstaged diff for one project. Input properties:
`"context_lines"`, `"max_bytes"`, `"path"`, `"paths"`, `"project_id"`,
`"staged"`, `"unstaged"`, `"workdir"`. Required: `"project_id"`.

Annotations: `"title": "Git diff"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### git_log

Return bounded commit history for one project. Input properties: `"max_count"`,
`"path"`, `"project_id"`, `"ref"`, `"skip"`, `"workdir"`. Required:
`"project_id"`.

Annotations: `"title": "Git log"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### git_show

Show one revision in one project. Input properties: `"context_lines"`,
`"include_diff"`, `"max_bytes"`, `"path"`, `"paths"`, `"project_id"`, `"rev"`,
`"workdir"`. Required: `"project_id"`.

Annotations: `"title": "Git show"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### git_blame

Return bounded line attribution for one project. Input properties: `"end_line"`,
`"max_lines"`, `"path"`, `"project_id"`, `"rev"`, `"start_line"`, `"workdir"`.
Required: `"path"`, `"project_id"`.

Annotations: `"title": "Git blame"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### request_permissions

Report permission-request status for a proposed tool invocation without
silently granting it. Input properties: `"arguments"`, `"permission"`,
`"reason"`, `"scope"`, `"tool_name"`, `"ttl_seconds"`. Required:
`"tool_name"`, `"permission"`, `"reason"`, `"arguments"`.

Annotations: `"title": "Request permissions"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=false`, `openWorldHint=false`.

### view_image

Read and optionally resize one image inside a selected project. Input
properties: `"auto_resize"`, `"max_bytes"`, `"max_height"`, `"max_width"`,
`"path"`, `"project_id"`. Required: `"path"`, `"project_id"`.

Annotations: `"title": "View image"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### list_projects

List the immutable configured project registry. Input properties: none.

Annotations: `"title": "List projects"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### resolve_project

Resolve an absolute server path to the longest matching configured project.
This is operator/server-path discovery, not required for normal remote
addressing; clients ordinarily use stable IDs returned by `list_projects`.
Input properties: `"path"`. Required: `"path"`.

Annotations: `"title": "Resolve project"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### list_skills

List effective project-scoped skills/instruction files. Input properties:
`"project_id"`, `"workdir"`. Required: `"project_id"`.

Annotations: `"title": "List skills"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

### read_skill

Read one effective project-scoped skill. Input properties: `"project_id"`,
`"skill"`, `"workdir"`. Required: `"project_id"`, `"skill"`.

Annotations: `"title": "Read skill"`, `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

## Compatibility and historical contracts

v0.4 deliberately evolves the fork's composed runtime contract while retaining
the same MCP protocol eras. v0.3 remains available at
`docs/runtime-contract-v0.3.md` and continues to describe its historical
22-tool single-workspace snapshot; `docs/migration-0.3.md` and the 0.3 CHANGELOG
entry are historical records and must not be rewritten to match v0.4.

The package version remains independent of this internal contract document;
release/version changes follow the repository release process separately. In
particular, runtime contract v0.4 does not itself require a package-version bump.

`python -m coding_tools_mcp --version` reports the package/release version.
`server_info.version` remains the legacy package-version field, and
`server_info.package_version` exposes the same identity explicitly.
`server_info.runtime_contract_version` separately reports `0.4`. The safe
configuration metadata also exposes resolution mode, deterministic fingerprint,
config versions, and warning count without resolved secret values.
