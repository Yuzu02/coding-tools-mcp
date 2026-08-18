# Host Configuration, Project Policy, and Single-Unit Deployment Implementation Plan

**Status:** HISTORICAL EXECUTION PLAN — Tasks 1–14 are IMPLEMENTED + VERIFIED; Tasks 15–16 record final documentation and verification work.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the strict HostConfig v2 / ProjectConfig v1 authority foundation, converge runtime and launcher configuration, add deterministic preflight, and migrate the current same-trust deployment to one live multi-project v0.4-capable service without losing rollback safety.

**Architecture:** Extract reusable strict-schema primitives from the existing extension config loader, then build one canonical immutable host/project configuration model above them. The MCP runtime and services launcher resolve the same model and fingerprint; `projects` and `semantic` consume a generic snapshot through the existing service registry rather than creating new mother-core/private-extension imports. Deployment remains OS-owned: HostConfig selects same-trust projects while systemd explicitly supplies mounts, users, directories, and hardening.

**Tech Stack:** Python 3.11+, stdlib `tomllib`, frozen dataclasses, `MappingProxyType`, SHA-256 canonical fingerprints, `unittest`, existing ExtensionHost/service registry, uv, mise, systemd, OpenAI `tunnel-client`, Serena `1.5.3`.

## Global Constraints

- Work only in the configured real `@Coding-Tools-Repo` workspace; use workspace-relative tool paths.
- Use `apply_patch` for repository file modifications.
- Do not use Git worktrees.
- Re-check Git before each commit/deployment mutation; do not overwrite unrelated work.
- Keep `xyTom/main` integrated through `sync/upstream-main`; `origin/main` is publication only.
- Mother-core must not import private `projects`, `semantic`, or future gateway packages.
- `coding-tools.toml` / `coding-tools.local.toml` stay developer config v1; HostConfig is `config_version = 2`; ProjectConfig is `project_config_version = 1`.
- Host mode never implicitly loads `coding-tools.local.toml`.
- Secret references are only `env:NAME` or `file:/absolute/path`; resolved values are never persisted.
- Project config can only reduce host authority; never use generic last-value-wins security merging.
- No hot reload. Config changes require restart.
- Keep `serena-agent==1.5.3` and the deliberate dev/semantic dependency conflict.
- Keep package release version separate from runtime contract `0.4`.
- Do not track real HostConfig, tunnel profiles, systemd units, project roots, tunnel IDs, or deployed-instance inventory.
- Preserve intentional `dangerous` mode when the verified loopback/systemd/tunnel trust boundary remains valid.
- Multiple units remain valid only for genuine trust/security/version boundaries, not project count.
- Do not implement Hooks, Work Items, or Gateway in this plan.
- Every implementation task uses RED → minimal GREEN → focused regression gate → commit.
- Historical test totals are evidence only; final claims use fresh results.

## File / Responsibility Map

- Create `coding_tools_mcp/config_schema.py`: shared strict schema, TOML parsing/merge, immutable freezing.
- Create `coding_tools_mcp/host_config.py`: HostConfig v2, ProjectConfig v1, authority model, secret refs, ConfigSnapshot/fingerprint.
- Create `coding_tools_mcp/runtime_contract.py`: `RUNTIME_CONTRACT_VERSION = "0.4"`.
- Create `scripts/launcher/preflight.py`: deployment-only deterministic preflight; no long-lived child startup.
- Create `tests/test_host_config.py`, `tests/test_project_config.py`, `tests/test_config_snapshot.py`, `tests/test_launcher_preflight.py`.
- Refactor `coding_tools_mcp/extensions/config.py`: retain v1 loader while re-exporting shared schema primitives.
- Extend `coding_tools_mcp/extensions/services.py`: generic `CORE_CONFIG_SNAPSHOT` capability.
- Extend `coding_tools_mcp/extensions/api.py` and `host.py`: generic prepare/discover phase.
- Extend `coding_tools_mcp/extensions/projects/{extension,registry}.py`: consume normalized project records.
- Extend `coding_tools_mcp/extensions/semantic/extension.py`: enforce effective project capability reductions.
- Extend `coding_tools_mcp/server.py`: HostConfig selection, pre-runtime snapshot, auth/security/transport consumption, server-info observability.
- Refactor `scripts/launcher/{config,app}.py` and `scripts/start_services.py`: canonical HostConfig mode while retaining compatibility flags.
- Update focused config/project/semantic/lifecycle/launcher/compliance/hygiene tests.
- Update `docs/runtime-contract-v0.4.md`, `docs/services-launcher.md`, and current status docs with generic examples only.
- Create real production config/unit/profile material only as private/untracked C3/C4 state.

---

### Task 1: Extract Shared Strict-Schema Primitives Without Changing Developer v1

**Files:**
- Create: `coding_tools_mcp/config_schema.py`
- Modify: `coding_tools_mcp/extensions/config.py`
- Test: `tests/extensions/test_config_validation.py`
- Regression: `tests/extensions/test_config_layers.py`, `tests/extensions/test_config_startup.py`

**Interfaces:**
- Produces `ConfigError`, `ConfigNode`, `scalar`, `list_of`, `table`, `map_of`, `read_toml`, `validate_node`, `merge_node`, `freeze_value`, and `freeze_mapping` in `coding_tools_mcp.config_schema`.
- `coding_tools_mcp.extensions.config` re-exports existing public names and keeps `RuntimeConfig`, `resolve_config_paths`, `parse_extension_list`, and `load_runtime_config` behavior.

