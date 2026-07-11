# Changelog

## 0.2.0 - 2026-07-11

### Changed

- Replaced selectable tool profiles with one stable, truthfully annotated
  coding catalog. Permission modes still control command policy but never alter
  `tools/list`.
- Made `apply_patch` the sole direct file-mutation tool and added staged,
  baseline-checked, same-directory atomic replacement with multi-file rollback,
  mode/BOM/newline preservation, and structured retry errors.
- Changed model-facing `content` from a JSON mirror to bounded per-tool summaries
  and previews. Clients that parsed text as JSON must read
  `structuredContent`.
- Changed `exec_command` and `write_stdin` default yield to 10 seconds. Running
  or truncated results now provide explicit machine-readable `next_action`.
- Split active and retained process sessions and added concurrency, count, byte,
  and TTL limits. POSIX TTY requests now use a real PTY; Windows reports an
  explicit unsupported error in this build and uses portable graceful/forced
  process termination without assuming `SIGKILL` exists.
- Upgraded the primary protocol to MCP `2025-11-25` while retaining explicit
  `2025-06-18` compatibility.

### Added

- Independent per-`Mcp-Session-Id` HTTP runtimes, session termination, standard
  cancellation mapping, batch rejection, and strict protocol-header checks.
- OAuth protected-resource metadata, Authorization Code + PKCE S256, exact
  redirect binding, one-hour client-bound tokens, and RFC 7591 dynamic client
  registration.
- Automatic bounded root project-instruction loading during initialization.
- Streaming/bounded file reads, early-stopping ripgrep, batched Git ignore
  checks, and iterator-based traversal.
- Dedicated patch, process, result, project-context, OAuth, error, and HTTP
  session modules plus regression coverage for their boundary conditions.
- Reproducible dogfood efficiency metrics and a five-run 0.1.7/0.2.0 comparison;
  serialized tool-result bytes fell 37.279% with unchanged completion and call
  counts on the deterministic workload.

### Removed

- `--tool-profile`, `CODING_TOOLS_MCP_TOOL_PROFILE`, and all launcher/UI/control
  plane profile selectors.
- Duplicate image base64/data URLs and the `view_image.output` selector. Image
  bytes now appear once in one MCP image block.
- JSON-RPC batch handling and the unimplemented logging capability declaration.

### Security

- Public tunnel documentation no longer recommends anonymous read-only mode:
  the fixed catalog includes mutation and execution, so remote access must be
  authenticated.
- Forwarded headers are trusted only when explicitly enabled; browser origins,
  OAuth resources, clients, redirect URIs, and token auth methods are bound
  exactly.
