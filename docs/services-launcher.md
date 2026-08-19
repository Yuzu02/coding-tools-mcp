# Multiplatform MCP and OpenAI Tunnel Launcher

`scripts/start_services.py` is the repository-owned supervisor for running this
checkout of `coding-tools-mcp` together with OpenAI `tunnel-client`. It is the
canonical clone-based launcher on Windows, Linux, and macOS.

It uses `mise` only to provision pinned tools and `uv` to synchronize and run
the Python checkout. It does not install the package globally, embed secrets in
the repository, or construct child commands through a shell.

The launcher has two explicit configuration modes. Developer compatibility
mode keeps the historical `coding-tools.toml` + `coding-tools.local.toml` +
environment + CLI precedence. System deployment mode selects one strict
HostConfig v2 and uses the same canonical HostConfig parser/model as the MCP
runtime; the launcher does not maintain a second TOML implementation.

## Clean clone setup

Install `mise`, clone the required branch, and trust the checked-in toolchain
configuration once:

```bash
git clone https://github.com/your-org/coding-tools-mcp.git
cd coding-tools-mcp

mise trust
mise install
mise run setup
```

`mise install` provisions the versions declared in `mise.toml`:

- Python 3.14.7;
- `uv` 0.12.5;
- Node 24.19.0;
- Rust 1.97.1;
- `github:openai/tunnel-client` 0.0.11.

The project environment separately sets `UV_PYTHON=3.13.12`, so `uv` resolves
the project interpreter independently of the Python version used to bootstrap
`mise` tasks.

Node and Rust are included because the repository's npm and real-workload
verification paths use them. The launcher itself is Python and runs the MCP
checkout exclusively through `uv run --project ... --locked`.

## Developer compatibility mode

The launcher needs a workspace to expose. The repository containing the
launcher and the workspace exposed through MCP may be different directories.

Start MCP and the default `coding-tools-dev` tunnel profile:

```bash
mise run start -- --workspace /srv/repos --tunnel-profile coding-tools-dev
```

The equivalent direct command is:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-profile coding-tools-dev
```

Start only the MCP server:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --no-tunnel
```

Example defaults are:

```text
host:                127.0.0.1
port:                8000
permission mode:     trusted
HTTP transport:      stateless
tunnel profile:      coding-tools-dev
tunnel admin:        127.0.0.1:8080
```

Every value can be overridden by an argument or its
`CODING_TOOLS_SERVICES_...` environment-variable counterpart.

Compatibility precedence is:

```text
built-in defaults
< coding-tools.toml
< coding-tools.local.toml
< supported environment overrides
< explicit CLI overrides
```

This mode remains useful for interactive clone-based development. It is not the
normal long-running systemd deployment authority.

## System HostConfig v2 mode

A long-running deployment selects one private HostConfig explicitly:

```bash
uv run --locked python scripts/start_services.py \
  --host-config /etc/coding-tools-mcp/config.toml
```

The launcher resolves HostConfig once, derives deployment/supervision settings
from that immutable snapshot, and starts the MCP child with the minimal
configuration argv:

```text
python -m coding_tools_mcp --host-config /etc/coding-tools-mcp/config.toml
```

HostConfig mode does **not** load `<workspace>/.env` and rejects legacy flags
that would compete with host-owned workspace, listener, permission, tunnel, or
deployment settings. Secret values are resolved narrowly by their actual
consumer. A tunnel `env:` secret reference remains available to tunnel-client
but its named environment variable is removed from the MCP child environment.

Run the deterministic deployment preflight without synchronization, capability
probes, or long-lived child startup:

```bash
uv run --locked python scripts/start_services.py \
  --host-config /etc/coding-tools-mcp/config.toml \
  --preflight
```

Preflight checks registered-root visibility, listener availability, external
runtime/state/cache writability, source/runtime separation, exact
`serena-agent==1.5.3` when semantic mode is enabled, and tunnel profile-file
metadata. Findings and the configuration fingerprint are serialized without
secret values.