- [ ] **Step 1: Write the failing import-identity regression**

```python
def test_extension_config_reexports_shared_schema_types(self) -> None:
    from coding_tools_mcp import config_schema
    from coding_tools_mcp.extensions import config as extension_config
    self.assertIs(extension_config.ConfigError, config_schema.ConfigError)
    self.assertIs(extension_config.ConfigNode, config_schema.ConfigNode)
```

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_config_validation.ConfigValidationTests.test_extension_config_reexports_shared_schema_types
```

Expected: import failure because `config_schema.py` does not exist.

- [ ] **Step 3: Move only generic machinery**

```python
@dataclass(frozen=True)
class ConfigNode:
    kind: Literal["scalar", "list", "table", "map"]
    value_types: tuple[type[object], ...] = ()
    children: Mapping[str, "ConfigNode"] = field(default_factory=dict)
    item: "ConfigNode | None" = None
```

Keep v1 path discovery and extension activation in `extensions/config.py`; no behavior change belongs in this extraction.

- [ ] **Step 4: Run GREEN/regression**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_config_layers \
  tests.extensions.test_config_validation \
  tests.extensions.test_config_startup
uv run --locked --no-sync python -m ruff check \
  coding_tools_mcp/config_schema.py coding_tools_mcp/extensions/config.py
```

- [ ] **Step 5: Commit**

```bash
git add coding_tools_mcp/config_schema.py coding_tools_mcp/extensions/config.py \
  tests/extensions/test_config_validation.py
git commit -m "refactor: share strict config schema primitives"
```

---

### Task 2: Implement Strict HostConfig v2 and Canonical Secret References

**Files:**
- Create: `coding_tools_mcp/host_config.py`
- Create: `tests/test_host_config.py`

**Interfaces:**
- Produces `HOST_CONFIG_VERSION = 2`, `PROJECT_CONFIG_VERSION = 1`.
- Produces immutable `SecretRef`, `HostRuntimeConfig`, `HostTransportConfig`, `HostSecurityConfig`, `HostTunnelConfig`, `HostDeploymentConfig`, and `HostConfig`.
- Produces `parse_secret_ref()`, `resolve_secret_ref()`, `standard_host_config_path()`, and `load_host_config()`.
- HostConfig stores extension composition as the existing immutable `RuntimeConfig` so ExtensionHost has one extension config representation.

- [ ] **Step 1: Write strict HostConfig RED tests**

Create named tests that assert all of these exact outcomes: version `1` is
rejected with a `config_version` diagnostic; an unknown root key is rejected;
an unknown nested security key is rejected; unauthenticated HTTP on `0.0.0.0`
is rejected; an adjacent `coding-tools.local.toml` is ignored in HostConfig
mode; `XDG_CONFIG_HOME` resolves to
`<xdg>/coding-tools-mcp/config.toml`; `env:API_TOKEN` and an absolute `file:`
reference parse successfully; literal text and relative `file:` references are
rejected.

Use a concrete first test such as:

```python
with self.assertRaisesRegex(ConfigError, "config_version"):
    load_host_config(
        root / "config.toml",
        extension_schemas={},
        default_enabled=(),
    )
```

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.test_host_config
```

- [ ] **Step 3: Implement typed HostConfig schema**

The root schema is dynamically composed from registered extension schemas:

```python
def host_config_schema(extension_schemas: Mapping[str, ConfigNode]) -> ConfigNode:
    return table({
        "config_version": scalar(int),
        "runtime": HOST_RUNTIME_SCHEMA,
        "transport": HOST_TRANSPORT_SCHEMA,
        "security": HOST_SECURITY_SCHEMA,
        "extensions": table({"enabled": list_of(scalar(str)), **extension_schemas}),
        "deployment": HOST_DEPLOYMENT_SCHEMA,
    })
```

Define those referenced schemas in the same module. `HOST_RUNTIME_SCHEMA`
allows only `bootstrap_workspace`, `runtime_root`, `state_root`, `cache_root`;
`HOST_TRANSPORT_SCHEMA` only `kind`, `host`, `port`; `HOST_SECURITY_SCHEMA`
only permission/shell-env/auth fields declared by the canonical spec;
`HOST_DEPLOYMENT_SCHEMA` only MCP repository, sync extras, startup/shutdown/poll
limits, logs root, and the strict tunnel table. Unknown keys fail.

- [ ] **Step 4: Implement secret refs without resolution during parsing**

```python
def parse_secret_ref(raw: str) -> SecretRef:
    if raw.startswith("env:") and ENV_SECRET_RE.fullmatch(raw[4:]):
        return SecretRef(scheme="env", target=raw[4:])
    if raw.startswith("file:") and Path(raw[5:]).is_absolute():
        return SecretRef(scheme="file", target=raw[5:])
    raise ConfigError("secret reference must use env:NAME or file:/absolute/path")
```

`resolve_secret_ref()` is consumer-only and never embeds the value in errors.

- [ ] **Step 5: Run GREEN**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_host_config tests.extensions.test_config_validation
```

- [ ] **Step 6: Commit**

```bash
git add coding_tools_mcp/host_config.py tests/test_host_config.py
git commit -m "feat: add strict host configuration model"
```

---

### Task 3: Implement ProjectConfig v1, Authority Rules, and Immutable ConfigSnapshot

