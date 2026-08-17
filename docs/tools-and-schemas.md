# Tools And Schemas

The normative behavior is [runtime-contract-v0.4.md](runtime-contract-v0.4.md).
Live JSON Schemas come from `tools/list`; CI compares their names, input
properties, annotations, and error codes with the contract.

## Fixed inventory

The committed/default composition contains exactly 24 tools:

- `server_info`: global server, auth, protocol, policy, extension, and configured
  project metadata without project-specific instruction contents.
- `check_exec_environment`: effective execution/runtime-directory policy for
  one explicit `project_id`.
- `read_file`: stream a bounded UTF-8 range from one project.
- `list_dir`: list immediate or bounded-recursive entries in one project.
- `list_files`: iterate project files with glob, ignore, hidden-file, sort, and
  cap controls.
- `search_text`: literal or regex project search; ripgrep stops after the cap.
- `apply_patch`: stage and atomically commit add/update/delete/move envelopes
  within one project.
- `exec_command`: run a bounded command in one project and wait up to 10
  seconds by default; `client_request_id` is idempotent within that project.
- `list_commands`: aggregate project-owned retained commands or filter by
  optional `project_id` / `client_request_id`.
- `get_command`: recover by opaque `command_id`, or by
  `project_id + client_request_id`, without consuming output.
- `write_stdin`: poll or interact with a running command.
- `kill_command`: terminate one runtime-owned command.
- `read_output`: page retained stdout or stderr using absolute byte offsets.
- `git_status`: structured status for one project.
- `git_diff`: bounded unified staged/unstaged diff for one project.
- `git_log`: structured bounded project commit history.
- `git_show`: bounded project revision metadata/content/diff.
- `git_blame`: structured bounded project line attribution.
- `request_permissions`: report permission-request status without silently
  granting; project-scoped targets carry their `project_id` in `arguments`.
- `view_image`: one project-scoped MCP image block plus structured metadata.
- `list_projects`: list stable configured project IDs and registry metadata.
- `resolve_project`: map an absolute server path to its longest matching
  configured project; primarily operator/local discovery.
- `list_skills`: list effective skills/instruction files for one project and
  explicit relative workdir.
- `read_skill`: load an effective named skill from one project scope.

The fork composes mother-core definitions plus enabled internal extension
contributions once during process startup. `tools/list`, argument validation,
and `tools/call` all consume the same frozen composed catalog, so
`listChanged` remains `false` for the lifetime of that process. There is no
runtime profile switching or dynamic extension activation.

The default `projects` extension contributes `list_projects`,
`resolve_project`, `list_skills`, and `read_skill`, and decorates the 13
project-addressed mother-core operations plus `view_image` when exposed. The
normal default therefore contains 24 tools. Starting the fork with
`--extensions ''` intentionally exposes only the 20 mother-core tools for that
process and removes project-addressing decoration. `view_image` remains
independently gated by installation capability. V1 tool decorators are
deterministic and may add schema properties and wrap handlers, but may not
replace existing schema properties.

## Optional semantic tools

The built-in `semantic` extension is disabled by default and depends on
`projects`. When it is enabled and exact-pinned `serena-agent==1.5.3` is
available at startup, the frozen catalog grows from 24 to 28 tools:

- **`list_symbols`** — list normalized semantic symbols in one project file;
- **`find_symbol`** — locate symbols by semantic name path, optionally returning a
  bounded body;
- **`find_definition`** — resolve a one-based project-relative source position;
- **`find_references`** — return project-relative one-based references, optionally
  including the declaration.

All four are `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`. They always require an
explicit `project_id`; there is no Serena activation/current-project API.

If the semantic extension is enabled but Serena is absent or not version
1.5.3, startup still succeeds without these four tools. If a worker later
fails after a 28-tool catalog was composed, the catalog stays fixed and the
semantic call returns a typed `SEMANTIC_*` failure. Filesystem, Git, and command
tools remain usable.

The adapter uses one lazy Serena worker per active project. Worker state lives
under runtime state, never `.serena` in the project source tree. Workers are
bounded by `max_semantic_projects`, reaped by idle timeout/LRU policy, and
serialize same-project operations while allowing different projects to make
semantic progress concurrently. Semantic source operations are read-only.

## Result envelope

Every successful tool call has:

```json
{
  "content": [{"type": "text", "text": "Agent-readable summary or bounded preview"}],
  "structuredContent": {"ok": true},
  "isError": false
}
```

`content` is not a JSON mirror. `structuredContent` is the complete machine
interface and retains existing fields where possible. Model-facing text is
bounded at 16 KiB; if it is shortened, the full structured value is still
present. Errors use the same envelope with readable recovery guidance and
`isError: true`.

