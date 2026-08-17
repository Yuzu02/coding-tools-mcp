# Multiplatform MCP and OpenAI Tunnel Launcher

`scripts/start_services.py` is the repository-owned supervisor for running this
checkout of `coding-tools-mcp` together with OpenAI `tunnel-client`. It is the
canonical clone-based launcher on Windows, Linux, and macOS.

It uses `mise` only to provision pinned tools and `uv` to synchronize and run
the Python checkout. It does not install the package globally, embed secrets in
the repository, or construct child commands through a shell.

The launcher is deployment/composition infrastructure, not the extension
configuration parser. It starts the MCP process with the MCP checkout as its
working directory, so the runtime can discover that checkout's
`coding-tools.toml`. The runtime package remains the sole parser/source of truth
for `coding-tools.toml`, `coding-tools.local.toml`, and extension-specific
configuration. The launcher may pass normal CLI/environment startup inputs but
does not interpret extension TOML itself.

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
- `uv` 0.12.3;
- Node 24.19.0;
- Rust 1.97.1;
- `github:openai/tunnel-client` 0.0.11.

The project environment separately sets `UV_PYTHON=3.13.12`, so `uv` resolves
the project interpreter independently of the Python version used to bootstrap
`mise` tasks.

Node and Rust are included because the repository's npm and real-workload
verification paths use them. The launcher itself is Python and runs the MCP
checkout exclusively through `uv run --project ... --locked`.

## Basic operation

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

## Environment files

Configuration precedence is:

```text
CLI argument > existing process environment > selected .env > default
```

The default file is `<workspace>/.env`. Existing process variables are never
overwritten. Select another file with `--env-file PATH`, or disable loading with
`--no-env-file`.

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

Example `/etc/systemd/system/coding-tools-mcp.service`:

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
ExecStart=/usr/bin/env mise exec -- uv run --locked python scripts/start_services.py --workspace /srv/coding-tools-mcp/workspace --tunnel-profile-file /etc/coding-tools-mcp/tunnel.yaml --no-sync
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Activate it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coding-tools-mcp.service
sudo systemctl status coding-tools-mcp.service
journalctl -u coding-tools-mcp.service -f
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
`noexec` mount if its workspace tools may execute generated shims. A deployed
host uses each service's isolated `/var/cache/coding-tools-mcp-*` directory for
`CODING_TOOLS_MCP_RUNTIME_ROOT` and `TMPDIR`; `/run/coding-tools-mcp-*` is
`noexec` in those service namespaces and breaks tools such as `npx` and Next.js
consumer builds.

Keep host-specific instance inventories and rollout order
are documented in `docs/ops/deployed-instances.md`.

For that host, the four version-controlled unit files live under
`deploy/systemd/`. `/etc/systemd/system/coding-tools-mcp*.service` should point
to those canonical files rather than ignored `.runtime/` state or independent
root-owned copies.

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

Typical preflight failures are an occupied MCP port, a missing workspace,
missing `pyproject.toml` or `uv.lock`, an unsupported older MCP checkout, a
missing profile file, or a failed `tunnel-client doctor` check.