**Files:**
- Modify: `coding_tools_mcp/host_config.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Create: `tests/test_project_config.py`
- Create: `tests/test_config_snapshot.py`

**Interfaces:**
- Produces `AuthorityKind` values `host-only`, `project-select-from-host-set`, `project-narrow-host-limit`, `project-provide-data-under-host-policy`.
- Produces immutable `RegisteredProjectConfig`, `ProjectCapabilities`, `ProjectConfig`, `EffectiveProjectConfig`, and `ConfigSnapshot`.
- Initial real ProjectConfig surface is deliberately narrow:

```toml
project_config_version = 1

[capabilities]
disabled = ["semantic"]
```

This field has a real consumer in Task 5. No unused hooks/providers/secrets syntax is accepted yet.

- [ ] **Step 1: Extend strict project registry schema**

Each registry record accepts only:

```python
table({
    "root": scalar(str),
    "allow_unavailable": scalar(bool),
    "project_config": scalar(str),
})
```

Missing `project_config` selects optional `.coding-tools-mcp.toml`; an explicitly named custom path is required to exist.

- [ ] **Step 2: Write ProjectConfig RED tests**

Create separate named tests proving: missing default file returns an empty
effective project config; missing explicitly named custom file raises
`ConfigError`; absolute path is rejected; outward symlink is rejected; a
parent config path entering a registered child root is rejected; unknown keys
are rejected; `disabled = ["semantic"]` is accepted when semantic is
host-authorized; disabling an unknown/unhosted capability is rejected.

The successful reduction assertion is concrete:

```python
effective = snapshot.projects["app"]
self.assertNotIn("semantic", effective.enabled_capabilities)
```

- [ ] **Step 3: Run ProjectConfig RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.test_project_config
```

- [ ] **Step 4: Implement safe project-config path resolution**

Implement `resolve_project_config_path(project, *, registered_roots) -> Path | None`
as the only path resolver used during snapshot construction.

Reject absolute paths, traversal, physical symlink escape, and entry into a separately registered nested root. Parse every present file during snapshot construction.

- [ ] **Step 5: Write snapshot RED tests**

Create separate tests that attempt mutation and expect `TypeError` or frozen
dataclass failure; build the same effective config twice and compare equal
fingerprints; change one non-secret setting and compare unequal fingerprints;
change only secret-reference identity and compare unequal fingerprints; change
the environment value behind the same `env:` identity and compare equal
fingerprints; construct logically equal mappings in opposite insertion order
and compare equal fingerprints.

- [ ] **Step 6: Implement canonical SHA-256 fingerprint**

```python
encoded = json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode("utf-8")
fingerprint = hashlib.sha256(encoded).hexdigest()
```

Include normalized non-secret config, effective project config, config/schema versions, and stable secret-reference identities only.

- [ ] **Step 7: Run GREEN and commit**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_project_config tests.test_config_snapshot
git add coding_tools_mcp/host_config.py coding_tools_mcp/extensions/projects/extension.py \
  tests/test_project_config.py tests/test_config_snapshot.py
git commit -m "feat: freeze project policy configuration"
```

---

### Task 4: Derive ProjectRegistry From the Canonical Snapshot

**Files:**
- Modify: `coding_tools_mcp/extensions/services.py`
- Modify: `coding_tools_mcp/extensions/projects/registry.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/server.py`
- Modify: `tests/extensions/test_project_registry.py`
- Modify: `tests/extensions/test_projects_extension.py`
- Regression: `tests/extensions/test_upstream_compatibility.py`

**Interfaces:**
- Produces `CORE_CONFIG_SNAPSHOT = CapabilityKey[ConfigSnapshot]("core.config_snapshot")`.
- Produces `build_project_registry_from_records(records, *, validate_root) -> ProjectRegistry` inside the private projects package.
- `Runtime` receives a prebuilt `ConfigSnapshot` and seeds it through the generic service registry.
- Server/mother-core still never imports `extensions.projects.registry`.

- [ ] **Step 1: Write RED tests proving snapshot project identity wins**

Construct a generic snapshot record whose stable ID is unrelated to its root basename, then assert `ProjectsExtension` publishes exactly that ID/root. Add a bridge assertion that `server.py` contains no private project imports.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_project_registry \
  tests.extensions.test_projects_extension \
  tests.extensions.test_upstream_compatibility
```

- [ ] **Step 3: Publish snapshot service and private adapter**

`projects.register()` uses:

```python
snapshot = context.services.require(CORE_CONFIG_SNAPSHOT)
registry = build_project_registry_from_records(
    snapshot.registered_projects,
    validate_root=workspace_runtimes.validate_root,
)
```

Keep the current settings-based registry builder only as a compatibility adapter for existing direct unit fixtures until tests/callers are migrated.

- [ ] **Step 4: Build developer snapshot before `Runtime`**

Add `build_developer_snapshot(*, runtime_config: RuntimeConfig,
bootstrap_workspace: Path) -> ConfigSnapshot` as the single developer-mode
snapshot constructor.

It normalizes configured v1 registry settings or synthesizes `project_id="default"`, and validates any present default ProjectConfig at startup.

- [ ] **Step 5: Seed snapshot into `ExtensionHost` and run GREEN**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_project_registry \
  tests.extensions.test_projects_extension \
  tests.extensions.test_project_addressing_tools \
  tests.extensions.test_project_addressing_integration \
  tests.extensions.test_upstream_compatibility
