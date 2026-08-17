# Quickstart

For a cloned checkout that should supervise both this MCP server and OpenAI
`tunnel-client` on Windows, Linux, or macOS, use the
[multiplatform services launcher](services-launcher.md). It supports `mise`
tool provisioning, locked `uv` synchronization, existing or generated tunnel
profiles, diagnostics, and a `systemd` deployment.

Install the published command from PyPI:

```bash
curl -fsSL https://raw.githubusercontent.com/xyTom/coding-tools-mcp/main/scripts/install.sh | bash
```

Install and start local Streamable HTTP against a workspace:

```bash
curl -fsSL https://raw.githubusercontent.com/xyTom/coding-tools-mcp/main/scripts/install.sh \
  | bash -s -- --start --workspace /path/to/repo
```

Install and expose an authenticated bearer-token tunnel:

```bash
curl -fsSL https://raw.githubusercontent.com/xyTom/coding-tools-mcp/main/scripts/install.sh \
  | bash -s -- --tunnel cloudflared --auto-install-tunnel --workspace /path/to/repo
```

Or, from this checkout:

```bash
scripts/install.sh
```

Run the published package without a persistent install:

```bash
uvx coding-tools-mcp --workspace .
```

Use stdio for MCP clients:

```bash
uvx coding-tools-mcp --stdio --workspace /path/to/repo
```

When working from this checkout instead of a published package, start Streamable HTTP with:

```bash
make start
```

Or start the checkout plus an optional OpenAI tunnel through the canonical
multiplatform supervisor:

```bash
mise trust
mise install
uv run --locked python scripts/start_services.py \
  --workspace /path/to/repo \
  --tunnel-profile coding-tools-dev
```

See [services-launcher.md](services-launcher.md) for profile-file, generated
profile, `--no-sync`, diagnostics, and VPS service examples.

Endpoint:

```text
http://127.0.0.1:8765/mcp
```

Pass a different workspace, host, port, or extra server flags with Make variables:

```bash
make start MCP_WORKSPACE=/path/to/repo MCP_PORT=8000 MCP_ARGS="--permission-mode trusted"
```

If dependencies are missing, install the runtime in editable mode:

```bash
python -m pip install -e ".[dev]"
```

Start stdio:

```bash
coding-tools-mcp --stdio --workspace /path/to/repo
```

Run the acceptance gate:

```bash
make compliance
```

For local trace debugging:

```bash
CODING_TOOLS_MCP_TRACE=1 coding-tools-mcp --workspace /path/to/repo
```

Trace JSON lines are written to stderr.

For toolchains that require inherited shell variables, start the server with a broader shell environment policy:

```bash
CODING_TOOLS_MCP_SHELL_ENV_INHERIT=all coding-tools-mcp --workspace /path/to/repo
```

For local development with dependency downloads, shell expansion, and inline interpreter snippets, use trusted mode:

```bash
coding-tools-mcp --permission-mode trusted --workspace /path/to/repo
```

`--allow-network` remains a compatibility flag when you only want to open the network-looking command gate.

If the MCP client cannot show permission prompts and you intentionally want to disable `exec_command` permission gates inside an isolated container or VM:

```bash
coding-tools-mcp --permission-mode dangerous --workspace /path/to/repo
```

Use this only with trusted workspaces and trusted clients in an externally hardened environment. `--dangerously-skip-all-permissions` remains as a compatibility alias.
