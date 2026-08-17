# Multiplatform Services Launcher Design

Date: 2026-08-05

Status: approved for implementation

Base branch: `fix/git-workdir-resolution` at
`9893bc88ab0fbfc0b410e4835915abfe9f7387e7`

Implementation branch: `feat/multiplatform-services-launcher`

## Purpose

Add a repository-owned, Python-based launcher that starts and supervises both
`coding-tools-mcp` and OpenAI `tunnel-client` on Windows, Linux, and macOS.
The intended clean-machine workflow is:

```text
git clone <fork>
cd coding-tools-mcp
mise install
uv run --locked python scripts/start_services.py <arguments>
```

The launcher must replace the useful behavior of the existing local PowerShell
launcher while removing Windows-only assumptions. It must be configurable by
CLI arguments and environment variables, support both existing and generated
tunnel profiles, use `uv` for dependency synchronization and execution, and
leave enough diagnostics to investigate failures after the processes exit.

## Scope

The feature includes:

- a root `mise.toml` that pins the repository's declared Python, Node, Rust,
  `uv`, and OpenAI tunnel toolchains;
- a thin `scripts/start_services.py` CLI entry point;
- focused launcher modules under `scripts/launcher/`;
- automatic or skipped `uv sync --locked` behavior;
- MCP-only and MCP-plus-tunnel operation;
- existing tunnel profile name, profile directory, and profile file modes;
- generated temporary or persistent tunnel profile modes;
- readiness checks, process supervision, signal handling, cleanup, logs, and a
  machine-readable run manifest;
- best-effort tunnel diagnostics before shutdown;
- unit and integration tests that do not require real OpenAI credentials;
- operator documentation, including a Linux `systemd` example.

The feature does not add a new MCP tool, modify the MCP protocol, embed API
credentials in the repository, or turn the launcher into a general-purpose
process manager.

## Design principles

1. **CLI-first and fully configurable.** Defaults are useful for local work,
   but paths, ports, modes, executables, timeouts, profile selection, logging,
   and tunnel control-plane inputs are all overridable.
2. **Official tools own their formats.** Generated profiles are created with
   `tunnel-client init`, not by maintaining a second YAML serializer in this
   repository. This reduces schema drift as `tunnel-client` evolves.
3. **Secrets stay out of MCP and diagnostics.** The control-plane credential is
   passed only to `tunnel-client`; the MCP child receives an explicitly scrubbed
   environment. Profiles store `env:` or `file:` references, never literal
   secrets supplied on the command line.
4. **No shell command construction.** Child processes are launched with argv
   arrays and `shell=False` so paths containing spaces behave consistently and
   user-provided values are not reinterpreted by a shell.
5. **One failed child terminates the run.** If MCP or the tunnel exits, the
   launcher records the failure, captures diagnostics, and stops the sibling.
6. **Diagnostics never block cleanup.** Status collection is best effort and
   cannot prevent child process termination.

## Repository layout

```text
mise.toml
scripts/
  start_services.py
  launcher/
    __init__.py
    config.py
    diagnostics.py
    processes.py
    tunnel.py
tests/
  test_launcher_config.py
  test_launcher_diagnostics.py
  test_launcher_processes.py
  test_launcher_tunnel.py
  test_launcher_integration.py
docs/
  services-launcher.md
```

`start_services.py` performs argument parsing and delegates to the modules. The
module boundaries are:

- `config.py`: argument definitions, environment precedence, `.env` loading,
  path resolution, validation, and immutable resolved configuration objects;
- `tunnel.py`: profile selection/generation, `tunnel-client doctor`, health URL
  discovery, and tunnel diagnostic commands;
- `processes.py`: child startup, output capture, readiness waits, supervision,
  cross-platform process-group termination, and exit-code normalization;
- `diagnostics.py`: unique run directories, atomic JSON writes, manifest state,
  log paths, HTTP diagnostic downloads, and redaction-safe error records.

## Toolchain contract

The root `mise.toml` pins the tools required for a clone-based run and the
repository's first-party verification workflows:

```toml
[tools]
python = "3.13.12"
uv = "0.12.1"
node = "24.15.0"
rust = "1.97.1"
"github:openai/tunnel-client" = "0.0.10"
```

Python 3.13.12 is the validated interpreter used by `uv` in the baseline
worktree. The project itself continues to declare `requires-python >=3.11`;
the `mise` pin is the reproducible contributor and VPS runtime, not a reduction
of the package's supported Python range. Node covers the published npm launcher
and the Remotion video project. Rust covers the real-workload and Docker smoke
contracts exercised by CI even though the repository does not currently contain
a root `Cargo.toml`.

