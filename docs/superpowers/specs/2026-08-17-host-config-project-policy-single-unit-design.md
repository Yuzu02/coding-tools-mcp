# Host Configuration, Project Policy, and Single-Unit Deployment Design

**Date:** 2026-08-17
**Status:** Approved architecture; canonical pre-Hooks/Work/Gateway design
**Scope:** HostConfig v2, project-local policy, immutable startup configuration,
launcher/runtime convergence, deployment preflight, and migration to one
same-trust deployment unit

## 1. Purpose

The fork already has the internal `ExtensionHost`, a default `projects`
extension, stable multi-project addressing, lazy project runtimes, and the
optional `semantic` extension backed by isolated Serena workers. The missing
foundation is a single explicit configuration and deployment authority model.

This design closes that gap before Hooks, Work Items, or the MCP Gateway are
implemented. It defines:

- a strict machine-global `HostConfig` v2;
- a distinct project-owned `ProjectConfig` v1;
- hard authority and security ceilings between the two;
- one immutable startup configuration snapshot;
- one canonical secret-reference syntax;
- one shared configuration model for the runtime and services launcher;
- deterministic deployment preflight/doctor behavior;
- a staged migration from per-project processes to one same-trust process;
- the generic lifecycle seam future gateway discovery needs before catalog
  freeze.

The intended normal deployment is:

```text
one systemd unit
  └── one services launcher/supervisor
        ├── one coding-tools-mcp process
        │     ├── one immutable ConfigSnapshot
        │     ├── one ExtensionHost
        │     ├── one immutable registered-project set
        │     ├── N lazy ProjectRuntime instances
        │     └── N bounded lazy semantic workers
        └── one tunnel-client path
```

Multiple units remain correct only when they represent distinct deployment or
security domains.

## 2. Verified baseline and upstream state

At design creation the repository has already completed Phase 0, Phase A, and
Phase B of the earlier extension/project/semantic designs. The current runtime
contract is v0.4:

- `projects` is enabled by default;
- the default composed catalog contains 24 tools;
- project-scoped calls carry explicit `project_id`;
- there is no active/current project state;
- enabling the available `semantic` extension produces 28 tools;
- Serena is private behind `SemanticBackend` and uses bounded lazy workers.

The original upstream was refreshed before this design was written. The local
`sync/upstream-main` branch tracks the refreshed `xyTom/main`, and that upstream
is an ancestor of fork `main`. The upstream change adding the package CLI
`--version` surface is integrated before this foundation work starts.

The live connector observed during the same audit was stale relative to Git:
it still reported package version `0.3.0`, 22 mother-core tools, one workspace,
and no project/semantic contributions. Four older same-trust services were
still listening on loopback ports in the 8000-8003 range. Their systemd
sandboxes bind-mounted only their individual project roots. No machine-global
`/etc/coding-tools-mcp/config.toml` existed yet.

Those observations are migration inputs, not permanent repository facts.
Acceptance must always revalidate the live host rather than relying on this
snapshot.

## 3. Supersession and relationship to earlier designs

This document is authoritative for configuration identity, authority,
deployment ownership, startup configuration snapshotting, preflight, and the
single-unit migration.

It **preserves** the implemented project and semantic public contracts from:

- `2026-08-17-extension-architecture-config-design.md`;
- `2026-08-16-project-addressing-semantic-navigation-design.md`.

It **refines/supersedes** these older assumptions where they conflict:

1. Repository-level `coding-tools.local.toml` is no longer the normal
   machine-global production configuration. It remains a developer checkout
   overlay only.
2. The proposed global/workspace precedence in
   `2026-08-16-development-runtime-gateway-hooks-work-coordination-design.md`
   is replaced by the explicit HostConfig/ProjectConfig authority lattice in
   this document.
3. `${ENV:NAME}` is not a second secret-reference syntax. The canonical syntax
   is `env:NAME` or `file:/absolute/path`.
4. Project-local configuration is validated at startup rather than on the
   first request.
5. One process serves multiple projects that share a trust domain. Project
   count alone does not justify additional systemd units.
6. The package release version and runtime-contract version are separate
   identities and must be reported separately.
7. Future gateway discovery happens in a generic pre-freeze lifecycle phase;
   it does not add gateway-specific conditionals to the mother core.

Historical documents remain historical. They should later receive semantic
status labels instead of checkbox archaeology or retroactive rewriting.