```

- [ ] **Step 6: Commit**

```bash
git add coding_tools_mcp/extensions/services.py \
  coding_tools_mcp/extensions/projects/registry.py \
  coding_tools_mcp/extensions/projects/extension.py coding_tools_mcp/server.py \
  tests/extensions/test_project_registry.py tests/extensions/test_projects_extension.py \
  tests/extensions/test_upstream_compatibility.py
git commit -m "refactor: derive project registry from config snapshot"
```

---

### Task 5: Enforce a Real Per-Project Semantic Capability Ceiling

**Files:**
- Modify: `coding_tools_mcp/extensions/semantic/extension.py`
- Modify: `tests/extensions/test_semantic_extension.py`
- Modify: `docs/runtime-contract-v0.4.md`

**Interfaces:**
- Consumes `CORE_CONFIG_SNAPSHOT`.
- Produces deterministic `PROJECT_CAPABILITY_DISABLED` ToolFailure with non-secret details `{project_id, capability}`.
- Does not remove semantic tools dynamically; catalog remains globally frozen when semantic is host-enabled.

- [ ] **Step 1: Write semantic policy RED tests**

Add one test whose snapshot disables semantic for project `blocked` and assert
the tool returns `PROJECT_CAPABILITY_DISABLED` while the fake backend call list
remains empty. Add a second project `allowed` in the same snapshot and assert a
semantic call reaches the fake backend normally. The global tool registry must
still contain all four semantic tools.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.extensions.test_semantic_extension
```

- [ ] **Step 3: Implement one shared guard**

```python
def _require_project_capability(self, project_id: str) -> None:
    effective = self._snapshot.projects[project_id]
    if "semantic" not in effective.enabled_capabilities:
        raise ToolFailure(
            "PROJECT_CAPABILITY_DISABLED",
            "Semantic navigation is disabled by project policy.",
            category="permission",
            retryable=False,
            details={"project_id": project_id, "capability": "semantic"},
        )
```

Call it before each semantic backend operation. Do not alter public semantic schemas or Serena worker ownership.

- [ ] **Step 4: Run GREEN/regression and update v0.4 docs**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_semantic_extension \
  tests.extensions.test_semantic_model \
  tests.extensions.test_semantic_concurrency
```

Document project capability reduction and the typed policy failure in the current contract.

- [ ] **Step 5: Commit**

```bash
git add coding_tools_mcp/extensions/semantic/extension.py \
  tests/extensions/test_semantic_extension.py docs/runtime-contract-v0.4.md
git commit -m "feat: enforce project semantic capability ceilings"
```

---

### Task 6: Add Generic Extension Prepare/Discover Lifecycle Before Freeze

**Files:**
- Modify: `coding_tools_mcp/extensions/api.py`
- Modify: `coding_tools_mcp/extensions/host.py`
- Modify: built-in extension classes only as needed for a consistent no-op `prepare()` contract.
- Modify: `tests/extensions/test_extension_lifecycle.py`
- Regression: `tests/extensions/test_upstream_compatibility.py`

**Interfaces:**
- Lifecycle becomes `configure -> prepare -> register -> compose/freeze -> start`.
- `prepare()` is generic and contains no gateway/upstream names or assumptions.

- [ ] **Step 1: Write exact lifecycle-order RED test**

Expected trace:

```text
configure:a
configure:b
prepare:a
prepare:b
register:a
register:b
start:a
start:b
```

Also cover prepare failure: no registration/start occurs, and cleanup runs in bounded reverse order for prepared instances according to the documented lifecycle contract.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.extensions.test_extension_lifecycle
```

- [ ] **Step 3: Implement generic orchestration**

Prefer an explicit protocol method with no-op built-ins if that keeps static typing clearer than reflective optional calls. Preserve contribution/service freeze before first `start()`.

- [ ] **Step 4: Run GREEN/bridge**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_extension_lifecycle \
  tests.extensions.test_core_bridge \
  tests.extensions.test_upstream_compatibility
```

- [ ] **Step 5: Commit**

```bash
git add coding_tools_mcp/extensions/api.py coding_tools_mcp/extensions/host.py \
  coding_tools_mcp/extensions/projects/extension.py \
  coding_tools_mcp/extensions/semantic/extension.py \
  tests/extensions/test_extension_lifecycle.py tests/extensions/test_upstream_compatibility.py
git commit -m "feat: prepare extensions before catalog freeze"
```

---

### Task 7: Start MCP Runtime From HostConfig and Expose Separate Version Identities

**Files:**
- Create: `coding_tools_mcp/runtime_contract.py`
- Modify: `coding_tools_mcp/server.py`
- Modify: `tests/extensions/test_config_startup.py`
- Modify: `tests/compliance/test_runtime_helpers.py`
- Modify: `tests/compliance/test_mcp_contract.py` only if its server-info expectations require it.

**Interfaces:**
- Adds explicit runtime selector `--host-config PATH`.
- Host selector conflicts with developer `--config`, `--local-config`, and `--extensions` selectors.
- Adds `RUNTIME_CONTRACT_VERSION = "0.4"`.
- `server_info` retains legacy `version = __version__` and adds `package_version`, `runtime_contract_version`, and bounded `configuration` metadata/fingerprint.

- [ ] **Step 1: Write startup/observability RED tests**

Add named tests that parse `--host-config`; reject combining it with each
developer selector; build a runtime whose workspace, permission policy and
enabled extensions come from HostConfig; prove an adjacent local overlay is
ignored; assert `server_info["version"] == server_info["package_version"]` and
`server_info["runtime_contract_version"] == "0.4"`; assert the configuration
fingerprint is present while a test secret value is absent from serialized
server info.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_config_startup \
  tests.compliance.test_runtime_helpers
```