The file also defines:

```toml
[env]
PYTHONUTF8 = "1"
PYTHONDONTWRITEBYTECODE = "1"
UV_MANAGED_PYTHON = "1"
UV_PYTHON = "3.13.12"
```

Tasks provide convenience rather than hidden behavior:

- `mise run setup`: `uv sync --locked`;
- `mise run setup-dev`: `uv sync --locked --extra dev`;
- `mise run start -- <args>`: execute the launcher with `uv run --locked`;
- `mise run check-npm`: execute the npm launcher tests and package dry-run;
- `mise run test-launcher`: execute only launcher tests;
- `mise run verify`: lint, run the complete Python test suite, and run the npm
  launcher gate. Rust remains installed for the existing opt-in real-workload
  checks rather than making every local verification compile external fixtures.

The launcher still supports direct invocation through `uv`; it does not invoke
`mise` itself. This keeps `mise install` as tool provisioning and `uv` as the
Python environment and execution authority.

## Configuration model

Configuration precedence is:

```text
CLI argument > existing process environment > selected .env file > default
```

The launcher loads `.env` without overwriting a variable already present in the
process environment. The default file is `<workspace>/.env`; operators may use
`--env-file PATH` or `--no-env-file`. Invalid `.env` entries fail closed with a
line number but never echo the value.

Primary CLI groups are:

### MCP

- `--workspace PATH` (required unless `CODING_TOOLS_MCP_WORKSPACE` is set);
- `--mcp-repository PATH` (default: repository root containing the launcher);
- `--host HOST` (default `127.0.0.1`);
- `--port PORT` (default `8000`);
- `--permission-mode {safe,trusted,dangerous}` (default `trusted`);
- `--shell-env-inherit POLICY` (default `all`);
- `--http-session-mode {stateful,ephemeral}` (default `ephemeral`);
- `--enable-view-image` / `--no-enable-view-image`;
- repeatable `--mcp-arg VALUE` for forward-compatible server flags.

`dangerous` mode prints a prominent warning. An optional
`--require-dangerous-confirmation` behavior is not used because it would make
non-interactive VPS startup unreliable; the explicit CLI value is the consent.

### Dependency synchronization

- `--sync` (default): run `uv sync --locked` before capability checks;
- `--no-sync`: validate and start using the existing environment;
- `--sync-extra NAME`: repeatable optional extras passed to `uv sync`;
- `--sync-only`: synchronize, validate the resolved tools, and exit;
- `--uv PATH_OR_NAME` (default `uv`).

The MCP child is always launched as:

```text
uv run --project <mcp-repository> --locked python -m coding_tools_mcp ...
```

### Tunnel selection

- `--no-tunnel`: supervise only MCP;
- `--tunnel-client PATH_OR_NAME` (default `tunnel-client`);
- `--tunnel-profile NAME` plus optional `--tunnel-profile-dir PATH`;
- `--tunnel-profile-file PATH`;
- generated profile inputs described below.

The profile selection modes are mutually exclusive. A clear validation error is
returned when arguments from multiple modes are mixed.

### Generated tunnel profile

Generated mode requires `--tunnel-id`. It accepts:

- `--control-plane-base-url` (default `https://api.openai.com`);
- `--control-plane-url-path`;
- `--control-plane-api-key-ref` (default
  `env:CONTROL_PLANE_API_KEY`);
- `--tunnel-health-listen-addr` (default `127.0.0.1:8080`);
- `--open-tunnel-web-ui`;
- `--generated-profile-name` (default derived from the tunnel ID without
  exposing the full ID in logs);
- `--write-tunnel-profile PATH` to persist the generated YAML;
- `--force-profile-write` to replace an existing destination.

Without `--write-tunnel-profile`, the launcher creates a private temporary
profile directory, calls `tunnel-client init`, uses it for the run, and deletes
it during cleanup. With a persistent destination, the parent directory is
created securely and `tunnel-client init --force` is used only when the operator
explicitly supplies `--force-profile-write`.

The generated MCP target defaults to the launcher endpoint:

```text
http://<host>:<port>/mcp
```

`--tunnel-mcp-server-url` overrides that value for proxy or container layouts.

### Runtime and diagnostics

- `--startup-timeout SECONDS` (default `60`);
- `--shutdown-timeout SECONDS` (default `10`);
- `--poll-interval SECONDS` (default `0.25`);
- `--logs-root PATH` (default `<repository>/.runtime/services`);
- `--tunnel-health-listen-addr` and `--tunnel-health-url-file`;
- `--tunnel-log-minutes` (default `120`);
- `--keep-generated-profile` for debugging without persistence semantics;
- `--dry-run` to print a redacted execution plan without synchronization or
  child startup;
