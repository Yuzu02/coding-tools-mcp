# Coding Tools MCP Spec

This fork implements the current `coding-tools-mcp-v0.4` runtime contract
defined in [docs/runtime-contract-v0.4.md](docs/runtime-contract-v0.4.md).
The v0.3/v0.2 contracts remain frozen historical records.

## Product boundary

The server exposes low-level coding primitives over MCP: inspect explicitly
addressed projects, apply structured patches, run and interact with commands,
and inspect Git. It is
not an agent wrapper and does not expose accounts, memory, cloud tasks, web
search, model routing, plugins, image generation, or subagent orchestration.

## Stable process tool model

The fork composes one catalog during startup from mother-core definitions plus
enabled internal extensions. Once startup succeeds, that catalog is stable for
the process: there are no runtime tool profiles, no dynamic
`tools/list_changed`, no required `open_workspace` call, and no runtime
extension enable/disable operation. `apply_patch` is the structured direct
file-edit primitive. `safe`, `trusted`, and `dangerous` are command permission
policies and never alter `tools/list`.

The default catalog contains 24 tools:

- runtime/context: `server_info`, project-scoped `check_exec_environment`
- project discovery: `list_projects`, `resolve_project`
- workspace inspection: `read_file`, `list_dir`, `list_files`, `search_text`
- project skills: `list_skills`, `read_skill`
- mutation: `apply_patch`
- processes: `exec_command`, `list_commands`, `get_command`, `write_stdin`, `read_output`, `kill_command`
- Git: `git_status`, `git_diff`, `git_log`, `git_show`, `git_blame`
- policy/image: `request_permissions`, `view_image`

`projects` is enabled in the default composition. It contributes
`list_projects`, `resolve_project`, `list_skills`, and `read_skill`; publishes
the configured project/runtime capabilities; and decorates project-scoped core
operations with explicit `project_id` routing. Disabling that extension before
startup removes those four tools and their addressing decorators, exposing the
20 mother-core tools for that process. `view_image` can independently be
disabled as an installation capability. Neither mechanism mutates the catalog
after startup.

There is no mutable active/current project. Project-scoped requests carry a
stable configured `project_id`; a traditional single `--workspace` launch
without an explicit registry synthesizes `project_id="default"`. Opaque
`command_id` and output handles route to their owning project automatically,
while a `client_request_id` lookup is scoped by project.

## Protocol

- Two eras are served at once: `2026-07-28`, which carries its version, client
  capabilities, and identity in each request's `params._meta`, and the
  handshake era `2025-11-25` with `2025-06-18` explicitly supported. A request
  belongs to the modern era if and only if its `_meta` names that version.
- Streamable HTTP uses `/mcp`; stdio uses newline-delimited JSON-RPC.
- There are no sessions in either era. One `Runtime` may own several registered
  project runtimes in one trust domain and serves every client of that endpoint;
  HTTP issues no `Mcp-Session-Id` and `DELETE /mcp` returns `405`.
- JSON-RPC batches are rejected, unimplemented logging is not advertised, and
  `notifications/cancelled` is accepted without terminating the command the
  cancelled request started — a command is stopped with `kill_command`.
- `content` is agent-readable text normally sized by each tool's per-call
  limits, with a documented emergency safety ceiling for pathological entries.
  `structuredContent` is the complete stable machine result. `_meta` is
  optional UI space only.
- `initialize` and `server/discover` return project-neutral instructions. Root
  and nested instruction files are resolved only inside an explicitly selected
  project scope.

## Correctness guarantees

Patch operations are staged before writing, use same-directory fsynced temporary
files and atomic replacement, preserve mode/BOM/newlines, detect stale
baselines, and roll back multi-file failures. Filesystem rollback failure is
reported explicitly rather than hidden.

Commands use a 10-second default yield, real POSIX PTYs, bounded active and
retained-command stores, per-command and runtime output budgets, TTL cleanup,
and explicit `next_action` objects for polling or truncated output. Command
handles are `command_id` values owned by one project runtime rather than by a
client: any authenticated client of the endpoint can continue, read, or kill a
retained command with its opaque handle, and no transport event ends it.

## Security boundary

Direct project-scoped tools reject absolute paths, traversal, NULs, symlink
escapes, and crossing into a separately registered nested project root.
`exec_command` also applies permission policy and Linux Landlock when available,
but remains a coding runtime rather than a complete container sandbox. Remote
deployment must use bearer or OAuth authentication. OAuth supports protected
resource metadata, PKCE S256, exact redirect binding, and RFC 7591 dynamic client
registration. Authentication admits a client to the endpoint and does not
partition configured projects into hostile tenants: one process is one trust
domain. `dangerous` disables MCP permission gates and Landlock, so external
process/container boundaries remain required for mutually untrusted projects.

## Compatibility

Version 0.3 adds `2026-07-28` and removes every session. The handshake era is
unchanged on the wire; the cwd tools, the HTTP session, and several
`server_info` fields are not. See
[docs/migration-0.3.md](docs/migration-0.3.md).

Runtime contract v0.4 keeps those protocol eras and adds the fork's internal
extension composition plus explicit multi-project addressing. It does not imply
a package-version bump by itself.

Version 0.2 changes model-facing result text from a JSON mirror to summaries.
Clients that parsed `content[0].text` as JSON must read `structuredContent`.
Image base64 now appears once, in the MCP image block. Tool profiles and the
`view_image.output` selector are removed.