- [ ] **Step 3: Resolve one snapshot before `Runtime` construction**

```python
def resolve_config_snapshot(
    args: argparse.Namespace, *, registry: ExtensionRegistry
) -> ConfigSnapshot:
    if args.host_config is not None:
        host_config = load_host_config(
            Path(args.host_config),
            extension_schemas=registry.schemas(),
            default_enabled=registry.default_enabled,
        )
        return build_host_snapshot(host_config)
    runtime_config = load_runtime_config(
        cwd=Path.cwd(),
        extension_schemas=registry.schemas(),
        default_enabled=registry.default_enabled,
        environ=os.environ,
        public_path=args.config,
        local_path=args.local_config,
        cli_extensions=(
            parse_extension_list(args.extensions)
            if args.extensions is not None
            else None
        ),
    )
    return build_developer_snapshot(
        runtime_config=runtime_config,
        bootstrap_workspace=resolved_workspace,
    )
```

No ProjectConfig read occurs during the first tool request.

- [ ] **Step 4: Consume HostConfig auth only in HTTP auth setup**

Resolve bearer/OAuth `SecretRef` values at the existing HTTP auth consumer. Developer mode preserves current CLI/env OAuth/bearer behavior. Error strings name the reference source only, never its resolved value.

- [ ] **Step 5: Keep package `--version` independent**

`coding_tools_mcp/runtime_contract.py` contains only:

```python
RUNTIME_CONTRACT_VERSION = "0.4"
```

Do not change `__version__` solely because the runtime contract is v0.4.

- [ ] **Step 6: Run GREEN and commit**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.extensions.test_config_startup \
  tests.compliance.test_runtime_helpers \
  tests.compliance.test_mcp_contract
git add coding_tools_mcp/runtime_contract.py coding_tools_mcp/server.py \
  tests/extensions/test_config_startup.py tests/compliance/test_runtime_helpers.py \
  tests/compliance/test_mcp_contract.py
git commit -m "feat: start runtime from immutable host config"
```

---

### Task 8: Converge Services Launcher on the Canonical HostConfig Model

**Files:**
- Modify: `scripts/launcher/config.py`
- Modify: `scripts/start_services.py`
- Modify: `tests/test_launcher_config.py`
- Regression: `tests/test_launcher_integration.py`

**Interfaces:**
- `ServiceConfig` gains an optional HostConfig/snapshot identity while retaining current compatibility fields.
- Host mode derives launcher settings from the canonical model and emits minimal MCP argv: `python -m coding_tools_mcp --host-config <path>` under the existing locked uv invocation.
- Existing CLI/env/dotenv mode remains available as compatibility mode.

- [ ] **Step 1: Write launcher HostConfig RED tests**

Add named tests that assert HostConfig mode builds locked uv argv ending in
`python -m coding_tools_mcp --host-config <resolved-path>`; does not read a
workspace `.env`; maps a profile-file tunnel selection exactly; leaves the
existing compatibility precedence test unchanged/green; rejects combining
HostConfig with `--workspace`, `--host`, `--port`, or permission flags; removes
the environment variable named by the tunnel secret ref from MCP child env.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.test_launcher_config
```

- [ ] **Step 3: Add explicit launcher HostConfig mode**

Call the same `load_host_config()` and snapshot builder as the runtime. Map canonical tunnel settings to the existing launcher `TunnelSelection`; do not add another TOML parser.

- [ ] **Step 4: Preserve narrow environment separation**

Keep `scrub_mcp_environment()` and ensure HostConfig tunnel `env:` refs are stripped from the MCP child while remaining available to tunnel-client.

- [ ] **Step 5: Run launcher GREEN/regression**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_launcher_config tests.test_launcher_integration \
  tests.test_launcher_diagnostics tests.test_launcher_processes tests.test_launcher_tunnel
```

- [ ] **Step 6: Commit**

```bash
git add scripts/launcher/config.py scripts/start_services.py \
  tests/test_launcher_config.py tests/test_launcher_integration.py
git commit -m "feat: converge launcher on host configuration"
```

---

### Task 9: Add Deterministic Deployment Preflight With No Long-Lived Startup

**Files:**
- Create: `scripts/launcher/preflight.py`
- Create: `tests/test_launcher_preflight.py`
- Modify: `scripts/launcher/config.py`
- Modify: `scripts/launcher/app.py`
- Modify: `scripts/start_services.py`
- Modify: `tests/test_launcher_integration.py`

**Interfaces:**
- Produces immutable `PreflightFinding` and `PreflightReport`.
- Produces `run_preflight(config, *, port_probe: Callable[[str, int], bool]) -> PreflightReport`.
- Adds `--preflight`; it exits without allocating/starting normal MCP/tunnel children.
- Preserves the legacy `--doctor-only` meaning instead of silently redefining it.

- [ ] **Step 1: Write preflight RED tests**

Use temporary roots and injected probes to assert: visible unique roots yield
`report.ok`; missing root yields `PROJECT_ROOT_NOT_VISIBLE`; occupied port
yields `LISTENER_PORT_IN_USE`; a non-writable external runtime root yields
`RUNTIME_ROOT_NOT_WRITABLE`; a runtime root inside a registered source root
yields `RUNTIME_ROOT_INSIDE_PROJECT`; semantic enabled with any distribution
version other than `1.5.3` yields `SEMANTIC_BACKEND_VERSION`; an existing
profile file is validated by metadata only; serialized report text does not
contain an injected secret; preflight-only integration leaves the fake process
starter call list empty.

- [ ] **Step 2: Run RED**

```bash
uv run --locked --no-sync python -m unittest -v tests.test_launcher_preflight
```

- [ ] **Step 3: Implement reusable checks**

Root visibility is checked in the current namespace; when the same command runs as candidate `ExecStartPre`, it therefore validates systemd mount visibility. Use `importlib.metadata.version("serena-agent") == "1.5.3"` only when semantic is host-enabled. Writable-root checks create/remove a private sentinel only under configured runtime/state/cache roots, never source roots.

- [ ] **Step 4: Wire `--preflight` before `run_services()`**

Return `0` when `report.ok`, `2` on fatal findings. Render bounded non-secret findings and the config fingerprint.

- [ ] **Step 5: Run GREEN and commit**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_launcher_preflight tests.test_launcher_config tests.test_launcher_integration
git add scripts/launcher/preflight.py scripts/launcher/config.py scripts/launcher/app.py \
  scripts/start_services.py tests/test_launcher_preflight.py tests/test_launcher_integration.py
git commit -m "feat: add deterministic deployment preflight"
```