## 4. Configuration identities and versions

There are four configuration file roles. They are not interchangeable.

### 4.1 `coding-tools.toml` — public developer/product configuration

Role:

- public and versioned;
- product/development defaults and examples;
- current developer compatibility configuration;
- safe to clone and publish.

It continues to use the existing extension-runtime schema:

```toml
config_version = 1
```

It must not contain machine-specific registered roots, credentials, tunnel
identifiers, host-only state paths, or deployed-instance inventories.

### 4.2 `coding-tools.local.toml` — private checkout overlay

Role:

- private developer-checkout overlay;
- ignored by Git;
- compatible with the existing v1 extension configuration model.

It is resolved only in **developer compatibility mode**. It is not
automatically discovered beside a machine-global HostConfig.

### 4.3 HostConfig — machine/deployment authority

Role:

- private host/deployment configuration;
- strict `config_version = 2`;
- authoritative for transport, security ceilings, project registration,
  enabled extensions, deployment supervision, and host-owned limits.

Recommended locations are:

```text
system service: /etc/coding-tools-mcp/config.toml
interactive:    $XDG_CONFIG_HOME/coding-tools-mcp/config.toml
fallback user:  ~/.config/coding-tools-mcp/config.toml
```

Systemd should pass the selected HostConfig path explicitly. A service must not
silently depend on the process working directory to discover its production
configuration.

### 4.4 ProjectConfig — project-owned reproducible policy/data

Role:

- optional configuration stored inside a registered project;
- separate schema and version:

```toml
project_config_version = 1
```

- project-owned data and requests constrained by host policy;
- never a generic higher-precedence overlay of HostConfig.

The default filename is `.coding-tools-mcp.toml`.

## 5. Resolution modes

Startup chooses exactly one configuration mode before constructing runtime
resources.

### 5.1 Developer compatibility mode

This preserves the current v1 behavior:

```text
built-in defaults
    < ./coding-tools.toml
    < ./coding-tools.local.toml
    < supported environment overrides
    < explicit CLI overrides
```

The existing `CODING_TOOLS_MCP_CONFIG`, `CODING_TOOLS_MCP_LOCAL_CONFIG`, and
extension override behavior remain compatibility surfaces.

The loader does not search parents or arbitrary home directories for these
developer files.

### 5.2 Host deployment mode

Host deployment mode is selected explicitly by a HostConfig v2 path. The
normal precedence is:

```text
built-in host defaults
    < selected HostConfig v2
    < supported emergency environment overrides
    < supported explicit CLI overrides
```

There is no implicit `coding-tools.local.toml` sibling overlay in this mode.
There is no implicit deep merge from repository configuration into HostConfig.

Environment and CLI overrides are compatibility/emergency mechanisms, not the
normal permanent deployment source. Every supported override maps to a typed
HostConfig field or an explicitly retained compatibility field.

### 5.3 Selection rules

The implementation exposes an explicit HostConfig option/environment selector
and a deterministic helper for standard host locations. System services use an
explicit path even when the path is conventional.

The existing v1 `--config`/developer behavior must remain unambiguous. A v2
HostConfig is never silently interpreted as v1 and v1 is never silently
upgraded to v2 semantics.

Conflicting developer-mode and host-mode selectors fail startup.

## 6. Strict schema and fail-fast behavior

Both HostConfig and ProjectConfig use the repository's schema-driven TOML
machinery. There is one TOML parsing/validation implementation, not an
independent launcher parser and runtime parser.

Requirements:

- unknown keys fail;
- unknown extension names fail;
- invalid types fail;
- unsupported config versions fail;
- duplicate/ambiguous identifiers fail;
- typed lists replace rather than accidentally concatenate unless a field
  explicitly defines another merge rule;
- accepted-but-ignored security fields are forbidden;
- malformed project config fails before transport accepts normal traffic;
- diagnostics are bounded and secret-safe.

Future subsystems may extend the strict v2 schema with backward-compatible
optional fields. A subsystem must not accept a field before there is a defined
consumer/policy for it merely to reserve syntax.

## 7. Authority lattice

Project configuration is resolved through explicit authority classes, not
last-value-wins merging.

Every project-configurable field belongs to one of four policy categories:

```text
host-only
project-select-from-host-set
project-narrow-host-limit
project-provide-data-under-host-policy
```

### 7.1 `host-only`

