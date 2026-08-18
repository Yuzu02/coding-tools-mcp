# Extension Architecture + TOML Configuration Design

**Date:** 2026-08-17
**Status:** IMPLEMENTED + VERIFIED
**Scope:** Internal extension host and layered TOML configuration for the Yuzu02 coding-tools-mcp fork

The Phase 0 foundation described here is implemented. Its execution history is
preserved in the companion implementation plan; current deployed HostConfig
authority is specified by
[`2026-08-17-host-config-project-policy-single-unit.md`](../plans/2026-08-17-host-config-project-policy-single-unit.md)
and [`../../runtime-contract-v0.4.md`](../../runtime-contract-v0.4.md).

## 1. Objective

Create a stable internal extension architecture that separates fork-owned capabilities from the original `xyTom/coding-tools-mcp` mother core.

The fork remains free to evolve its own runtime and public MCP contract. The goal is not to keep the fork behavior-identical to upstream; the goal is to keep upstream synchronization reviewable by concentrating fork integration into a small, explicit bridge while implementing fork capabilities behind stable extension APIs.

Target model:

```text
xyTom mother core
        │
        │ minimal integration bridge
        ▼
┌───────────────────────────────┐
│ ExtensionHost                 │
│                               │
│ ExtensionRegistry             │
│ ContributionRegistry          │
│ ServiceRegistry               │
│ lifecycle orchestration       │
│ layered configuration         │
└───────────────┬───────────────┘
                │
     ┌──────────┼───────────┬────────────┐
     ▼          ▼           ▼            ▼
  projects   semantic   work_items     hooks/...
                │
              Serena
```

The first consumers are the planned `projects` and `semantic` extensions. Future fork-owned subsystems such as Work Items, hooks, gateway adapters, and other assistant capabilities should use the same boundary rather than introducing new direct coupling into the mother core.

## 2. Core invariants

1. Fork-owned feature code lives behind the internal extension boundary whenever practical.
2. The mother core knows only the minimum bridge necessary to host extensions.
3. Extensions are internal Python modules registered explicitly by the fork. V1 does not load arbitrary Python modules, filesystem paths, or package entry points from configuration.
4. TOML selects and configures registered extensions; TOML never imports or executes arbitrary code.
5. Extension dependencies are declarative, acyclic, validated before startup, and resolved deterministically.
6. Extensions communicate through published services/capabilities, not imports of another extension's private implementation.
7. Extensions add or adapt tools through explicit contribution APIs; monkey-patching, runtime `setattr`, and direct mutation of the mother-core `TOOL_REGISTRY` are forbidden extension mechanisms.
8. The composed tool catalog is validated and frozen before MCP transport begins accepting requests.
9. Startup configuration errors fail fast. Runtime degradation of an already-started optional backend is represented explicitly and does not silently mutate the tool catalog.
10. Host-specific configuration, paths, credentials, tunnel identifiers, and effective service units remain local and ignored by Git.
11. Upstream synchronization may change mother-core behavior. Conflicts are resolved deliberately in the sync/integration lane, with bridge compatibility tests identifying the affected seams.

## 3. Internal extension registry

V1 uses a static registry owned by the fork:

```python
EXTENSIONS = {
    "projects": ProjectsExtension,
    "semantic": SemanticExtension,
    "work_items": WorkItemsExtension,
}
```

The registry is code, not configuration. Unknown names in TOML are configuration errors.

Each extension exposes an immutable manifest conceptually equivalent to:

```python
@dataclass(frozen=True)
class ExtensionManifest:
    name: str
    requires: tuple[str, ...] = ()
    description: str = ""
```

Names use a stable grammar suitable for configuration keys and diagnostics. A practical initial grammar is:

```text
[A-Za-z][A-Za-z0-9_-]{0,63}
```

Duplicate names are a programmer error detected while building the static registry.

## 4. Dependency graph

Extensions declare hard dependencies through `requires`.

Example:

```python
class SemanticExtension:
    manifest = ExtensionManifest(
        name="semantic",
        requires=("projects",),
    )
```

Startup performs:

1. resolve enabled extension names;
2. verify every required extension exists;
3. verify every required extension is enabled;
4. detect dependency cycles;
5. topologically sort enabled extensions;
6. initialize in dependency order;
7. stop in reverse dependency order.

Missing dependencies and cycles are fatal configuration errors. V1 does not implicitly enable transitive dependencies because explicit configuration is easier to audit.

## 5. Extension APIs

Do not expose one unconstrained god-object to extensions. Separate the extension surface into three APIs.

### 5.1 Extension lifecycle API

Conceptual contract:

```python
class Extension(Protocol):
    manifest: ExtensionManifest

    def configure(self, config: Mapping[str, object]) -> None: ...
    def register(self, context: ExtensionContext) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

Exact signatures may use immutable typed configuration objects rather than raw mappings once implementation begins. The important boundary is lifecycle intent:

- `configure`: validate/normalize extension-owned configuration without performing runtime work;
- `register`: publish services and contributions;
- `start`: start workers/resources after the complete graph and contribution set are valid;
- `stop`: release resources idempotently.

`stop()` must be safe after partial startup failure for extensions whose `start()` began allocating resources.

### 5.2 Contribution API

Extensions may contribute:

```text
ToolContribution
ToolDecorator
ServerMetadataContribution
```

Future contribution types require an explicit API addition; extensions do not reach into unrelated core registries directly.

#### ToolContribution

Adds one new MCP tool definition including:

- name;
- title/description;
- annotations;
- input schema;
- handler;
- optional availability metadata owned by the extension.

Tool-name collisions are fatal. An extension cannot silently replace a mother-core tool or another extension's tool.

#### ToolDecorator

Adapts a declared existing tool through a controlled handler/schema pipeline.

This exists because some extensions, especially `projects`, must add cross-cutting addressing to existing mother-core operations without copying or monkey-patching their implementations.

Conceptual example:

```python
ctx.tools.decorate(
    targets=PROJECT_SCOPED_CORE_TOOLS,
    decorator=project_addressing,
)
```

A decorator may contribute validated schema changes and a wrapper around dispatch. Decoration order is deterministic and based on extension dependency/topological order plus explicit per-extension contribution order.

Invalid targets, duplicate incompatible schema additions, and ambiguous composition fail startup.

Decorators may not mutate the underlying tool definition in place.

### 5.3 Service/capability API

Extensions communicate through a typed service registry.

Conceptual model:

```python
PROJECT_REGISTRY = CapabilityKey[ProjectRegistry]("projects.registry")

ctx.services.provide(PROJECT_REGISTRY, registry)
project_registry = ctx.services.require(PROJECT_REGISTRY)
```

Rules:

- one provider per non-multi capability key;
- duplicate providers fail startup;
- required capabilities must exist before dependent extension registration completes;
- consumers depend on public protocols/interfaces, not provider implementation classes;
- service lookup after registry freeze is read-only.

## 6. ExtensionHost

`ExtensionHost` owns extension composition for one server process.

Responsibilities:

- hold the static extension registry;
- resolve enabled extensions from validated configuration;
- validate and sort dependency graph;
- construct extension instances;
- own contribution and service registries;
- orchestrate `configure/register/start/stop`;
- compose the final tool catalog from mother-core tools plus contributions/decorators;
- expose bounded extension metadata for diagnostics/server info;
- freeze registries before transport startup.

It does not own MCP transport, protocol negotiation, low-level filesystem/process primitives, or deployment supervision.

## 7. Mother-core bridge

The bridge is intentionally small and is the primary surface audited after `xyTom/main` synchronization.

Target seams:

```text
runtime/server construction
    -> construct ExtensionHost

tool catalog construction
    -> compose mother-core tools + extension contributions

input schema construction
    -> use composed definitions

tool dispatch
    -> dispatch through composed handler/decorator pipeline

server_info / discover metadata
    -> include extension metadata where appropriate

runtime close
    -> stop/close ExtensionHost