---

### Task 10: Harden Public Hygiene and Current Operator Documentation

**Files:**
- Modify: `tests/test_public_fork_hygiene.py`
- Modify: `docs/services-launcher.md`
- Modify: `docs/runtime-contract-v0.4.md`
- Modify: current README/operator link only if required by docs compliance.
- Optionally create a generic HostConfig example only if it contains no real deployment markers.

**Interfaces:**
- Public docs distinguish developer v1 mode from system HostConfig v2 mode.
- Public examples use synthetic `/srv/projects/example` roots and generic tunnel names.
- Hygiene keeps real unit/config/tunnel/project inventory untracked.

- [ ] **Step 1: Extend hygiene assertions before adding examples**

Protect the chosen private production HostConfig filename/location pattern without rejecting generic schema documentation. Keep the existing private-marker scanner intact.

- [ ] **Step 2: Document explicit launch modes**

```text
Developer compatibility:
  coding-tools.toml + coding-tools.local.toml + env + CLI

System deployment:
  scripts/start_services.py --host-config /etc/coding-tools-mcp/config.toml
  scripts/start_services.py --host-config /etc/coding-tools-mcp/config.toml --preflight
```

State explicitly that HostConfig cannot widen systemd `ProtectHome`/bind mounts.

- [ ] **Step 3: Document version observability**

`docs/runtime-contract-v0.4.md` must specify that CLI/package version is independent of runtime contract v0.4 and `server_info` exposes both identities.

- [ ] **Step 4: Run docs/hygiene GREEN**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_public_fork_hygiene tests.compliance.test_docs_required
```

- [ ] **Step 5: Commit reviewed docs only**

```bash
git status --short
git add tests/test_public_fork_hygiene.py docs/services-launcher.md docs/runtime-contract-v0.4.md
git commit -m "docs: define host config operator workflow"
```

If README/example files were actually changed, add them explicitly after reviewing `git diff`; never use broad staging to mask unrelated work.

---

### Task 11: C1/C2 Fresh Verification Checkpoint Before Production Mutation

**Files:**
- No intended source changes. Any failure gets its own TDD fix cycle and commit before proceeding.

**Interfaces:**
- Produces a clean, tested implementation baseline suitable for C3 deployment work.

- [ ] **Step 1: Revalidate Git and remote refs**

```bash
git status --short --branch
git ls-remote xyTom refs/heads/main
git ls-remote origin refs/heads/main
```

If original upstream moved, fetch, advance `sync/upstream-main`, integrate deliberately, then rerun bridge gates. Do not rewrite fork history merely to prefer a rebase aesthetic.

- [ ] **Step 2: Run all extension tests**

```bash
uv run --locked --no-sync python -m unittest discover -s tests/extensions -p 'test_*.py' -v
```

- [ ] **Step 3: Run launcher gate**

```bash
mise run test-launcher
```

- [ ] **Step 4: Run full repository verification**

```bash
mise run verify
```

Report actual fresh totals rather than historical counts.

- [ ] **Step 5: Run exact isolated Serena integration**

Inspect the current semantic CI workflow/Phase B plan first, then execute its supported isolated command for `serena-agent==1.5.3` and its expected MCP dependency. Do not treat the mixed dev environment as the integration environment.

- [ ] **Step 6: Run explicit bridge/privacy gates**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.test_public_fork_hygiene tests.extensions.test_upstream_compatibility
```

- [ ] **Step 7: Require clean tree**

```bash
git status --short --branch
```

Do not begin C3 with unexplained WIP.

---

### Task 12: Create Private Production HostConfig and Candidate Sandbox (C3)

**Files:**
- Private/untracked only: selected system HostConfig, tunnel reference/profile, service environment, ignored candidate unit source.

**Interfaces:**
- Consumes the live roots, current hardening, and tunnel data re-discovered immediately before mutation.
- Produces one HostConfig fingerprint accepted by preflight and runtime.

- [ ] **Step 1: Re-discover actual deployment inventory**

Use `systemctl show`, `ss`, current process namespaces, and current private profile references. Do not echo secret values or place real inventory in tracked docs.

- [ ] **Step 2: Verify deployment privileges before writes**

Test whether the connector context can create/update the selected `/etc/coding-tools-mcp` state and reload/start systemd non-interactively. If privilege is unavailable, stop claiming C3 completion and report the exact blocker while retaining completed code/gates.