Only HostConfig may set the value. ProjectConfig cannot mention an equivalent
field.

Examples:

- listener address/port;
- auth mode and credentials;
- tunnel configuration;
- system/runtime/state/cache roots;
- project registry roots;
- extension installation/availability policy;
- global permission mode;
- network ceiling;
- host secret stores/references.

### 7.2 `project-select-from-host-set`

HostConfig defines an allowed set; ProjectConfig may choose a subset.

Examples:

```text
host gateway integrations = {mise, moon}
project selection          = {mise}
effective                  = {mise}
```

A project request for `random-server` fails validation instead of extending
the host set.

### 7.3 `project-narrow-host-limit`

HostConfig defines a ceiling; ProjectConfig may request an equal or stricter
value.

Example:

```text
host semantic timeout = 60 s
project request        = 20 s  -> valid
project request        = 300 s -> invalid
```

Permission strength follows the same direction: a project may reduce
capability but never turn a host `safe` policy into `dangerous`.

### 7.4 `project-provide-data-under-host-policy`

The project owns reproducible data, but a host policy controls whether and how
it can be consumed.

Examples include future verification command metadata and executable hook
definitions. Project ownership of command text is not permission to execute it.
Host policy must explicitly authorize the consumer and apply command/network/
timeout/environment ceilings.

## 8. HostConfig v2 schema ownership

HostConfig v2 owns five current top-level areas:

```text
runtime
transport
security
extensions
deployment
```

The exact Python model is immutable after normalization.

### 8.1 `[runtime]`

Host-owned runtime identity and storage settings include:

- `bootstrap_workspace`: existing workspace used to construct the mother-core
  runtime before project routing is composed;
- runtime/state/cache roots where explicitly configured;
- bounded runtime defaults that are process-wide rather than project-owned.

The bootstrap workspace does not become an active/current project. In an
explicit project registry, all project-scoped calls still require and route by
`project_id`.

Runtime/state/cache roots must not resolve inside a registered source project.

### 8.2 `[transport]`

Current fields include:

- `kind = "http" | "stdio"`;
- HTTP host;
- HTTP port;
- bounded transport options already supported by the server.

Unauthenticated HTTP is permitted only on loopback. A non-loopback no-auth
HostConfig is a fatal preflight/startup error.

### 8.3 `[security]`

Current host-owned security fields include:

- permission mode;
- shell environment inheritance policy;
- network/compatibility ceilings where supported;
- authentication mode and references for credentials;
- any explicitly supported annotation-override compatibility switches.

`dangerous` remains a valid intentional deployment mode. HostConfig does not
silently downgrade it when the service is loopback-only and externally
isolated by the tunnel/systemd boundary.

### 8.4 `[extensions]`

HostConfig owns:

- enabled internal extensions;
- strict extension-owned settings;
- the registered project set;
- semantic host limits and backend selection.

Example using generic public paths:

```toml
config_version = 2

[runtime]
bootstrap_workspace = "/srv/projects/coding-tools-mcp"

[transport]
kind = "http"
host = "127.0.0.1"
port = 8000

[security]
permission_mode = "dangerous"
shell_env_inherit = "all"

[extensions]
enabled = ["projects", "semantic"]

[extensions.projects.registry.coding-tools]
root = "/srv/projects/coding-tools-mcp"
project_config = ".coding-tools-mcp.toml"

[extensions.projects.registry.application]
root = "/srv/projects/application"
project_config = ".coding-tools-mcp.toml"

[extensions.semantic]
backend = "serena"
max_semantic_projects = 4
semantic_idle_timeout_seconds = 900
semantic_start_timeout_seconds = 60
semantic_request_timeout_seconds = 60
allow_dependency_install = false

[deployment.tunnel]
mode = "profile-file"
profile_file = "/etc/coding-tools-mcp/tunnel.yaml"
```

This example is documentation only. Real host roots and tunnel configuration
remain untracked.

### 8.5 `[deployment]`

Deployment settings consumed by the services launcher include only
supervision/launch concerns, for example:

- MCP repository/code checkout when it cannot be derived safely;
- dependency sync policy/extras;
- startup/shutdown/poll limits;
- log root;
- tunnel-client executable and mode;
- tunnel profile/reference;
- tunnel health/readiness settings.

The launcher consumes these fields from the same HostConfig object used to
derive runtime arguments. It does not define a second permanent configuration
universe.