## Dependency synchronization

By default, every invocation runs:

```bash
uv sync --project <mcp-repository> --locked
```

Optional extras may be repeated:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --sync-extra dev \
  --sync-extra image \
  --no-tunnel
```

Use an already synchronized environment with `--no-sync`:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --no-tunnel \
  --no-sync
```

Synchronize and validate the local MCP checkout without starting children:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --no-tunnel \
  --sync-only
```

For a long-running service, synchronize during deployment and use `--no-sync`
in the service unit. This avoids making every restart depend on package-index
availability. For an interactive clone, automatic synchronization is the safer
default.

Service-level cache variables such as `UV_CACHE_DIR=/var/cache/.../uv` belong
to bootstrap/synchronization. In `safe` or `trusted` mode, workspace commands
do not reuse that external cache path: inherited `UV_CACHE_DIR` and
`XDG_CACHE_HOME` are rehomed under the runtime's own `cache` directory so
Landlock can write them. `dangerous` mode intentionally keeps the inherited
environment unchanged.

## Existing tunnel profiles

### Profile by name

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-profile coding-tools-dev
```

Override the profile directory when profiles do not live in the platform
default location:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-profile coding-tools-dev \
  --tunnel-profile-dir /etc/coding-tools-mcp/tunnel-profiles
```

### Specific profile file

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-profile-file /etc/coding-tools-mcp/tunnel.yaml
```

Existing profiles are not parsed or rewritten by this repository. The launcher
passes them to `tunnel-client doctor` and `tunnel-client run`.

## Generated tunnel profiles

Generated profiles are materialized by the official `tunnel-client init`
command. The launcher never maintains a duplicate YAML serializer.

### Temporary profile

```bash
export CONTROL_PLANE_API_KEY='...'

uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-id tunnel_0123456789abcdef \
  --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY
```

The generated profile stores `env:CONTROL_PLANE_API_KEY`, not the key value.
Its private temporary directory is removed during cleanup. Add
`--keep-generated-profile` only when inspecting a failed local run.

### Persistent generated profile

Generate, validate, and retain a profile without starting services:

```bash
export CONTROL_PLANE_API_KEY='...'

uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-id tunnel_0123456789abcdef \
  --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY \
  --write-tunnel-profile /etc/coding-tools-mcp/tunnel.yaml \
  --doctor-only
```

An existing destination is refused. Replacement requires the explicit
`--force-profile-write` flag.

### File-based control-plane secret

```bash
sudo install -m 600 /dev/null /etc/coding-tools-mcp/control-plane.key
sudoedit /etc/coding-tools-mcp/control-plane.key

uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-id tunnel_0123456789abcdef \
  --control-plane-api-key-ref file:/etc/coding-tools-mcp/control-plane.key
```

Only `env:NAME` and `file:/path` references are accepted. Literal keys passed
to `--control-plane-api-key-ref` are rejected so they cannot enter shell
history, generated YAML, manifests, or diagnostic messages.

Generated mode also supports:

```text
--control-plane-base-url URL
--control-plane-url-path PATH
--tunnel-mcp-server-url URL
--generated-profile-name NAME
--open-tunnel-web-ui
--tunnel-health-listen-addr HOST:PORT
```

Non-loopback tunnel admin listeners require
`--allow-remote-tunnel-ui`. Avoid exposing the admin UI unless the host has an
independent access-control boundary.

## Environment files in compatibility mode

Configuration precedence is:

```text
CLI argument > existing process environment > selected .env > default
```

The default file is `<workspace>/.env`. Existing process variables are never
overwritten. Select another file with `--env-file PATH`, or disable loading with
`--no-env-file`.

This precedence and automatic workspace `.env` lookup apply only to developer
compatibility mode. `--host-config` never loads a workspace `.env`; deployment
secrets must already exist in the launcher process environment or in the
absolute `file:` locations named by HostConfig secret references.

Example:

```dotenv
CONTROL_PLANE_API_KEY=replace-me
CODING_TOOLS_SERVICES_TUNNEL_PROFILE=coding-tools-dev
CODING_TOOLS_SERVICES_PERMISSION_MODE=trusted
```

Do not commit `.env` or generated profiles containing local paths. The MCP
child receives a separate environment with `CONTROL_PLANE_API_KEY`,
`OPENAI_API_KEY`, control-plane certificates, private keys, extra headers, and
tunnel-only variables removed.

## Validation-only and dry-run modes

Validate an existing or generated tunnel profile and exit:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-profile-file /etc/coding-tools-mcp/tunnel.yaml \
  --doctor-only
```

Print a redacted execution plan without synchronization, profile generation, or
child startup:

```bash
uv run --locked python scripts/start_services.py \
  --workspace /srv/repos \
  --tunnel-id tunnel_example \
  --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY \
  --dry-run
```

## MCP server options

Common options include:

```text
--mcp-repository PATH
--host HOST
--port PORT
--permission-mode safe|trusted|dangerous
--shell-env-inherit POLICY
--enable-view-image / --no-enable-view-image
--mcp-arg VALUE
```

`trusted` allows normal development network access, shell expansion, and inline
scripts, but retains destructive-command checks and secret filtering.
`dangerous` disables command permission gates and is appropriate only inside an
externally isolated VM or container. The launcher prints a warning whenever
`dangerous` is selected.

HTTP is stateless in upstream 0.3. Clients that reconnect or reinitialize do
not receive an MCP session identifier, and the launcher does not pass any
session-retention flags through to the server. Workspace-owned command handles
remain recoverable through `command_id`.

## Process lifecycle

The launcher performs these stages:

```text
optional uv sync
tool and MCP capability probes
tunnel profile resolution or generation
tunnel-client doctor
MCP startup and TCP readiness
tunnel startup and /readyz readiness
supervision
diagnostic capture
tunnel shutdown, then MCP shutdown
```

If either child exits unexpectedly, the launcher records the primary failure,
captures tunnel diagnostics, and terminates the sibling. `Ctrl+C` uses the same
cleanup path and exits with code 130. A second interrupt forces immediate tree
termination.

On POSIX, children run in new sessions and cleanup signals their process groups.
On Windows, children use new process groups; graceful `CTRL_BREAK_EVENT` is
followed by `taskkill /T /F` if the shutdown timeout expires.

## Diagnostics

Every invocation allocates a directory below `.runtime/services/` unless
`--logs-root` selects another location:

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

`run.json` records the redacted configuration, state transitions, tool versions,
PIDs, readiness timestamps, exits, and the primary failure. It does not contain
environment dumps, `.env` contents, literal API keys, raw extra headers, or raw
child argv values.

Tunnel status, health, and event archive capture are independent and best
effort. A diagnostic endpoint failure never prevents process cleanup.

## Linux `systemd` deployment

Create a non-root account and deploy the checkout:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin codingtools
sudo install -d -o codingtools -g codingtools /opt/coding-tools-mcp /srv/coding-tools-mcp/workspace
sudo -u codingtools git clone \
  https://github.com/your-org/coding-tools-mcp.git \
  /opt/coding-tools-mcp/repository

cd /opt/coding-tools-mcp/repository
sudo -u codingtools mise trust
sudo -u codingtools mise install
sudo -u codingtools mise run setup
```

Store secrets outside the repository:

```bash
sudo install -m 600 -o codingtools -g codingtools /dev/null /etc/coding-tools-mcp/service.env
sudoedit /etc/coding-tools-mcp/service.env
```

Example `/etc/coding-tools-mcp/service.env`:

```dotenv
CONTROL_PLANE_API_KEY=replace-me
```

Example private `/etc/coding-tools-mcp/config.toml` using only synthetic
project paths and IDs. Credential providers are deliberately not declared in
HostConfig; the launcher derives their registry and broker from this file's
location and its `runtime.state_root`:

```toml
config_version = 2

[runtime]
bootstrap_workspace = "/srv/projects/example"
runtime_root = "/var/lib/coding-tools-mcp/runtime"
state_root = "/var/lib/coding-tools-mcp/state"
cache_root = "/var/lib/coding-tools-mcp/cache"

[transport]
kind = "http"
host = "127.0.0.1"
port = 8000

[security]
permission_mode = "dangerous"
shell_env_inherit = "all"
allow_network = true
auth_mode = "noauth"

[extensions]
enabled = ["projects"]

[extensions.projects.registry.example]
root = "/srv/projects/example"

[deployment]
mcp_repository = "/opt/coding-tools-mcp/repository"
sync = false
logs_root = "/var/lib/coding-tools-mcp/logs"

[deployment.tunnel]
mode = "profile-file"
profile_file = "/etc/coding-tools-mcp/tunnel.yaml"
api_key_ref = "env:CONTROL_PLANE_API_KEY"
```

### Dynamic credential providers

The host-owned provider registry is the only credential-provider authority for
HostConfig deployments. It is the `credentials.d` directory beside the
selected HostConfig, and the broker is always `<state-root>/credentials` (for
the example above, `/var/lib/coding-tools-mcp/state/credentials`). The service
account must be able to read the registry and its own broker subtrees; registry
fragments should be root-managed and published atomically.

When using `scripts/credentials.py provision --apply`, pass the numeric
`--service-uid <service-account-uid> --service-gid <service-account-gid>` for
the account named by the unit's `User=`/`Group=`. The CLI uses those values to
own and later audit the broker tree; they are not the invoking administrator's
identity. `doctor` uses the same pair for ownership/mode checks, while
`doctor --system` additionally requires explicit root and performs its
systemd-status query against `coding-tools-mcp-unified.service` by default.
If the deployment uses another unit name, pass it explicitly with
`--system-unit <unit-name>`; this keeps the doctor check tied to the unit that
actually runs the service.

Each `credentials.d/*.toml` fragment contains one provider and names no secret
values:

```toml
name = "example"
commands = ["example-cli"]
read_roots = ["/var/lib/coding-tools-mcp/state/credentials/example/read-only"]
write_roots = ["/var/lib/coding-tools-mcp/state/credentials/example/state"]
env_passthrough = ["EXAMPLE_TOKEN"]
env_paths = ["EXAMPLE_CLI_CONFIG_DIR=/var/lib/coding-tools-mcp/state/credentials/example/state"]
```