- [ ] **Step 3: Materialize private HostConfig with restrictive permissions**

Use revalidated same-trust roots, projects+semantic enablement, loopback HTTP, intended permission mode, exact semantic limits, external runtime/state/cache roots, and private tunnel reference. Store only secret references.

- [ ] **Step 4: Prepare exact semantic production environment**

Use the repository's current supported semantic sync/install strategy and verify the exact backend/dependency versions from the resulting production environment, not from the dev extra.

- [ ] **Step 5: Build candidate hardened unit**

Retain the currently verified hardening family, including:

```text
ProtectSystem=strict
ProtectHome=tmpfs
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
```

Add explicit bind mounts for every registered root and only the external config/tool paths genuinely required.

- [ ] **Step 6: Run preflight outside and inside candidate unit namespace**

The candidate `ExecStartPre` (or equivalent transient systemd invocation with the same hardening) runs the canonical `--preflight`, proving all roots are visible under `ProtectHome` and paths are writable where required.

- [ ] **Step 7: Prove no private state became tracked**

```bash
git status --short
uv run --locked --no-sync python -m unittest -v tests.test_public_fork_hygiene
```

---

### Task 13: Start Reversible Candidate and Perform Live Multi-Project Acceptance (C4-A)

**Files:**
- Private/untracked candidate unit/tunnel material only unless acceptance exposes a code defect.

**Interfaces:**
- Existing services remain available until every candidate criterion passes.

- [ ] **Step 1: Select a free temporary loopback port**

Verify with `ss` and canonical preflight immediately before startup.

- [ ] **Step 2: Start candidate without retiring old units**

Verify process tree contains one services launcher supervising one MCP child and only the intended tunnel child.

- [ ] **Step 3: Verify candidate server identity/catalog**

Using the repository MCP client/compliance helper, assert:

```text
package_version is explicit
runtime_contract_version == 0.4
configuration fingerprint == preflight fingerprint
projects and semantic enabled
tool_count == 28
```

- [ ] **Step 4: Exercise `list_projects` and `resolve_project`**

Every intended real same-trust project must be present under its stable ID and resolve deterministically.

- [ ] **Step 5: Prove filesystem routing isolation on at least two real projects**

Read harmless relative markers from project A and B; where possible use the same relative filename to prove routing does not cross-contaminate.

- [ ] **Step 6: Prove Git routing isolation on at least two real projects**

Run live project-scoped Git status/log and verify branch/HEAD/status belong to the selected repository.

- [ ] **Step 7: Prove real Serena isolation on at least two real projects**

Choose existing supported-language symbols and run symbol/definition/reference operations. All returned paths must remain in the selected project namespace; same/common symbol names must not cross projects.

- [ ] **Step 8: Verify semantic/runtime state stays outside source roots**

Search all registered roots for `.serena`; expected none created. Confirm worker HOME/cache/runtime state is under configured external runtime state.

- [ ] **Step 9: Verify candidate tunnel health when used**

Use the launcher/tunnel client's doctor/health interface with the private profile; do not print credentials.

- [ ] **Step 10: On failure, preserve rollback**

Stop only the candidate, keep old working services, capture bounded diagnostics, fix the defect with a fresh TDD cycle, and repeat candidate acceptance.

---

### Task 14: Cut Over to One Canonical Same-Trust Unit and Re-Accept ChatGPT Connector (C4-B)

**Files:**
- Private/untracked deployment state only unless a code defect is found.

**Interfaces:**
- Produces one canonical same-trust unit, one MCP endpoint, and one intended tunnel path.

- [ ] **Step 1: Capture rollback facts privately**

Record old unit active states, listeners, and private unit targets in command output/diagnostics only; do not create a tracked deployed-instance document.

- [ ] **Step 2: Stop/disable redundant old same-trust units only after candidate GREEN**

Do not delete definitions until final acceptance completes.

- [ ] **Step 3: Run final preflight and start canonical endpoint/tunnel**

Recheck port/root/system state immediately before start because it may have changed since candidate acceptance.

- [ ] **Step 4: Verify obsolete listeners/units are gone**

Use `ss` and `systemctl` to prove only the intended canonical same-trust service remains.

- [ ] **Step 5: Repeat the live 28-tool/project/filesystem/Git/Serena/no-`.serena` acceptance**

Repeat Task 13 against the final endpoint, not the temporary candidate.

- [ ] **Step 6: Re-discover the actual `@Coding-Tools-Repo` ChatGPT connector**

Service restarts can invalidate the connector connection. Retry discovery plus a real `server_info` invocation. It must expose v0.4-capable project/semantic tools rather than the stale 22-tool runtime.

- [ ] **Step 7: Revalidate security boundary**

Confirm loopback listener, intended auth state, intended `dangerous` mode where configured, active systemd hardening, healthy tunnel, and no accidental direct public listener.

- [ ] **Step 8: Remove obsolete private definitions after final GREEN**

Retain only the canonical unit/tunnel material plus services belonging to genuinely separate trust domains.

---

### Task 15: Semantic Spec/Plan Status Cleanup (C5)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-extension-architecture-config-design.md`
- Modify: `docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md`
- Modify: `docs/superpowers/specs/2026-08-16-development-runtime-gateway-hooks-work-coordination-design.md`
- Modify: `docs/superpowers/specs/2026-08-03-project-skills-context-design.md`
- Modify: relevant `docs/superpowers/plans/*.md` status headers.