## 9. Project registration and ProjectConfig path rules

Each HostConfig project record has a stable project ID and canonical root. It
may also name a project-config path.

Rules:

1. A relative project-config path resolves from the registered root.
2. Absolute project-config paths are rejected.
3. The normalized physical path must remain inside the registered root.
4. The path must not enter a separately registered nested-project root.
5. Symlink escape from the registered root is a startup error.
6. The default `.coding-tools-mcp.toml` is optional; absence means no project
   config.
7. A custom explicitly configured project-config path is required; if missing,
   startup fails.
8. A present file must declare `project_config_version = 1`.
9. Project configs are parsed and policy-resolved at startup, never on first
   request.
10. Changes take effect only after restart; hot reload is not part of this
    design.

Nested registered roots remain independent security/addressing boundaries.
Config discovery never causes a parent project to consume a child project's
file.

## 10. ProjectConfig v1 semantics

ProjectConfig is intentionally narrower than HostConfig. Initial implemented
categories may include reproducible metadata, capability reductions, and
bounded limits that already have a consumer. Additional Hooks/Work/Gateway
sections are added only when those subsystems ship.

ProjectConfig must never be able to:

- change transport host/port/kind;
- change auth mode or credentials;
- change tunnel configuration;
- change systemd or OS hardening;
- register or retarget project roots;
- name a path outside its registered root;
- change global runtime/state/cache roots;
- enable an extension disabled by the host;
- increase permission strength;
- increase network authority;
- increase a host timeout/resource ceiling;
- authorize a new gateway/provider identity;
- turn a read-only integration into write-capable mode;
- inject literal secrets;
- obtain arbitrary executable-provider loading.

Future executable hooks are project-owned data executed only when host policy
explicitly permits that hook class and applies normal runtime command policy.

## 11. Immutable ConfigSnapshot

Startup produces one immutable `ConfigSnapshot` (or a deliberately named
equivalent) before normal transport traffic is accepted.

It contains:

- selected resolution mode;
- selected configuration sources and source roles;
- normalized non-secret HostConfig/developer config;
- canonical registered-project records;
- each present project's validated effective ProjectConfig;
- config versions;
- bounded warnings;
- redacted secret-reference metadata;
- a deterministic configuration fingerprint.

The concrete runtime `ProjectRegistry` is derived exactly once from the same
canonical registered-project records and published by the `projects`
extension. This avoids a direct mother-core import of a private projects
package while preserving one project identity source of truth.

### 11.1 Fingerprint

The fingerprint is derived from deterministic canonical serialization of:

- normalized non-secret configuration;
- stable secret-reference identities;
- effective project configuration;
- relevant config/schema version identities.

It must not include:

- resolved secret values;
- process IDs;
- timestamps;
- unordered dictionary iteration artifacts;
- runtime health state.

The same effective configuration yields the same fingerprint across restarts.

The fingerprint is suitable for diagnostics, state partitioning, future hook
run identities, Work metadata, and gateway discovery/cache identities.

## 12. Secret references

There is one canonical syntax:

```text
env:NAME
file:/absolute/path
```

`env:NAME` requires a valid environment variable name. `file:` requires an
absolute path. Literal secret values in fields declared as secret references
are rejected.

Secrets are resolved by the narrow consumer that needs them:

- tunnel-only credentials remain in the tunnel environment/consumer;
- MCP auth credentials are resolved by the MCP auth consumer;
- future upstream A credentials are never added to upstream B's environment;
- project configuration cannot contain literal secret values.

Resolved values are never written to ConfigSnapshot diagnostics, SQLite,
runtime manifests, logs, command output metadata, or fingerprints.

Diagnostic metadata may report the secret-reference scheme and a stable
redacted identity. It does not need to expose a sensitive file path.

## 13. Startup lifecycle and generic prepare/discover phase

The startup lifecycle becomes:

```text
1. choose resolution mode
2. load built-in defaults
3. parse selected developer config OR HostConfig
4. apply supported env/CLI overrides
5. validate schema/version and authority
6. canonicalize registered projects
7. locate and validate all present ProjectConfig files
8. resolve project security ceilings
9. freeze ConfigSnapshot
10. construct/configure ExtensionHost
11. run generic extension prepare/discover phase when needed
12. register services/contributions/decorators
13. compose and freeze the MCP catalog
14. start runtime resources/extensions
15. accept transport traffic
```