```

The implementation plan may refine exact call sites if upstream structure makes a smaller bridge possible. It must not spread extension-specific conditionals through unrelated mother-core handlers.

## 8. Package layout

Recommended fork-owned layout:

```text
coding_tools_mcp/
    extensions/
        __init__.py
        api.py
        config.py
        host.py
        registry.py
        services.py

        projects/
            __init__.py
            extension.py
            config.py
            ...

        semantic/
            __init__.py
            extension.py
            backend.py
            serena.py
            ...

        work_items/
            ...
```

The exact split between `api.py`, `registry.py`, and `services.py` should remain small and responsibility-driven; do not create abstraction-only files with no independent purpose.

## 9. Configuration files and precedence

Use two repository-level TOML files:

```text
coding-tools.toml        public, versioned defaults/composition
coding-tools.local.toml  host-specific overlay, always gitignored
```

Configuration precedence, lowest to highest:

```text
mother-core/fork defaults
        ↓
coding-tools.toml
        ↓
coding-tools.local.toml
        ↓
supported environment overrides
        ↓
explicit CLI overrides
```

Later layers override earlier layers through a schema-aware merge owned by the configuration layer:

- scalar values replace lower-precedence scalar values;
- known tables merge recursively only along fields declared by the schema;
- lists replace the lower-precedence list as a whole unless a field explicitly declares another merge policy;
- unknown keys never participate in merging because validation rejects them;
- no layer may inject arbitrary extension names or arbitrary root tables.

This avoids ad-hoc generic deep-merging while still allowing a local file to override one nested host-specific field without copying the complete public configuration.

This is the implemented developer-mode v1 layering model. It is not the
authority model for a deployed multi-project endpoint: HostConfig v2 is the
single authority there, does not implicitly load `coding-tools.local.toml`, and
only permits ProjectConfig v1 to reduce host authority.

## 10. Configuration schema

The public file starts with a version:

```toml
config_version = 1

[extensions]
enabled = ["projects", "semantic"]

[extensions.projects]
# public projects-extension defaults

[extensions.semantic]
backend = "serena"
lazy = true
```

`extensions.enabled` is the **only** activation source in TOML. Extension tables configure registered extensions; they do not contain a second `enabled` switch. A registered extension may have configuration present while disabled, which allows temporarily disabling a feature without deleting its configuration. Unknown extension tables still fail validation.

The local overlay contains host-specific values, for example:

```toml
config_version = 1

[extensions.projects.registry.coding-tools]
root = "/host/path/coding-tools-mcp"

[extensions.projects.registry.application]
root = "/host/path/application"

[extensions.semantic.serena]
# host-specific semantic backend settings
```

`config_version` is mandatory in both `coding-tools.toml` and any present `coding-tools.local.toml`. Both files must declare the same supported version. Unsupported or mismatched versions fail startup with a clear diagnostic; the runtime does not guess at compatibility.

## 11. Strict validation

Configuration is fail-fast and typo-resistant.

Requirements:

- unknown root keys are rejected;
- unknown extension names are rejected;
- extension-owned config is validated by that extension's config schema/parser;
- invalid types are rejected;
- duplicate/ambiguous declarations are rejected;
- deprecated fields, when eventually introduced, produce bounded explicit warnings during a defined migration window rather than being silently ignored;
- secrets are referenced, not embedded in committed public config;
- diagnostics redact secret values.

Example typo:

```toml
[extentions.semantic]
```

must fail rather than booting with semantic support unexpectedly disabled.

## 12. Local/private configuration policy

`coding-tools.local.toml` must be ignored by Git in the public fork.

Repository policy:

- public defaults/examples may be committed;
- actual host roots, user-specific paths, credentials, tunnel identifiers, local service definitions, and secrets are not committed;
- local runtime material remains under ignored `.runtime/` where appropriate;
- public tests enforce the policy so later changes cannot accidentally reintroduce private deployment data.

The existing public-fork hygiene regression should be extended to cover `coding-tools.local.toml` and any future effective-config filenames.

## 13. Launcher boundary

The services launcher is deployment/composition infrastructure, not an extension.

```text
scripts/launcher
      │
      ├── resolve deployment concerns
      ├── launch MCP process
      └── optional tunnel supervision
                 │
                 ▼
          coding-tools-mcp
                 │
                 ▼
          ExtensionHost