- `--doctor-only` to run validation and `tunnel-client doctor`, then exit.

Every option has an environment-variable counterpart prefixed with
`CODING_TOOLS_SERVICES_`, except native `tunnel-client` credential variables
such as `CONTROL_PLANE_API_KEY`.

## Tunnel profile handling

### Existing profile by name

The launcher starts and diagnoses the tunnel using:

```text
tunnel-client doctor --profile <name> [--profile-dir <dir>] --json
tunnel-client run --profile <name> [--profile-dir <dir>] ...
```

### Existing profile file

The equivalent commands use `--profile-file <path>`. The launcher verifies that
the file exists but does not parse or rewrite it.

### Generated profile

The launcher delegates profile materialization to:

```text
tunnel-client init
  --profile <name>
  --profile-dir <private-dir>
  --tunnel-id <id>
  --mcp-server-url <url>
  --control-plane-base-url <url>
  --control-plane-api-key-ref <env:...|file:...>
  [--control-plane-url-path <path>]
  [--health-listen-addr <addr>]
  [--open-web-ui]
```

The launcher validates the secret reference format before invoking the command.
Literal key values are rejected. This prevents accidental key storage in shell
history, manifests, and generated YAML.

## Process and readiness behavior

The launcher opens four binary log files in the run directory and directs each
child's stdout and stderr into its corresponding file. It does not buffer
unbounded child output in Python memory.

On POSIX, children start in new sessions and cleanup signals the process group.
On Windows, children start in new process groups; graceful interruption is
attempted first, followed by `taskkill /T /F` when descendants remain after the
shutdown timeout. PID reuse risk is limited by retaining live `Popen` handles
and checking process state before escalation.

MCP readiness is a bounded TCP connection to the configured host and port. A
plain HTTP GET is not used because `/mcp` expects MCP protocol traffic and may
legitimately reject an ordinary request. The wait aborts immediately when the
MCP child exits.

The tunnel always receives a `--health.url-file` override in a run-private
location. This permits fixed or ephemeral admin ports and gives the launcher a
reliable resolved base URL. Tunnel readiness requires the URL file plus a
successful `/readyz` response. Existing profile values remain otherwise intact
because command-line flags have documented precedence over YAML.

After both services are ready, the supervisor polls their process handles. The
first unexpected exit determines the primary failure. A child exit code of zero
while its sibling is still expected to run is normalized to launcher exit code
`1`; explicit user interruption returns `130` on every platform.

## Environment isolation and security

The parent process creates separate environment dictionaries:

- **MCP environment:** inherits the configured shell environment but removes
  `CONTROL_PLANE_API_KEY`, `OPENAI_API_KEY` when it is acting as the tunnel
  credential fallback, client certificates/keys, extra control-plane headers,
  and launcher-only secret references;
- **tunnel environment:** receives the loaded process environment required to
  resolve `env:` references;
- **probe environment:** capability and help probes use the MCP environment;
  tunnel doctor uses the tunnel environment.

No environment dump is written. Commands recorded in the manifest are redacted
and omit secret-bearing argument values. The generated profile name and run
directory do not include the complete tunnel ID. Temporary profile directories
use owner-only permissions where supported.

The launcher refuses:

- literal control-plane API keys in `--control-plane-api-key-ref`;
- a generated profile without a tunnel ID;
- a profile destination that already exists unless replacement is explicit;
- a port already accepting TCP connections;
- workspace or repository paths that do not exist;
- repository paths without `pyproject.toml` and `uv.lock`;
- unsupported server flags detected by the capability probe;
- remote tunnel admin listeners unless `--allow-remote-tunnel-ui` is explicit.

## Diagnostics and manifest

Each invocation allocates:

```text
.runtime/services/YYYYMMDD-HHMMSS[-NN]/
  run.json
  coding-tools-mcp.stdout.log
  coding-tools-mcp.stderr.log
  tunnel-client.stdout.log
  tunnel-client.stderr.log
  tunnel-health.url
  tunnel-status.json
  tunnel-health.json
  tunnel-events.tar.gz
  diagnostics-errors.json
```

`.runtime/` is ignored by Git.

`run.json` is written atomically and advances through:

```text
starting
synchronizing
validating
starting-mcp
waiting-for-mcp
starting-tunnel
waiting-for-tunnel
running
capturing-diagnostics
stopping
stopped | failed
```

The manifest includes timestamps, non-secret resolved configuration, tool
versions, child PIDs and exit codes, readiness timestamps, paths to artifacts,
the primary failure, and diagnostic-capture errors. It never includes `.env`
contents, API keys, private key material, raw extra headers, or full process
environments.