Heavy resources remain lazy. Config validation does not eagerly create every
`ProjectRuntime`, Serena worker, future gateway stdio process, language server,
or heavy index/cache.

### 13.1 Why prepare/discover exists

The current extension lifecycle freezes contributions before normal traffic.
Future Gateway tools require upstream schema discovery before that freeze.

The generic lifecycle therefore supports the conceptual sequence:

```text
configure
prepare/discover
register
compose + freeze
start
```

The API remains generic. The mother core does not contain gateway-specific
discovery branches.

Prepare/discover requirements:

- bounded timeouts;
- no normal MCP traffic during discovery;
- a required dependency/upstream may fail startup;
- an optional dependency/upstream failure is deterministic and observable;
- tools/schemas are known before catalog freeze;
- cleanup after partial preparation is bounded and idempotent.

The first Gateway release may keep its public catalog frozen until restart.
Dynamic `tools/list_changed` is not required by this foundation.

## 14. Launcher/runtime convergence

Today the launcher and runtime each own overlapping configuration parsing. That
must not evolve into two independent authorities.

Target:

```text
canonical typed config parser/model
        │
        ├── launcher consumes deployment/supervision fields
        │
        └── MCP runtime consumes runtime/transport/security/extensions fields
```

The launcher may retain compatibility CLI flags and environment aliases, but
their normalized values map onto the canonical model. Permanent systemd
configuration should reduce to a selected HostConfig plus only genuinely
OS-owned settings.

A normal systemd launch should be equivalent to:

```text
python scripts/start_services.py --host-config /etc/coding-tools-mcp/config.toml
```

The exact option name may differ if implementation compatibility makes another
explicit name cleaner. The invariant is one selected HostConfig, not twenty
permanent feature flags.

### 14.1 Environment isolation

The existing launcher guarantee remains:

- tunnel-only secret environment does not reach the MCP child;
- probe environments use the least authority they need;
- no environment dump is persisted;
- manifests remain redacted.

HostConfig secret references reuse the launcher's `env:` / `file:` model rather
than creating another syntax.

## 15. Package version vs runtime-contract version

The package release version and fork runtime contract are distinct identities.
The current package remains `0.3.0` until the normal release process chooses a
new package version. Runtime contract v0.4 does not by itself force a package
release bump.

Observability must remove ambiguity by reporting both:

```text
package_version = 0.3.0
runtime_contract_version = 0.4
```

The upstream `--version` CLI reports the package version. `server_info` keeps
its legacy `version` compatibility field as the package version and adds
explicit `package_version` and `runtime_contract_version` fields. Configuration
metadata also reports the active config mode/version and fingerprint.

Tests must prevent these identities from being accidentally conflated again.

## 16. Systemd remains the OS security authority

HostConfig does not replace systemd hardening.

Systemd remains authoritative for:

- `User` / `Group`;
- `ProtectSystem`;
- `ProtectHome`;
- `BindPaths` / `BindReadOnlyPaths`;
- `NoNewPrivileges`;
- `PrivateTmp` / `PrivateDevices`;
- `RuntimeDirectory` / `StateDirectory` / `CacheDirectory`;
- capability bounding;
- address-family restrictions;
- process/mount namespace policy.

HostConfig cannot widen its own systemd sandbox. That separation is a desired
security property.

The one-unit service must explicitly bind every registered root that must be
visible under the hardened mount namespace. The current audit demonstrated why
this is necessary: a per-project unit with `ProtectHome=tmpfs` cannot see an
unbound sibling project merely because HostConfig names it.

## 17. Deployment doctor / preflight

Before normal startup and before cutover, a deterministic doctor/preflight can
validate configuration without starting the long-running service.

Minimum checks:

1. HostConfig parse/schema/version.
2. Project IDs and unique canonical roots.
3. Registered roots exist and are visible in the current execution sandbox.
4. ProjectConfig path, version, parse, and symlink/nested-boundary rules.
5. Project/host authority ceilings.
6. Enabled extension dependency graph.
7. Required semantic backend distribution/version when semantic is enabled.
8. Runtime/state/cache roots are outside project source roots and writable as
   required.
9. Unauthenticated HTTP binds are loopback-only.
10. Selected tunnel profile/reference is structurally valid and accessible to
    the launcher consumer.