```

The canonical application configuration belongs to the MCP runtime, not duplicated in launcher-only dataclasses. The launcher may locate/select config files and pass explicit CLI/environment overrides, but extension configuration should have one parser/source of truth in the runtime package.

## 14. Startup lifecycle

Deterministic startup sequence:

```text
1. load built-in defaults
2. parse coding-tools.toml
3. parse coding-tools.local.toml when present
4. apply supported environment overrides
5. apply explicit CLI overrides
6. validate config_version and root schema
7. resolve enabled internal extensions
8. validate dependency graph
9. topologically sort extensions
10. configure extension instances
11. register services and contributions
12. compose/validate final tool registry and schemas
13. freeze contribution/service/tool registries
14. start extensions in dependency order
15. start accepting MCP transport requests
```

If steps 1-14 fail, transport does not begin accepting normal requests. `start()` may mutate the runtime state of already-published service objects, but it may not register new services, tools, decorators, or schemas after the registries are frozen.

## 15. Shutdown lifecycle

```text
transport stops accepting new work
        ↓
in-flight shutdown policy executes
        ↓
extensions stop in reverse dependency order
        ↓
extension services/resources close
        ↓
mother-core runtime closes
```

Shutdown is bounded and idempotent. One extension shutdown failure is collected/reported but does not prevent attempts to stop remaining extensions.

## 16. Runtime degradation and health

Startup validation and runtime backend health are different concerns.

Fatal startup examples:

- invalid TOML;
- unknown extension;
- missing dependency;
- dependency cycle;
- tool collision;
- invalid decorator target;
- required service unavailable during registration.

Runtime-degraded examples:

- Serena worker crashes after successful startup;
- an optional external backend becomes temporarily unavailable.

Runtime degradation is owned by the relevant extension and exposed through bounded diagnostics/typed tool failures. It does not silently remove tools from the frozen catalog.

## 17. Tool composition rules

The final tool catalog is built once per process startup.

Composition properties:

1. begin with the mother-core definitions;
2. add extension `ToolContribution`s;
3. apply validated `ToolDecorator`s deterministically;
4. validate schemas/annotations/handlers;
5. freeze the result;
6. serve the frozen catalog to protocol layers.

The extension layer may deliberately evolve the fork's public MCP contract. It is not required to preserve an upstream tool count or schema when a fork feature explicitly changes that contract.

## 18. Upstream synchronization model

This fork is **upstream-syncable**, not constrained to be behavior-identical or purely additive.

The intended Git workflow is:

```text
xyTom/main
    ↓
sync/upstream-main
    ↓
review/resolve conflicts
    ↓
fork main
```

The extension architecture reduces synchronization cost by keeping fork-owned behavior out of mother-core internals except for the explicit bridge.

After each upstream integration:

- run bridge compatibility tests first;
- inspect changed bridge surfaces;
- adapt the bridge when upstream internals legitimately changed;
- run mother-core/upstream-relevant tests;
- run extension-host tests;
- run enabled extension tests.

Conflict resolution may favor fork architecture where intentional; such resolutions should remain localized and test-backed.

## 19. Testing strategy

Create focused extension architecture tests, conceptually:

```text
tests/extensions/
    test_extension_registry.py
    test_extension_dependencies.py
    test_extension_lifecycle.py
    test_extension_services.py
    test_tool_contributions.py
    test_tool_decorators.py
    test_config_layers.py
    test_config_validation.py
    test_core_bridge.py
    test_upstream_compatibility.py