Before stopping a live tunnel, the launcher attempts:

1. GET `/api/status` into `tunnel-status.json`;
2. `tunnel-client health --url <base> --require-control-plane-poll --json` into
   `tunnel-health.json`;
3. GET `/api/logs/export?minutes=<n>` into `tunnel-events.tar.gz`.

Failures are recorded in `diagnostics-errors.json` and cleanup continues.

## Error handling

Configuration and preflight errors are concise, actionable, and exit with code
`2`. Synchronization, doctor, startup, or runtime child failures exit nonzero and
point to the run directory. Child stderr is not dumped wholesale to the terminal
because it may be large; the launcher prints the last bounded, decoded lines
when useful and always names the complete log file.

Keyboard interrupt and termination signals trigger the same cleanup path. A
second interrupt escalates immediately to forced process-tree termination.

## Testing strategy

Tests use only the Python standard library plus existing development
dependencies. No test contacts OpenAI or requires a real tunnel credential.

### Unit tests

- precedence across CLI, environment, `.env`, and defaults;
- `.env` parsing and secret-safe errors;
- argument conflict and path validation;
- MCP and tunnel argv generation;
- environment scrubbing;
- generated-profile command construction and literal-secret rejection;
- atomic manifest writes and unique run-directory allocation;
- redaction and bounded log-tail behavior;
- readiness timeout and child-early-exit behavior;
- exit-code normalization and process-group cleanup decisions.

### Integration tests

Temporary Python helper processes simulate MCP and `tunnel-client`:

- successful MCP readiness followed by successful tunnel readiness;
- MCP exits before opening its port;
- tunnel doctor fails before startup;
- tunnel exits while MCP remains alive;
- Ctrl+C cleanup terminates both child trees;
- `--no-tunnel`, `--sync-only`, `--doctor-only`, and `--dry-run` paths;
- generated temporary profile cleanup and persistent profile retention.

The integration tests inject executable paths and command runners; they do not
depend on the actual installed `tunnel-client` binary. One opt-in smoke test may
validate `tunnel-client --version` and `uv --version` when both are available.

### Verification gates

Implementation completion requires:

```text
uv run --locked python -m ruff check scripts tests
uv run --locked python -m unittest <launcher test modules> -v
uv run --locked python -m unittest discover -s tests -p "test_*.py"
mise tasks ls
mise run test-launcher
```

Windows validates process-tree cleanup and ordinary profile resolution. Linux
or WSL validates POSIX process-group cleanup and the documented `systemd`
command. Tests that are platform-specific are explicitly skipped elsewhere.

## Documentation and deployment

`docs/services-launcher.md` documents:

- clean clone setup with `mise install`;
- MCP-only operation;
- existing profile name and profile file examples;
- generated temporary and persistent profile examples;
- `.env` and file-based secret references;
- automatic sync and `--no-sync`;
- diagnostics layout and troubleshooting;
- trusted versus dangerous permission modes;
- a `systemd` unit using `mise exec -- uv run --locked python
  scripts/start_services.py ...` and an external `EnvironmentFile`.

The unit example uses `Restart=on-failure`, a fixed working directory, a
non-root service account, and no secrets embedded in `ExecStart`.

## Compatibility and migration

The existing external PowerShell launcher is not deleted because it is outside
this repository and may remain useful during transition. The Python launcher
preserves its essential operational guarantees while improving portability and
configuration. The repository documentation identifies Python as the canonical
launcher after the feature lands.

Existing MCP startup commands remain supported. Operators who do not use the
OpenAI tunnel can pass `--no-tunnel` or continue launching the server directly.

## Acceptance criteria

The feature is accepted when:

1. a clean clone can install Python, `uv`, and OpenAI `tunnel-client` with one
   `mise install`;
2. the launcher runs exclusively through `uv` and can synchronize automatically
   or honor `--no-sync`;
3. profile name, profile file, temporary generated profile, and persistent
   generated profile modes are supported and mutually validated;
4. no literal tunnel credential enters a generated profile, MCP child
   environment, command manifest, or log produced by launcher code;
5. MCP and tunnel startup/readiness failures produce deterministic nonzero exits
   and a complete diagnostics directory;
6. Ctrl+C and child failure terminate both process trees on Windows and POSIX;
7. `ephemeral`, project-scoped `list_skills` / `read_skill`, reliable command
   recovery, and Git workdir behavior from the base branch continue passing
   their tests;
8. launcher tests and the complete repository test suite pass;
9. the implementation branch is pushed to the user's fork with its exact commit
   and clone command reported.