**Interfaces:**
- Uses semantic categories only: `IMPLEMENTED + VERIFIED`, `SUPERSEDED`, `HISTORICAL EXECUTION PLAN`, `PROPOSED / NOT IMPLEMENTED`.

- [ ] **Step 1: Mark implemented phases without checkbox archaeology**

Phase 0/A/B plans become historical execution plans with pointers to current contract/spec. Do not mark hundreds of individual boxes.

- [ ] **Step 2: Mark superseded configuration assumptions**

The broad gateway/hooks/work design remains proposed, but its old global/workspace precedence and `${ENV:...}` syntax are explicitly superseded by the HostConfig design.

- [ ] **Step 3: Refresh stale current-facing snapshots only**

Remove “ready for implementation” wording for shipped features and stale present-tense HEAD/upstream/test totals. Preserve actual historical contract/release records.

- [ ] **Step 4: Run docs/hygiene GREEN**

```bash
uv run --locked --no-sync python -m unittest -v \
  tests.compliance.test_docs_required tests.test_public_fork_hygiene
```

- [ ] **Step 5: Commit**

```bash
git status --short
git add docs/superpowers/specs docs/superpowers/plans
git commit -m "docs: align architecture status with deployed v0.4"
```

Review staged paths first and unstage any unrelated concurrent docs before committing.

---

### Task 16: Final Verification, Upstream Revalidation, and Normal Fork Push

**Files:**
- No intended source changes. Any failure must be fixed and reverified before publication.

**Interfaces:**
- Produces a clean, upstream-integrated local `main`, final live deployment evidence, and ordinary fast-forward push to the fork.

- [ ] **Step 1: Apply `superpowers:verification-before-completion`**

Do not state “complete”, “solid”, “green”, or equivalent without fresh command evidence from the final state.

- [ ] **Step 2: Run final full gates**

```bash
mise run verify
mise run test-launcher
mise run check-npm
```

Inspect current task/workflow definitions and additionally run any compliance or exact isolated Serena gate not included by `verify`.

- [ ] **Step 3: Revalidate final live service after code gates**

Confirm the canonical unit/listener/tunnel is still healthy and the live connector still exposes the accepted catalog/fingerprint.

- [ ] **Step 4: Refresh original upstream one final time**

```bash
git ls-remote xyTom refs/heads/main
git fetch --prune xyTom main
```

If upstream moved, advance `sync/upstream-main`, integrate deliberately, and rerun affected bridge/full/live gates.

- [ ] **Step 5: Prove ancestry and ordinary push safety**

```bash
git merge-base --is-ancestor xyTom/main HEAD
git merge-base --is-ancestor origin/main HEAD
git rev-list --left-right --count xyTom/main...HEAD
git rev-list --left-right --count origin/main...HEAD
git status --short --branch
```

Expected: clean tree, original upstream integrated, `origin/main` ancestor of local `main`.

- [ ] **Step 6: Push normally and verify refs**

```bash
git push origin main
git ls-remote origin refs/heads/main
git ls-remote xyTom refs/heads/main
```

Do not force-push absent a new exceptional, explicit, evidence-backed reason.

---

## Spec Coverage Checklist

- Purpose/current v0.4/supersession: Tasks 7, 10, 15.
- Four config identities and resolution modes: Tasks 1-3, 7-8.
- Strict schemas and unknown-key failure: Tasks 1-3.
- Authority lattice/security ceilings: Tasks 3 and 5.
- Host runtime/transport/security/extensions/deployment ownership: Tasks 2, 7, 8.
- ProjectConfig startup/path/nested/symlink rules: Tasks 3-4.
- Immutable ConfigSnapshot/fingerprint: Tasks 3-4, 7.
- Secret refs and narrow resolution/redaction: Tasks 2, 7-9.
- Generic pre-freeze prepare/discover lifecycle: Task 6.
- Launcher/runtime convergence: Tasks 7-9.
- Package/runtime-contract version separation: Tasks 7 and 10.
- Systemd OS boundary/root visibility: Tasks 9, 12-14.
- Deterministic doctor/preflight: Task 9 and Task 12.
- Reversible migration/live acceptance: Tasks 12-14.
- Real multi-project filesystem/Git/Serena isolation and no repo `.serena`: Tasks 13-14.
- Future Hooks/Work/Gateway implementation: intentionally excluded; Task 6 supplies only the approved generic lifecycle prerequisite.
- Public-fork hygiene: Tasks 10, 12, 15.
- Semantic status cleanup: Task 15.
- Fresh gates/upstream-sync/push: Tasks 11 and 16.
- No worktrees: global constraint and all tasks.

## Execution Order / Checkpoints

```text
1  shared schema primitives
2  HostConfig v2
3  ProjectConfig + authority + ConfigSnapshot
4  snapshot-backed ProjectRegistry
5  real semantic project ceiling
6  generic prepare/discover lifecycle
7  MCP HostConfig startup + observability
8  launcher convergence
9  deterministic preflight
10 public docs/hygiene
11 full C1/C2 verification checkpoint
12 private production HostConfig + candidate sandbox
13 candidate live acceptance
14 final one-unit cutover + ChatGPT connector acceptance
15 semantic docs/status cleanup
16 final gates + upstream refresh + normal push
```

Do not proceed from Task 11 to C3 with a dirty tree or failed gate. Do not
retire old services before Task 13 is fully green. Do not push until Task 14
has proven the final live connector and Task 16 has rerun final verification.
