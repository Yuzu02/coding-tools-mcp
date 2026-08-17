# Coding Tools MCP Boundary Findings

This document records boundary issues found while dogfooding this workspace with
real coding tasks across Java/Spring Boot, C, C++, Node/npm, TypeScript, Go,
Rust/Cargo, Python, FFmpeg downloads, interactive sessions, images, and git.

## Confirmed capabilities

- Java/Maven and Spring Boot can be created, tested, packaged, started, and
  verified with local HTTP requests.
- C and C++ builds work with gcc/g++, Make, CMake, and CTest.
- Node/npm and TypeScript package install, compile, and run flows work.
- Go modules and Rust/Cargo can download dependencies and execute programs.
- Direct HTTPS downloads work, including a static FFmpeg tarball.
- Long-running interactive sessions work with `write_stdin`.
- `view_image` can inspect generated PNG files.
- Security policy correctly blocks workspace escapes, secret-looking environment
  variables, and destructive commands.

## Issues found and current status

### 1. Git helper tools can falsely report `is_repo: false` — resolved

The workspace is a valid git repository and native `git` works under
`exec_command`, but `git_status`, `git_log`, and `git_diff` can report a
non-repository fallback. Reproducing native git without the configured global git
config produces Git's `dubious ownership` error for `/workspace`.

The runtime now routes Git helper subprocesses through `_git_env()`, which is
derived from `_command_env({})`. This keeps the helper tools aligned with the
command environment, including `GIT_CONFIG_GLOBAL` when it is intentionally
inherited. The helper methods also preserve explicit repository `workdir`
semantics.

### 2. Python package installation is blocked by Landlock read roots

`python3 -m venv` failed in `ensurepip`, and `pip install --target` failed when
pip's vendored distro code attempted to read `/etc/debian_version` for its
User-Agent.

Status: partially investigated. A narrow file-root Landlock change was attempted
for low-sensitivity OS metadata commonly read by language package managers
(`/etc/os-release`, `/etc/debian_version`, `/etc/lsb-release` as read-only file
roots), but pip's distro metadata read was still denied by Landlock path
traversal behavior. An additional experiment that granted `READ_DIR` on each
file root's parent directory was reverted: Landlock `path_beneath` rules apply
to the whole subtree, so it would have allowed listing directory names across
`/etc` without fixing pip. Broadening system read roots beyond the three
metadata files remains a follow-up item that requires a deliberate security
review.

### 3. Common argument aliases are rejected by strict schemas — resolved

The schemas intentionally use `additionalProperties: false`, which is good for
contract clarity but brittle for common coding-agent parameter names.

Examples originally hit during dogfooding:

- `exec_command` originally accepted `workdir`, not `cwd`.
- `read_file` originally accepted `start_line`/`end_line`, not `max_lines`.
- `git_status` accepts `path`/`include_untracked`/`max_entries`, not `short`.

`exec_command` now accepts `cwd` as an alias of `workdir`, and `read_file`
accepts `max_lines`; conflicting canonical/alias values are rejected.
`git_status` intentionally still does not accept `short`: its output is already
structured entries, so silently accepting the flag would be misleading.

### 4. Heredoc XML can be misclassified as an escaping path — resolved

Shell tokenization of a heredoc containing XML such as `<modelVersion>` can
produce tokens like `/modelVersion`, which the path scanner treats as an absolute
path escape.

`_check_command_paths()` now scans `strip_heredoc_payloads(cmd)` instead of the
raw command. Heredoc bodies are treated as stdin data, while redirection targets
on the operator line and live commands after the closing delimiter remain
visible to policy checks.

### 5. Service-level UV caches can be outside Landlock write roots — resolved

Long-running systemd units intentionally keep bootstrap caches under
`/var/cache/coding-tools-mcp*/uv`. A trusted/safe child command that inherited
that `UV_CACHE_DIR` could then fail under Landlock because command write access
is confined to the per-runtime tree under `/run/.../runtime/...`.

The runtime now rehomes inherited `UV_CACHE_DIR` to `<runtime>/cache/uv` and
inherited `XDG_CACHE_HOME` to `<runtime>/cache` for non-dangerous child commands.
Explicit per-command overrides remain intentional. `dangerous` mode preserves
its existing unrestricted environment semantics.

## Remaining known limitations

- `apt`/system package managers need `/etc/apt` and `/var/cache/apt`; they remain
  outside the current sandbox model and are not fixed here.
- Docker/Podman and several language ecosystems are not installed in the current
  image.
- The system lacks an `xz` executable; Python's `lzma` can still extract `.xz`
  archives as a fallback.
- A full `make ci` launched *through another trusted, Landlocked
  coding-tools-mcp instance* is not equivalent to the normal host/CI gate. The
  outer sandbox remains authoritative over every nested runtime. In particular,
  a service-level `CODING_TOOLS_MCP_RUNTIME_ROOT` must not be reused by nested
  test workspaces; executable temporary fixtures need an execute-capable outer
  path; host Git configuration can reference files such as
  `/etc/git/gitignore_global` that are outside the outer instance's read roots;
  and host PTY exhaustion is inherited. Run the canonical full suite from the
  host shell or CI. Focused tests that do not depend on those outer resources
  remain valid when dogfooding through the connector.