11. Selected listener port is free before candidate startup.
12. The current sandbox exposes all roots required by the registered project
    set; when run as `ExecStartPre`, this proves the candidate systemd bind set.
13. No host-private deployment state is tracked by Git.

Doctor output is bounded, structured, non-secret, and returns nonzero on a
fatal finding. It does not start the normal MCP server or tunnel supervisor.

Checks that are inherently launcher/deployment-specific live in the launcher
preflight layer, while reusable config/project/security validation lives in the
canonical config layer.

## 18. Single-unit migration strategy

The cutover is staged and reversible. Do not stop all working connectors first
and then discover whether the candidate boots.

Sequence:

1. Implement HostConfig/ProjectConfig/ConfigSnapshot foundation.
2. Run focused and full repository gates.
3. Create private untracked production HostConfig.
4. Ensure the deployed runtime has the exact semantic capability required by
   the configured semantic adapter.
5. Create a candidate hardened single unit that can see every intended
   registered root.
6. Run doctor/preflight inside the candidate systemd sandbox.
7. Start the candidate on a temporary non-conflicting loopback port and tunnel
   path if required.
8. Perform live multi-project and semantic acceptance against the candidate.
9. Only after acceptance, retire redundant same-trust units/listeners/tunnels.
10. Move the canonical service to its final intended endpoint/tunnel.
11. Repeat live acceptance against the final unit.

If candidate acceptance fails, the previous services remain available and the
candidate is stopped/fixed without destructive cutover.

## 19. Live acceptance contract

The deployment is not complete until live acceptance verifies the actual
running service, not only unit tests.

Required acceptance:

- exactly one canonical same-trust systemd unit;
- one MCP process/endpoint;
- one intended tunnel-client path;
- HTTP listener loopback-only when no MCP auth is configured;
- explicitly intended `dangerous` policy remains visible where configured;
- projects-only composition exposes 24 tools;
- projects + available semantic composition exposes 28 tools;
- `list_projects` and `resolve_project` work;
- at least two real projects route independently;
- filesystem routing is independent across projects;
- Git routing is independent across projects;
- the same relative path in two projects does not cross-contaminate;
- semantic same-name/same-symbol operations in at least two projects remain
  isolated;
- the real Serena backend runs for at least two projects;
- no `.serena` directory appears inside registered source repositories;
- runtime and semantic state remain outside source repositories;
- the stale 22-tool live connector is gone;
- obsolete same-trust listeners/units are gone after final cutover;
- tunnel-client is healthy;
- the actual ChatGPT connector exposes the v0.4-capable composed catalog.

Acceptance should use every intended same-trust project when practical, while
the minimum isolation proof requires two.

## 20. Dangerous mode and trust domains

`permission_mode = "dangerous"`, local MCP no-auth, and disabled Landlock are
not automatically defects. They are valid for the current trusted deployment
when all of these remain true:

- listener is loopback/private;
- no direct public exposure bypasses the intended tunnel boundary;
- systemd sandbox and Unix-user boundary are controlled;
- every project in the process belongs to the same trust/security domain.

One process is one aggregate trust domain. Use separate units/containers/users
for materially different:

- Unix identities;
- secrets/credentials;
- network policies;
- filesystem/sandbox policies;
- mutually untrusted tenants;
- incompatible runtime versions.

## 21. Future Hooks and Work Items boundary

Hooks and Work Items start only after this foundation and deployment acceptance
are green.

They consume, rather than redefine:

- ConfigSnapshot identity;
- registered `project_id`;
- HostConfig security ceilings;
- ProjectConfig bounded project-owned data;
- canonical child-environment/command policy;
- host-owned state/runtime roots.

Executable project-owned hook definitions never become self-authorizing code.

## 22. Future Gateway configuration boundary

Gateway remains an internal extension:

```text
ExtensionHost
├── projects
├── semantic
├── hooks
├── work
└── gateway
```

HostConfig is the authority for every upstream definition:

- stable upstream ID;
- stable namespace;
- transport;
- argv or URL;
- lifecycle scope;
- secret references;
- timeout/resource limits;
- exposure policy;
- read/write policy;
- discovery policy;
- auth-context/cache identity inputs.

ProjectConfig may only select/narrow an already host-authorized integration.
It cannot define arbitrary executable upstream providers.

### 22.1 Lifecycle scopes

The gateway must support at least:

```text
global upstream  -> one client/pool for its HostConfig/auth scope
project upstream -> lazy client/process keyed by project_id
```

Secrets and runtime state are isolated by upstream identity and project scope
where applicable.

### 22.2 Discovery before catalog freeze

Gateway discovery uses the generic prepare/discover lifecycle described in
this document. Required upstream discovery failure may be fatal. Optional
failure is deterministic and observable.

Downstream data is untrusted:

- tool annotations are hints, not authorization facts;
- downstream identity is metadata, not authority;
- downstream server instructions are never promoted automatically into
  trusted Coding Tools server instructions.

## 23. Gateway public-tool policy

A generic normal model-facing tool such as:

```text
mcp_call(server, tool, arbitrary_json)
```

is rejected as the primary gateway interface because it erases schemas,
stable capability identity, annotation context, policy granularity, and
authorization boundaries.

Proxy identities are canonical internally as:

```text
(upstream_id, primitive_kind, remote_name)
```

External names are stable and namespaced, for example
`mise__run_task` when MCP naming constraints require encoding.

Remote write capabilities are deny-by-default. Exposure requires both remote
authorization and local HostConfig policy. ProjectConfig may only reduce the
authorized set.

## 24. Implementation phases

### C1 — Configuration model foundation

- shared strict schema/TOML machinery;
- HostConfig v2 immutable model;
- ProjectConfig v1 immutable model;
- authority rule metadata/resolution;
- explicit developer v1 compatibility mode;
- explicit HostConfig selection and standard path helpers;
- startup project-config validation;
- canonical secret references;
- immutable ConfigSnapshot and deterministic fingerprint;
- focused unit tests.

### C2 — Runtime/launcher convergence and doctor

- runtime consumes ConfigSnapshot rather than independently reinterpreting
  permanent deployment configuration;
- launcher consumes HostConfig deployment fields from the same model;
- compatibility flags/env aliases remain bounded;
- tunnel secret scrubbing remains enforced;
- generic extension prepare/discover lifecycle lands;
- package/runtime-contract version observability is explicit;
- deterministic doctor/preflight lands;
- integration tests cover runtime/launcher equivalence.

### C3 — Private production configuration

- create private HostConfig outside tracked files;
- register the revalidated same-trust project roots;
- ensure semantic production capability is installed/usable;
- construct candidate hardened single-unit deployment with all required roots
  visible;
- keep secrets/profile/unit inventory untracked.

### C4 — Live acceptance and cutover

- candidate preflight;
- candidate live multi-project/semantic acceptance;
- reversible retirement of redundant units/listeners/tunnels;
- final canonical endpoint acceptance;
- actual ChatGPT connector validation.

### C5 — Documentation/status cleanup

- mark earlier specs/plans semantically as `IMPLEMENTED + VERIFIED`,
  `SUPERSEDED`, `HISTORICAL EXECUTION PLAN`, or `PROPOSED / NOT IMPLEMENTED`;
- refresh stale test counts/snapshots without turning old plans into enormous
  completed checklists;
- remove stale v0.3/single-workspace wording from current-facing docs while
  preserving historical contracts;
- refresh upstream status and bridge evidence.

Only then begin:

```text
Hooks -> Work Items -> Gateway
```

## 25. Testing strategy

### 25.1 HostConfig tests

- `config_version = 2` required;
- unknown root/section/field rejection;
- invalid types and enum values;
- explicit host/developer mode conflict rejection;
- no implicit local overlay in host mode;
- deterministic standard host path helpers;
- env/CLI compatibility override precedence;
- loopback/no-auth invariant;
- runtime/state/cache path safety.

### 25.2 ProjectConfig tests

- version required;
- unknown keys rejected;
- default missing file allowed;
- custom required file missing fails;
- absolute path rejected;
- traversal/symlink escape rejected;
- nested registered-root boundary rejected;
- project cannot escalate host-only fields;
- subset selection enforcement;
- limit narrowing enforcement;
- deterministic effective project config.

### 25.3 Snapshot/secret tests

- immutability;
- deterministic fingerprint under stable config;
- fingerprint changes for material non-secret config changes;
- fingerprint changes for secret-reference identity changes;
- resolved secret value changes do not enter persisted snapshot/fingerprint
  computation;
- `env:` and absolute `file:` accepted;
- literals/malformed references rejected;
- diagnostics redact secret-reference-sensitive metadata.

### 25.4 Lifecycle tests