`view_image` is the exception to text-only content: its base64 appears exactly
once in one `image` block. `structuredContent` contains path, media type, byte
count, dimensions, resize metadata, and warnings, but no base64 or data URL.

## Patch behavior

`apply_patch` accepts the standard envelope:

```text
*** Begin Patch
*** Add File: path/to/new.py
+content
*** Update File: path/to/existing.py
@@
 old
-before
+after
*** Move to: path/to/moved.py
*** Delete File: path/to/old.py
*** End Patch
```

All operations are parsed and matched before writes. Context must be unique.
Files are prepared in their destination directories, fsynced, baseline-checked,
and installed with atomic replacement. Multi-file failure restores prior files.
Mode bits, BOM, and newline style are preserved; moves inherit source mode.
Lines are split on `\n` only, so a line containing another Unicode line
boundary (`\x0c`, `\u2028`, `\x85`, …) is one line to both the file and the
patch. A file's final newline is an ordinary line the hunk can add or remove.

## Model-ready examples

Every project-scoped filesystem/Git/process call carries `project_id`. Relative
paths and workdirs resolve against that selected project root; there is no
session-scoped cwd or mutable current project. Discover IDs without activating
anything:

```json
{}
```

Call the global `list_projects` tool with that empty object, then address a
project directly. For example, run tests in project `app`:

```json
{"project_id":"app","cmd":"pytest -q","workdir":".","yield_time_ms":30000}
```

If the result is still running, copy its `command_id` exactly:

```json
{"command_id":"abc","chars":"","yield_time_ms":10000}
```

Terminate that command when needed:

```json
{"command_id":"abc","signal":"KILL"}
```

Page a truncated stream using the returned reference:

```json
{"output_ref":"command:abc:stdout","offset":0,"limit":4096}
```

`exec_command.workdir`, each Git tool's `workdir`, and each file/Git tool's
`path` argument are how a call targets a subdirectory; all remain confined to
the workspace.

## Command and output behavior

`exec_command` and `write_stdin` default `yield_time_ms` to `10000`. Short
commands ordinarily return `status: "exited"` in one call. A still-running
command returns a `command_id` and a machine-readable `next_action` for
`write_stdin` with empty `chars`.

For an execution whose outcome is uncertain, pass one stable, non-secret
`client_request_id` to `exec_command`. Retrying the same request either
returns the existing command or reports `COMMAND_STARTING`/
`COMMAND_START_FAILED` while its launch state is being resolved. Reusing that
identifier for different command inputs inside the same project returns
`IDEMPOTENCY_CONFLICT`. The same `client_request_id` may exist independently in
another project. `get_command(command_id=...)` uses the opaque global handle
without `project_id`; `get_command(client_request_id=...)` requires the owning
`project_id`. Bare `list_commands` aggregates retained commands and labels
project ownership, while `list_commands(project_id=...)` filters one project.

Only truncated terminal output returns a `read_output` next action by default.
`output_ref` values are `command:<id>:stdout` or `command:<id>:stderr`; offsets
are stream-specific absolute byte positions. Runtime limits bound active
commands, retained completed commands, per-command output, total output, and
retention time.

Each stream retains the earliest output (a frozen head segment, one eighth of
the per-stream budget) plus the most recent output (a rolling tail). When a
command produces more output than the budget, bytes between the head and the
tail are evicted permanently; `read_output` reports the loss via
`evicted_gap_bytes` and `omitted_bytes`. For output expected to exceed the
budget, redirect it to a file (`cmd > out.log 2>&1`) and page it with
`read_file` or `search_text` instead of relying on retained output.

Use `tty: true` only when a program requires a terminal. POSIX receives a real
PTY (`isatty()` is true). This build returns `TTY_UNSUPPORTED` on Windows rather
than labeling pipes as a TTY.

## Permission modes

- `safe`: blocks network-looking commands, shell expansion, inline scripts,
  destructive commands, outside-workspace arguments, and secret/loader env.
- `trusted`: enables normal local-development network, expansion, and inline
  snippets while retaining secret and destructive-command checks.
- `dangerous`: disables command permission gates and Landlock; use only inside
  an isolated container or VM.

These modes do not change the tool list. Direct path tools always route and
validate relative paths against the addressed project. `dangerous` still
disables MCP permission gates and Landlock, however, so it is not tenant
isolation: arbitrary shell commands retain the aggregate host authority of the
server process. Projects requiring different trust policies need separate
external process/container boundaries.

`--dangerously-fake-readonly-annotations` advertises every tool as read-only in
`tools/list` for clients that gate on annotations. It does not change the tool list
either, and it does not stop mutation or execution. `server_info` and the server
card keep reporting the real annotations. See
[permission-modes.md](permission-modes.md).
