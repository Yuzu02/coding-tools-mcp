# Internal Extensions

## Why the fork uses extensions

This fork keeps `xyTom/coding-tools-mcp` as its mother core while adding
fork-owned capabilities behind a small internal extension boundary. The goal is
not behavior parity with upstream; the goal is to keep upstream synchronization
reviewable by localizing fork-specific integration to the ExtensionHost bridge.

Extensions are internal Python modules registered explicitly in code. V1 does
not discover package entry points, import arbitrary module paths, or execute
code named by TOML.

## Built-in extensions

`projects` is enabled by default and owns the fork's explicit multi-project
addressing and project/skill discovery:

- the immutable configured project registry;
- lazy project-local runtime/catalog/context state;
- command ownership routing across project runtimes;
- the structural project catalog inside each project runtime;
- the `projects.catalog` service capability;
- the `projects.registry` and `projects.runtimes` service capabilities;
- `list_projects`;
- `resolve_project`;
- `list_skills`;
- `read_skill`;
- `project_context`;
- `doctor`;
- project-addressing decorators for filesystem/Git/process/image/environment
  tools;
- opaque command/output routing; and
- project-neutral server discovery instructions.

The default composition therefore exposes 26 tools. If `projects` is disabled
before startup, its six contributed tools are absent and that process exposes
the 20 mother-core tools, subject to the existing `view_image` capability gate.

`semantic` is a second built-in extension, disabled by default. It requires
`projects` and contributes `list_symbols`, `find_symbol`, `find_definition`,
`find_references`, `find_implementations`, and `get_diagnostics` only when the exact supported Serena backend is available
at startup. The supported backend is `serena-agent==1.5.3`; with both
extensions enabled and Serena available the frozen catalog contains 32 tools.
If Serena is unavailable or the version is unsupported, the process still
starts and semantic metadata reports the backend unavailable, but the six
semantic tools are absent for that process.

Semantic backend state is isolated behind one lazy worker per active project.
Workers are bounded by `max_semantic_projects`, idle/LRU reaped, and use a
private JSON-lines protocol so Serena/SolidLSP types never enter the public MCP
contract. Worker state, HOME, temp files, cache, and Serena metadata live under
runtime state rather than inside project roots. Runtime worker failure does not
remove tools from an already frozen catalog and does not affect filesystem,
Git, or command capabilities.

## Configuration files

The committed public configuration is `coding-tools.toml`:

```toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

To opt into semantic navigation, install its exact dependency set separately
and enable it in configuration:

```bash
uv sync --extra semantic
```

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

The repository's `dev` and `semantic` extras are intentionally incompatible in
one uv environment: development validation uses MCP 2.x while Serena 1.5.3
requires MCP 1.27.0. Use `uv run --isolated --locked --extra semantic ...` for
semantic integration and `uv run --locked --extra dev ...` for normal
development gates. CI follows the same split.

`allow_dependency_install = false` keeps SolidLSP dependency bootstrap offline.
Set it to `true` only in host-local configuration when the operator explicitly
wants missing `uvx`/npm language-server dependencies to be downloaded.

Register actual project roots in the local overlay rather than the public file:

```toml
config_version = 1

[extensions.projects.registry.app]
root = "/srv/projects/app"