- prepare/discover order precedes registration/catalog freeze;
- registration cannot occur after freeze;
- preparation failure cleanup is bounded;
- existing extensions without preparation remain behavior-compatible;
- no mother-core import of private extension packages is introduced.

### 25.5 Launcher/runtime tests

- one HostConfig yields consistent MCP argv/runtime settings;
- CLI compatibility overrides canonical fields deterministically;
- MCP child does not receive tunnel-only secrets;
- doctor does not start long-lived children;
- port-in-use detection;
- tunnel profile/ref validation;
- semantic availability validation;
- same project roots are seen by runtime and launcher preflight.

### 25.6 Regression gates

Fresh completion gates include the repository's actual current equivalents of:

- extension/project/semantic suites;
- isolated Serena integration against the exact supported backend;
- upstream bridge/privacy tests;
- protocol/schema-drift/dispatch-input/compliance gates;
- Ruff and mypy;
- launcher tests;
- npm launcher/package dry-run checks;
- `mise run verify`.

Historical test counts are evidence only. No implementation hard-codes them as
expected totals.

## 26. Public fork hygiene

The public repository must not track:

- real machine HostConfig;
- real tunnel profile;
- literal credentials/secrets;
- real systemd service inventory;
- private registered project roots;
- deployed-instance inventory;
- `coding-tools.local.toml`.

The repository may track:

- schema/model code;
- generic example HostConfig;
- generic systemd templates that contain no real instance data;
- public documentation;
- tests and fixtures using synthetic roots/secrets.

Existing hygiene tests are extended when new private filenames or examples are
introduced.

## 27. Upstream synchronization constraints

The fork remains upstream-syncable:

```text
xyTom/main
    -> sync/upstream-main
    -> fork main integration
```

`origin/main` is the fork publication target, not the original upstream
source. The bridge remains small and reviewable. Mother-core code does not
import extension-private project/semantic/gateway packages directly.

Before final publication:

- refresh `xyTom/main` from the network;
- move `sync/upstream-main` to the same upstream ref;
- confirm upstream is an ancestor of fork `main`;
- rerun focused bridge compatibility tests;
- preserve a normal fast-forward push to `origin/main` when possible;
- never force-push merely because local history was unnecessarily rewritten.

## 28. Non-goals

This foundation does not implement:

- Hooks;
- Work Items;
- Gateway upstream clients;
- arbitrary provider loading;
- hot config reload;
- dynamic gateway catalog refresh after startup;
- `tools/list_changed` support;
- mutable project activation;
- arbitrary project-root registration through MCP;
- cross-trust tenant isolation inside one process;
- a general secret manager;
- a generic `mcp_call` public escape hatch;
- automatic trust of downstream instructions/annotations;
- a replacement for systemd hardening.

## 29. Success criteria

This pre-Hooks/Work/Gateway stage is complete only when all of the following
are true:

1. HostConfig v2 and ProjectConfig v1 are strict, versioned, and tested.
2. Developer v1 configuration remains compatible without becoming production
   HostConfig.
3. Project policy is resolved through explicit authority ceilings rather than
   generic overlay precedence.
4. All project configs are located, validated, and frozen at startup.
5. ConfigSnapshot is immutable, deterministic, and secret-safe.
6. Launcher and runtime share the canonical HostConfig model.
7. Package version and runtime-contract version are independently observable.
8. Deployment doctor catches config/root/security/semantic/tunnel/port/private-
   state failures before normal startup.
9. One hardened same-trust systemd unit can see all registered roots without
   HostConfig widening its own sandbox.
10. One MCP process serves the intended registered projects through explicit
    `project_id` routing.
11. Projects-only and projects+semantic catalogs match the v0.4 contract.
12. Real Serena operations are isolated across at least two projects.
13. Runtime/semantic state remains outside source repositories.
14. One intended tunnel path is healthy and no obsolete same-trust listeners
    remain after cutover.
15. The actual ChatGPT connector exposes the v0.4-capable catalog rather than
    the stale 22-tool runtime.
16. Public fork hygiene remains clean and no real deployment state is tracked.
17. Refreshed `xyTom/main` remains integrated through `sync/upstream-main` and
    bridge tests are green.
18. The working tree is clean and full fresh verification gates pass before
    publication.

Only after these criteria are met does implementation proceed to Hooks, then
Work Items, then Gateway.