`name` and each command are safe basenames. Every read/write root and every
`env_paths` path must be a canonical descendant of that provider's broker
subtree; symlinked roots and paths outside it are rejected. `env_passthrough`
contains variable names only. Values come from the service environment and
are granted only to the matching simple executable. Secret-like variable names
are rejected from `env_paths`. `HOME`, `PATH`, temporary roots, and inherited
XDG values cannot be overridden. A provider may set `XDG_CACHE_HOME`,
`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, or `XDG_STATE_HOME` only through an
`env_paths` value that remains inside its own broker subtree; this lets CLIs
with XDG state use provider-owned storage without widening their authority.
Never bind or copy a personal home directory: provision the required files
into the provider's broker subtree instead.

The registry is checked lazily before each `exec_command` and `server_info`.
Changes to fragment names, metadata, size, timestamps, or contents create a
new generation without restarting MCP. A malformed or unavailable generation
is fail-closed: no providers, roots, or provider environment values are
authorized, and `server_info.credential_providers.registry.health` is
`invalid`. A healthy registry has `health = "healthy"`, a generation and a
fingerprint. An empty configured directory is healthy with no providers.

When a registry is configured, every command—not only provider commands—is
started through the credential-isolation Landlock profile. Non-provider
commands receive no broker root; a selected provider receives only its own
declared roots. This all-command enforcement is the trade-off that prevents a
`dangerous` non-provider command from opening every provider store: dangerous
permission mode does not disable registry isolation. If the credential
Landlock profile cannot be created or verified, the command is not started
(fail closed). `server_info.credential_providers.filesystem_isolation` reports
the backend, availability, and `enforced_for = "all_exec"`.

The Linux profile permits directory discovery (`READ_DIR`) across the host so
toolchains can canonicalize the current working directory and search ancestor
directories. That grant does not include `READ_FILE`, execution, or write
rights: non-provider commands still cannot open provider files, and provider
commands receive file access only to their declared broker subtree.
Because Landlock has no traversal-only directory permission, this compatibility
grant can expose directory names/metadata to child processes. Provider names
are not treated as credentials; credential confidentiality is enforced at
file-open/write access. A future mount-namespace layer may tighten metadata
visibility independently without changing the broker authorization model.

For operations and dry-run behavior, use
[credential-provider-migration.md](credential-provider-migration.md). The
`credentials` tool is host-only and is never exposed as an MCP tool.

### Generic Git provider

For a Git provider, keep GitHub authentication separate from Git commit
metadata. The broker source is staged by root and must contain one canonical
author/committer identity chosen by the operator. Configure that identity in a
broker-owned Git config and configure GitHub CLI authentication in a separate
broker-owned CLI directory. Pass them to matching commands with
`GIT_CONFIG_GLOBAL` and `GH_CONFIG_DIR`; do not rely on a personal home
directory. This repository's locally pinned identity is not an automatic
deployment property.

The host-only CLI takes global registry, broker, and service identity options
before `provision`. Its environment-path options are repeatable and use paths
relative to the provider broker. Repeat `--env-passthrough NAME` after the
subcommand only for an explicitly allowlisted service variable; it accepts a
name, not a value:

```sh
uv run --locked python scripts/credentials.py \
  --registry-dir <registry-dir> --broker-dir <broker-dir> \
  --service-uid <service-account-uid> --service-gid <service-account-gid> provision \
  --name git-provider --command git --command gh \
  --source <operator-reviewed-source-store> \
  --env-path GIT_CONFIG_GLOBAL=gitconfig \
  --env-path GH_CONFIG_DIR=gh \
  --env-passthrough <approved-variable-name>
```

The source must be reviewed and staged by root before an operator applies the
plan. `gh auth status` is a safe identity check for the brokered GitHub CLI
configuration; it does not prove Git commit author/committer metadata. Never
use a command that reads or prints credential contents.

`ProtectHome=tmpfs` still applies outside the MCP process. Providers must use
their broker subtrees, not stores under a personal home, so no home bind is
needed for credential discovery. A service that must inherit non-secret
Mise-managed user tools may bind only the two Mise subtrees read-only
(`~/.config/mise` and `~/.local/share/mise`), set `MISE_CONFIG_DIR` and
`MISE_DATA_DIR` to those paths, and retain `ProtectHome=tmpfs`. Never bind the
home root, Git/GitHub config, or any credential store; project `mise.toml`
files are already available through the registered project binds.

The all-command credential Landlock profile preserves that same three-level
Mise hierarchy instead of flattening it: system config/data (`/etc/mise` and
`MISE_SYSTEM_DATA_DIR`), user config/tool installs (`MISE_CONFIG_DIR` and
`MISE_DATA_DIR`), and the registered project tree are readable by child
commands. Explicit `MISE_GLOBAL_CONFIG_FILE` and `MISE_SYSTEM_CONFIG_FILE`
overrides are admitted as exact read-only files. These Mise roots may never
encompass the credential broker. Mutable Mise cache/state are rehomed to the
project-scoped MCP cache/state trees via `MISE_CACHE_DIR` and
`MISE_STATE_DIR`, so user and system Mise stores remain read-only.
Symlinked entries inside the system/user Mise config trees are followed only
to their exact resolved targets; this keeps centrally managed task links
usable without granting the target parent tree wholesale.

The credential profile must also preserve the selected permission mode's
temporary-directory contract. In `dangerous` mode, where
`global_tmp_write=allowed`, `/tmp` and `/var/tmp` remain readable/writable so
system/user Mise configuration such as `TMPDIR=/var/tmp` continues to work.
Those roots are still rejected if they would encompass the credential broker.

Provision the runtime/state/cache/log roots outside the source tree and make
them writable by the service account before preflight.

Example `/etc/systemd/system/coding-tools-mcp-unified.service`:

```ini
[Unit]
Description=Coding Tools MCP with OpenAI tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=codingtools
Group=codingtools
WorkingDirectory=/opt/coding-tools-mcp/repository
EnvironmentFile=/etc/coding-tools-mcp/service.env
Environment=HOME=/home/codingtools
Environment=PATH=/home/codingtools/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=MISE_CONFIG_DIR=/home/codingtools/.config/mise
Environment=MISE_DATA_DIR=/home/codingtools/.local/share/mise
ExecStartPre=/usr/bin/env mise exec -- uv run --locked python scripts/start_services.py --host-config /etc/coding-tools-mcp/config.toml --preflight
ExecStart=/usr/bin/env mise exec -- uv run --locked python scripts/start_services.py --host-config /etc/coding-tools-mcp/config.toml
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=20
SuccessExitStatus=130
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=tmpfs
BindReadOnlyPaths=/home/codingtools/.config/mise
BindReadOnlyPaths=/home/codingtools/.local/share/mise
BindReadOnlyPaths=/srv/projects/example
BindPaths=/var/lib/coding-tools-mcp