```

Required coverage:

### Registry/dependencies

- known extension resolution;
- unknown extension rejection;
- missing required extension rejection;
- dependency cycle rejection;
- deterministic topological order;
- reverse shutdown order.

### Services

- provider registration;
- duplicate provider rejection;
- required capability lookup;
- registry immutability after freeze;
- extension-private implementation does not leak through the capability API.

### Tool composition

- new tool contribution;
- duplicate tool rejection;
- valid core-tool decoration;
- unknown decorator target rejection;
- deterministic decorator order;
- conflicting schema decoration rejection;
- frozen catalog cannot be mutated after startup.

### Configuration

- public TOML only;
- public + local overlay;
- env precedence;
- CLI precedence;
- unsupported `config_version`;
- unknown key rejection;
- unknown extension rejection;
- extension-specific validation;
- private local config is Git-ignored.

### Bridge compatibility

- mother-core tools remain available before optional extensions are enabled;
- disabled extensions contribute nothing;
- enabled extension contributions flow through the public tool-list/dispatch path;
- protocol layers consume the composed catalog rather than a second fork-only registry;
- upstream synchronization changes that bypass the bridge contract cause focused tests to fail.

## 20. Implementation order

Phase 0 is implemented before `projects` or `semantic` feature work.

Recommended decomposition:

### Phase 0A — Configuration foundation

- TOML loader using Python `tomllib`;
- `config_version`;
- deterministic layer/override model;
- strict root validation;
- `coding-tools.local.toml` ignore/hygiene rules;
- typed normalized runtime configuration.

### Phase 0B — Extension kernel

- `ExtensionManifest` / extension protocol;
- static extension registry;
- dependency graph resolver;
- `ExtensionHost` lifecycle;
- `ServiceRegistry` / capability keys;
- contribution registries.

### Phase 0C — Mother-core bridge

- composed tool definitions;
- schema integration;
- dispatch/decorator pipeline;
- server metadata integration;
- runtime shutdown integration;
- bridge compatibility tests.

### Phase 0D — First extraction/migration

Move the existing fork-owned project/skill discovery capability behind the `projects` extension boundary in a single-workspace compatibility form, without yet implementing the full multi-project contract. Phase A then evolves that same extension to stable configured project IDs and multi-project routing. This proves the extension architecture on real existing functionality rather than only synthetic test extensions and avoids creating a temporary throwaway extension.

Acceptance criterion: a clean startup can enable/disable the first internal extension through TOML, the mother core requires only the defined bridge seams, and the upstream-sync workflow can detect bridge breakage without coupling to extension internals.

## 21. Relationship to Project Addressing + Semantic Navigation

`2026-08-16-project-addressing-semantic-navigation-design.md` depends on this design.

After Phase 0:

```text
projects extension
    ├── publishes ProjectRegistry capability
    ├── contributes list_projects / resolve_project
    └── decorates project-scoped mother-core tools

semantic extension
    ├── requires projects
    ├── consumes ProjectRegistry capability
    ├── publishes SemanticBackend services
    └── contributes semantic navigation tools
```

Neither extension should introduce direct feature-specific mutations into the mother core.

## 22. Non-goals for V1

- external Python plugin packages;
- package entry-point discovery;
- loading Python modules by filesystem path;
- runtime install/uninstall of extensions;
- dynamic enable/disable after server startup;
- remote extension marketplace;
- arbitrary user-authored extension code;
- dependency version solving between third-party plugins;
- hot reloading extension Python modules;
- a general-purpose dependency-injection framework.

These can be considered later if actual use requires them. The V1 interface should not preclude external providers, but must not pay their complexity cost now.

## 23. Success criteria

The extension architecture is successful when:

1. fork-owned capabilities can be implemented as internal extensions without scattered mother-core edits;
2. mother-core integration is limited to a small documented bridge;
3. `coding-tools.toml` defines public composition/defaults;
4. `coding-tools.local.toml` cleanly overrides host-specific values and is never tracked;
5. extension configuration is strict, versioned, and deterministic;
6. extension dependencies and lifecycle are deterministic and test-covered;
7. extensions share capabilities through stable service interfaces;
8. new tools and cross-cutting tool adaptations use contribution APIs rather than monkey-patching;
9. the final tool catalog is validated and immutable after startup;
10. runtime backend failures degrade explicitly without corrupting other extensions or the mother core;
11. an upstream `xyTom/main` sync primarily impacts bridge code/tests rather than extension internals;
12. `projects` and `semantic` can be implemented as consumers of this foundation without requiring another architectural rewrite.