[extensions.projects.registry.api]
root = "/srv/projects/api"
```

The keys `app` and `api` are stable `project_id` values. They are logical
identifiers, not directory names. A launch with no explicit registry keeps
single-workspace compatibility by synthesizing one project called `default`
from `--workspace` / `CODING_TOOLS_MCP_WORKSPACE`; calls still pass
`project_id="default"` explicitly.

`coding-tools.local.toml` is the optional host-specific overlay. It is ignored
by Git and must not be used for public defaults.

Every present TOML file must declare `config_version = 1`. Unknown root keys,
unknown extension tables, invalid values, unsupported versions, and invalid
extension names fail startup rather than being ignored.

The runtime discovers `coding-tools.toml` only in its current working
directory unless another public file is selected with `--config` or
`CODING_TOOLS_MCP_CONFIG`. The default local overlay is
`coding-tools.local.toml` beside the selected public file, or in the current
working directory when no public file is selected. `--local-config` and
`CODING_TOOLS_MCP_LOCAL_CONFIG` select a different local file. The runtime does
not search parent directories or the user's home directory.

## Precedence

For supported values, precedence from highest to lowest is:

```text
explicit CLI
environment
coding-tools.local.toml
coding-tools.toml
built-in defaults
```

TOML tables merge only through declared schema fields. Scalars replace lower
layers. Lists replace the lower-precedence list as a whole; they are not
concatenated implicitly.

## Enabling and disabling extensions

`extensions.enabled` is the only activation field in TOML. Extension-specific
tables configure registered extensions but do not contain a second activation
switch.

The CLI can replace the enabled list for one process:

```bash
coding-tools-mcp --workspace /path/to/workspace --extensions projects
coding-tools-mcp --workspace /path/to/workspace --extensions ''
```

The environment equivalent is:

```bash
CODING_TOOLS_MCP_EXTENSIONS=projects
```

Both the CLI and environment forms are full replacement lists. An empty value
means no extensions. Extensions are resolved once during startup; there is no
runtime enable/disable operation and the MCP tool catalog is not mutated after
startup.

## Local/private configuration

`coding-tools.toml` contains public composition and defaults. It is safe to
commit when it contains no machine-specific data.

`coding-tools.local.toml` is intentionally untracked. Keep actual host paths,
credentials, tunnel identifiers, effective service-unit details, and other
machine-specific deployment state out of the public repository. Runtime state
belongs under ignored locations such as `.runtime/` where appropriate.

The public-fork hygiene tests enforce the local-config ignore rule and scan
tracked text for known private deployment markers.

## Project addressing

The endpoint has no mutable active project. `list_projects` discovers the
configured IDs; it does not select one. Every project-scoped call independently
carries `project_id`, for example:

```json
{"project_id":"app","path":"src/main.py"}
```

The project runtime resolves that path against the `app` root. Registered
nested projects form explicit boundaries: a parent-scoped relative path cannot
silently cross into a separately registered child project.

Command IDs and output refs are opaque handles and route back to their owning
project automatically. `client_request_id` is project-local, so recovering a
command by that identifier requires `project_id`; the same request ID may be
used independently in another project.

`safe` and `trusted` retain normal runtime confinement behavior. `dangerous`
keeps explicit project routing/path validation but disables MCP permission
gates and Landlock, so it must not be treated as isolation between mutually
untrusted projects. Use separate service/process/container boundaries for
different trust domains.

## Extension lifecycle

Startup is deterministic:

1. load and validate layered configuration;
2. resolve the enabled extension dependency graph;
3. instantiate extensions in topological order;
4. call `configure()` in dependency order;
5. seed core service capabilities;
6. call `register()` in dependency order;
7. compose and validate the final tool catalog;
8. freeze service and contribution registries;
9. call `start()` in dependency order;
10. begin serving MCP requests.

If registration or composition fails, normal transport does not start. If an
extension fails during `start()`, the failing extension is stopped first and
already-started extensions are stopped in reverse order. Normal shutdown stops
extensions in reverse dependency order and then closes mother-core resources.

## Tool contributions and decorators

`ToolContribution` adds a new tool contract: name, title/description, input
schema, handler, annotations, optional error status, optional MCP content
builder, and optional model-facing text renderer. A contribution cannot
silently replace a mother-core tool or another extension's tool; collisions
fail startup.

`ToolDecorator` adapts an existing composed tool without monkey-patching its
implementation. In V1, schema decoration is additive: a decorator may add new
input properties and required names, and may wrap the handler. It may not
replace an existing input property. Decorator order follows extension
dependency/order plus registration order and is deterministic.

The final catalog is composed once and frozen. `tools/list`, argument
validation, `tools/call`, content building, and text rendering all use that
same composed contract.

## Service capabilities

Extensions communicate through named `CapabilityKey` values in a
`ServiceRegistry`, rather than importing another extension's private
implementation. V1 permits one provider per capability key and rejects missing
required capabilities or duplicate providers during registration.

The mother core currently seeds `core.workspace` and the generic
`core.workspace_runtimes` factory. The `projects` extension publishes the
configured project registry, lazy runtime manager, and structural project
catalog capabilities. Registries become read-only before any extension
`start()` call.

## Upstream synchronization bridge

The intended relationship is:

```text
xyTom/main
    ↓
sync/upstream-main
    ↓
review and resolve conflicts
    ↓
fork main
```

The mother-core bridge is intentionally small. `Runtime` adapts core
`TOOL_REGISTRY`/schemas into composed tool contracts, seeds core capabilities,
constructs `ExtensionHost`, and then lists/dispatches tools through the frozen
catalog. Fork features should not add feature-specific branches throughout
`server.py`.

After an upstream sync, run the extension bridge compatibility tests first.
They verify, among other things, that project/skill tools and their renderers
remain extension-owned and that schema decorators affect both `tools/list` and
dispatch.

If upstream history is rewritten or reparented, do not use tree/patch
equivalence as a permanent substitute for ancestry. Diagnose the case first:
compare the merge base, range counts, tree IDs, and stable patch IDs. When the
current upstream tip has an exact tree match with an already-integrated fork
ancestor, reconnect the graph with an explicit merge whose first-parent tree is
preserved (for example `git merge -s ours --no-ff xyTom/main`). Use that
strategy only after exact equivalence is proven; otherwise perform a normal
content merge and resolve conflicts deliberately. In both cases, finish by
proving `git merge-base --is-ancestor xyTom/main HEAD` and rerunning the bridge
and relevant full gates.

## Adding a new internal extension

For V1, add an extension deliberately in code:

1. define an `ExtensionManifest` with a stable name, dependencies, and config
   schema;
2. implement `configure`, `register`, `start`, and `stop`;
3. publish/require named service capabilities instead of importing private
   implementation objects across extensions;
4. add tools, decorators, and bounded metadata through `ExtensionContext`;
5. register the extension in `builtin_extension_registry()`;
6. add explicit public/default configuration only when it should be enabled by
   default;
7. add lifecycle, composition, and bridge regression tests.

Do not make TOML name a Python module, entry point, or filesystem source.

## Non-goals

V1 intentionally does not provide external Python plugin packages, package
entry-point discovery, arbitrary filesystem module loading, runtime
installation/uninstallation, hot reload, dynamic enable/disable, a remote
extension marketplace, third-party dependency solving, or a general-purpose
dependency-injection framework.