[Install]
WantedBy=multi-user.target
```

Activate it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coding-tools-mcp-unified.service
sudo systemctl status coding-tools-mcp-unified.service
journalctl -u coding-tools-mcp-unified.service -f
```

For upgrades on a host with multiple MCP instances, distinguish runtime-code
updates from unit-definition updates. Runtime-code-only changes require a
service restart but not `systemctl daemon-reload`. If a `.service` unit or
systemd drop-in changed, validate it and run `systemctl daemon-reload` before
restarting the affected unit. Restart one unit at a time. Require the unit to
return to `active`, verify its local MCP endpoint, and verify tunnel health
before restarting the next instance. Do not batch-restart every workspace at
once.

When hardening a service, do not place the command runtime or `TMPDIR` on a
`noexec` mount if project tools may execute generated shims. Provision the
HostConfig runtime/state/cache roots on mounts compatible with the workloads
served by the unit, and keep those mutable roots outside registered source
trees.

HostConfig cannot widen the outer systemd sandbox. A path hidden by
`ProtectHome`, omitted from bind mounts, or denied by filesystem permissions
remains unavailable even if HostConfig names it. Treat the unit as the outer
security ceiling and HostConfig as policy inside that ceiling.

One systemd unit may serve multiple registered projects when they share the
same trust/security domain. Split units only for a genuine OS/security
boundary, not merely because project count increased. Keep real unit files,
HostConfig, tunnel identities, project inventories, and secret material outside
the public fork.

The launcher also writes its own run directory, which remains the primary source
for child stdout, stderr, and tunnel diagnostics after a restart.

## Troubleshooting

Confirm the pinned tools:

```bash
mise exec -- python --version
mise exec -- uv --version
mise exec -- tunnel-client --version
```

Validate the tunnel independently:

```bash
mise exec -- tunnel-client doctor \
  --profile-file /etc/coding-tools-mcp/tunnel.yaml \
  --json
```

Check the newest run:

```bash
ls -1dt .runtime/services/* | head -1
cat .runtime/services/<run>/run.json
tail -n 80 .runtime/services/<run>/coding-tools-mcp.stderr.log
tail -n 80 .runtime/services/<run>/tunnel-client.stderr.log
```

Typical deterministic preflight failures are an occupied MCP port, an invisible
registered project root, a runtime/state/cache root that is not writable or is
inside a source root, a semantic backend version other than Serena 1.5.3, or a
missing profile file. `--doctor-only` is separate and performs the live
`tunnel-client doctor` workflow when tunnel validation is required.
