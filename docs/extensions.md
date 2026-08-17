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

The current built-in extension is `projects`. It is enabled by default and owns
the fork's project/skill discovery behavior:

- the structural project catalog;
- the `projects.catalog` service capability;
- `list_skills`;
- `read_skill`;
- the model-facing renderers for those two tools.

The default composition therefore remains the current 22-tool catalog. If
`projects` is disabled before startup, `list_skills` and `read_skill` are absent
and that process exposes 20 tools, subject to the existing `view_image`
capability gate.

## Configuration files

The committed public configuration is `coding-tools.toml`:

```toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

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

The mother core currently seeds `core.workspace`. The `projects` extension
publishes `projects.catalog`. Registries become read-only before any extension
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
