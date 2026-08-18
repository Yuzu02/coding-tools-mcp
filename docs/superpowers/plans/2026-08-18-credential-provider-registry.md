# Dynamic Credential Provider Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static credential configuration with a dynamically reloaded, host-managed provider registry whose broker stores are isolated from every unauthorized command child.

**Architecture:** `CredentialProviderRegistry` loads atomic root-managed TOML fragments into immutable snapshots. Runtime selection uses that snapshot to scrub environment values and builds a credential-isolation Landlock profile for every command, adding only the selected provider's broker roots. A separate host-only script owns broker provisioning and fragment publication.

**Tech Stack:** Python 3.13 stdlib (`tomllib`, `hashlib`, `os`, `pathlib`), existing HostConfig validation, Linux Landlock ctypes wrapper, `unittest`, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-18-credential-provider-registry-design.md`

## Global Constraints

- Work only within this coding-tools-mcp repository; do not modify unrelated repositories.
- Do not mount a home directory, print credential material, deploy, push, or use a worktree.
- The MCP runtime must never mount filesystems, modify systemd, invoke sudo, or change ownership/modes outside its runtime directories.
- A registry error must discard previous grants and scrub secret-like environment values; it must never preserve stale credential access.
- Broker roots are canonical descendants of the selected provider directory under `<state-root>/credentials`.
- Once broker isolation is enabled, every `exec_command` must receive the credential-isolation Landlock profile; non-provider commands receive no broker root.
- Do not reuse `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS` in the credential-isolation profile.
- Existing `exec_policy.secret_env_filter` remains compatible; new metadata distinguishes it from provider filtering and filesystem isolation.
- Keep the unrelated `oauth_config` mypy redeclaration outside this change unless directly required.
- Each behavior starts with a focused failing test and a confirmed RED run before implementation.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `coding_tools_mcp/credential_providers.py` | Immutable provider/snapshot models, fragment parsing, generation detection, and fail-closed reload. |
| `coding_tools_mcp/host_config.py` | Derive registry/broker locations from HostConfig; remove static `exec_credentials` authority. |
| `coding_tools_mcp/server.py` | Runtime registry integration, environment selection, metadata, all-command credential Landlock profile, instance details. |
| `coding_tools_mcp/credential_admin.py` | Testable host-only provisioning/list/doctor/remove operations; never imported by runtime. |
| `scripts/credentials.py` | Thin argparse entry point for the host-only operations. |
| `tests/test_credential_providers.py` | Registry parsing, generations, reload, invalidation, and metadata redaction tests. |
| `tests/compliance/test_runtime_helpers.py` | Runtime environment and Landlock observable behavior tests. |
| `tests/test_credential_admin.py` | Administrative tool dry-run, containment, atomic publication, and no-secret-output tests. |
| `docs/services-launcher.md` | Public generic setup and explicit security trade-off. |
| `docs/credential-provider-migration.md` | One root-only migration/rollback block with deterministic unit rollback and non-destructive credential handling; documented, never auto-executed. |

## Task 1: Define the Dynamic Registry Model and Host Locations

**Files:**
- Create: `coding_tools_mcp/credential_providers.py`
- Modify: `coding_tools_mcp/host_config.py:114-129,258-280,362-425,855-890`
- Modify: `tests/test_host_config.py:91-220`
- Create: `tests/test_credential_providers.py`

**Interfaces:**
- Consumes: `ConfigError`, `HostRuntimeConfig.state_root`, existing `_parse_exec_credential_env_path`, `EXEC_CREDENTIAL_COMMAND_RE`.
- Produces: `CredentialProvider`, `CredentialRegistrySnapshot`, `CredentialProviderRegistry`, `credential_registry_dir(config) -> Path`, and `credential_broker_dir(config) -> Path`.

- [ ] **Step 1: Write the failing HostConfig and registry tests**

```python
def test_host_config_derives_private_registry_and_broker(self) -> None:
    config = load_host_config(config_path, extension_schemas={}, default_enabled=())
    self.assertEqual(credential_registry_dir(config), config_path.parent / "credentials.d")
    self.assertEqual(credential_broker_dir(config), state_root / "credentials")

def test_registry_rejects_provider_root_outside_own_broker(self) -> None:
    registry = CredentialProviderRegistry(registry_dir, broker_dir)
    (registry_dir / "bad.toml").write_text('name="bad"\ncommands=["bad"]\nread_roots=["/tmp"]\n')
    snapshot = registry.snapshot()
    self.assertEqual(snapshot.health, "invalid")
    self.assertEqual(snapshot.providers, ())
```

Name the protected break: changing the containment predicate to accept a root
outside `<broker>/<provider>` must make the second test fail.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run python -m unittest tests.test_host_config tests.test_credential_providers -v`

Expected: import failure for `credential_providers` and missing location helpers; no pre-existing test is changed to pass by accident.

- [ ] **Step 3: Implement immutable models and location derivation**

```python
@dataclass(frozen=True)
class CredentialProvider:
    name: str
    commands: tuple[str, ...]
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    env_passthrough: tuple[str, ...]
    env_paths: tuple[tuple[str, Path], ...]

def credential_registry_dir(config: HostConfig) -> Path:
    return config.source.parent / "credentials.d"

def credential_broker_dir(config: HostConfig) -> Path:
    runtime = config.runtime
    if runtime is None or runtime.state_root is None:
        raise ConfigError("HostConfig runtime.state_root is required for credential providers")
    return runtime.state_root / "credentials"
```

Move the existing static provider validators into the new module as pure
functions. Parse one provider per fragment with `tomllib.loads`; require
canonical descendant roots under `broker_dir / name`; reject duplicate names,
duplicate command ownership, and symlinked declared roots. Remove
`security.exec_credentials` from the HostConfig schema and dataclass rather
than retaining a second authorization path.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `uv run python -m unittest tests.test_host_config tests.test_credential_providers -v`

Expected: all targeted tests pass; legacy static credential config fails as an
unknown HostConfig key.

## Task 2: Add Atomic, Fail-closed Registry Reload

**Files:**
- Modify: `coding_tools_mcp/credential_providers.py`
- Modify: `tests/test_credential_providers.py`

**Interfaces:**
- Consumes: `CredentialProvider` and `CredentialRegistrySnapshot` from Task 1.
- Produces: `CredentialProviderRegistry.snapshot() -> CredentialRegistrySnapshot`, `snapshot.generation`, `snapshot.fingerprint`, `snapshot.health`, `snapshot.error`.

- [ ] **Step 1: Write failing reload tests**

```python
def test_registry_reloads_add_and_remove_without_recreation(self) -> None:
    registry = CredentialProviderRegistry(registry_dir, broker_dir)
    self.assertEqual(registry.snapshot().providers, ())
    atomic_fragment(registry_dir / "a.toml", valid_fragment("a", "a-cli"))
    first = registry.snapshot()
    atomic_fragment(registry_dir / "b.toml", valid_fragment("b", "b-cli"))
    second = registry.snapshot()
    (registry_dir / "a.toml").unlink()
    third = registry.snapshot()
    self.assertEqual([item.name for item in first.providers], ["a"])
    self.assertEqual([item.name for item in second.providers], ["a", "b"])
    self.assertEqual([item.name for item in third.providers], ["b"])
    self.assertNotEqual(first.generation, second.generation)

def test_invalid_replacement_discards_previous_provider_grants(self) -> None:
    atomic_fragment(registry_dir / "a.toml", valid_fragment("a", "a-cli"))
    self.assertEqual(len(registry.snapshot().providers), 1)
    atomic_fragment(registry_dir / "a.toml", 'name="a"\ncommands=[]\n')
    snapshot = registry.snapshot()
    self.assertEqual(snapshot.health, "invalid")
    self.assertEqual(snapshot.providers, ())
    self.assertNotIn("a-cli", snapshot.command_owners)
```

- [ ] **Step 2: Run the two tests and confirm RED**

Run: `uv run python -m unittest tests.test_credential_providers.CredentialProviderRegistryTests -v`

Expected: methods or immutable generation handling are missing; the failure is
not caused by temporary-directory permissions.

- [ ] **Step 3: Implement generation comparison and atomic writer**

```python
def _directory_generation(self) -> tuple[tuple[str, int, int, int, int], ...]:
    entries = []
    for path in sorted(self.registry_dir.glob("*.toml")):
        entry = path.stat()
        if stat.S_ISREG(entry.st_mode):
            entries.append((path.name, entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns))
    return tuple(entries)

def snapshot(self) -> CredentialRegistrySnapshot:
    generation = self._directory_generation()
    if generation != self._generation:
        self._snapshot = self._load_generation(generation)
        self._generation = generation
    return self._snapshot
```

`_load_generation` must catch parse/validation failures and return an invalid
snapshot with no providers or command owners. Its error string is bounded and
must not include TOML values. Implement `atomic_write_fragment()` with a
same-directory `NamedTemporaryFile`, `flush`, `os.fsync`, `os.replace`, and
directory fsync; it is used only by Task 5's administrative module.

- [ ] **Step 4: Run the registry suite and confirm GREEN**

Run: `uv run python -m unittest tests.test_credential_providers -v`

Expected: add/remove/replacement works through one registry object, malformed
replacement has no stale grants, and fingerprint/generation change only after
a whole fragment publication.

## Task 3: Integrate Runtime Selection, Environment Scrubbing, and Metadata

**Files:**
- Modify: `coding_tools_mcp/server.py:1750-1795,2228-2272,3454-3555,6726-6752`
- Modify: `tests/compliance/test_runtime_helpers.py:521-628,730-770`
- Modify: `tests/extensions/test_project_server_context.py`

**Interfaces:**
- Consumes: `CredentialProviderRegistry.snapshot()`, `CredentialProvider`, HostConfig location helpers.
- Produces: `Runtime.credential_registry`, `Runtime._credential_provider(command)`, `Runtime._credential_snapshot()`, and `server_info["credential_providers"]`.

- [ ] **Step 1: Write failing runtime tests**

```python
def test_registry_scrubs_secrets_for_non_provider_and_only_allows_selected_provider(self) -> None:
    runtime = Runtime(workspace, permission_mode="dangerous", credential_registry=registry)
    with patch.dict(server_module.os.environ, {"PATH": "/usr/bin", "A_TOKEN": "a", "B_TOKEN": "b"}, clear=True):
        direct = runtime._command_env({}, command="a-cli status")
        ordinary = runtime._command_env({}, command="printf ok")
    self.assertEqual(direct["A_TOKEN"], "a")
    self.assertNotIn("B_TOKEN", direct)
    self.assertNotIn("A_TOKEN", ordinary)
    self.assertNotIn("B_TOKEN", ordinary)

def test_server_info_separates_global_and_credential_filters_without_values(self) -> None:
    info = runtime.server_info_payload()
    self.assertEqual(info["exec_policy"]["secret_env_filter"], "disabled")
    self.assertEqual(info["credential_providers"]["sensitive_env_filter"], "enabled-when-registry-present")
    self.assertNotIn("a", repr(info["credential_providers"]))
```

The last assertion must use a test token value that cannot appear in names or
paths, for example `"credential-value-for-test-only"`.

- [ ] **Step 2: Run the named tests and confirm RED**

Run: `uv run python -m unittest tests.compliance.test_runtime_helpers.RuntimeHelperTests.test_registry_scrubs_secrets_for_non_provider_and_only_allows_selected_provider tests.compliance.test_runtime_helpers.RuntimeHelperTests.test_server_info_separates_global_and_credential_filters_without_values -v`

Expected: `Runtime` lacks the registry injection and `credential_providers`
metadata.

- [ ] **Step 3: Replace static Runtime credentials with lazy snapshots**

```python
def _credential_snapshot(self) -> CredentialRegistrySnapshot:
    return self.credential_registry.snapshot()

def _credential_provider(self, command: str | None) -> CredentialProvider | None:
    snapshot = self._credential_snapshot()
    if snapshot.health != "healthy":
        return None
    return snapshot.provider_for_simple_command(command)
```

Instantiate `CredentialProviderRegistry` only in HostConfig mode, deriving
paths from Task 1. When its directory is present, scrub every sensitive name
before extra environment values are considered. Reinject only a matching
healthy provider's allowlisted names. Render registry health, generation,
fingerprint, bounded error, backend status, provider metadata, process start
time, `server_instance_id`, and a SHA-256 tool-name fingerprint in
`server_info`. Preserve the old `exec_credential_providers` key as an empty
compatibility alias only if current in-repository consumers require it; do not
use it for decisions.

- [ ] **Step 4: Run runtime and project-server tests and confirm GREEN**

Run: `uv run python -m unittest tests.compliance.test_runtime_helpers tests.extensions.test_project_server_context -v`

Expected: selected provider variables work, malformed registry never grants a
value, HOME remains runtime-owned, and project `server_info` decoration retains
the new global metadata.

## Task 4: Enforce Credential-isolation Landlock for Every Command

**Files:**
- Modify: `coding_tools_mcp/server.py:2114-2119,3108-3229,5190-5335`
- Modify: `tests/compliance/test_runtime_helpers.py:67-106,610-628,1000-1045`
- Create: `tests/test_credential_landlock.py`

**Interfaces:**
- Consumes: runtime registry snapshot/provider from Task 3 and existing `open_landlock_ruleset`/`landlock_exec_argv`.
- Produces: `open_credential_landlock_ruleset(workspace, runtime_roots, provider) -> int` and `Runtime._credential_landlock_roots(command, workdir) -> CredentialLandlockRoots`.

- [ ] **Step 1: Write failing filesystem-open tests**

```python
def test_non_provider_landlock_cannot_open_any_broker_store(self) -> None:
    result = run_restricted("head -c 0 provider_a/store; head -c 0 provider_b/store")
    self.assertEqual(result["provider_a"], "BLOCKED")
    self.assertEqual(result["provider_b"], "BLOCKED")

def test_provider_landlock_can_open_only_its_own_store_and_write_root(self) -> None:
    result = run_provider_restricted("a-cli", provider_a, provider_b)
    self.assertEqual(result["selected_read"], "READABLE")
    self.assertEqual(result["sibling_read"], "BLOCKED")
    self.assertEqual(result["selected_write"], "WRITABLE")
```

`run_restricted` must attempt `head -c 0`, not `test -r`: the latter only
checks DAC permissions and would not prove Landlock file-open denial.

- [ ] **Step 2: Run the Landlock tests and confirm RED**

Run: `uv run python -m unittest tests.test_credential_landlock -v`

Expected: current dangerous non-provider execution can open the test broker or
the new profile helper is absent.

- [ ] **Step 3: Implement separate all-command credential profiles**

```python
def _credential_landlock_roots(
    self, command: str, workdir: Path
) -> CredentialLandlockRoots:
    provider = self._credential_provider(command)
    return CredentialLandlockRoots(
        read_roots=credential_system_roots(self._command_env({}, workdir=workdir, command=command), workdir, self),
        write_roots=credential_runtime_write_roots(self, workdir),
        provider=provider,
    )
```

Use a new narrow `credential_system_roots` helper that includes required
system/toolchain paths, canonical resolved executable parents, the selected
workspace, and runtime directories. It must not call `guard_allow_roots` or
read `CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS`. Append provider read/write roots only
when provider selection is healthy. In `exec_command`, use this ruleset for
all HostConfig runtime children after broker bootstrap, including dangerous
non-provider commands. If creation or `restrict_self` fails, return
`CREDENTIAL_SANDBOX_UNAVAILABLE` before spawning the child.

- [ ] **Step 4: Run Landlock and existing process tests and confirm GREEN**

Run: `uv run python -m unittest tests.test_credential_landlock tests.compliance.test_runtime_helpers -v`

Expected: actual opens establish non-provider and cross-provider denial,
selected store writes work, safe/trusted existing Landlock tests stay green,
and dangerous non-provider metadata states `enforced_for = "all_exec"`.

## Task 5: Implement Host-only Credential Administration

**Files:**
- Create: `coding_tools_mcp/credential_admin.py`
- Create: `scripts/credentials.py`
- Create: `tests/test_credential_admin.py`

**Interfaces:**
- Consumes: `CredentialProviderRegistry`, `atomic_write_fragment`, broker/registry helpers from Tasks 1-2.
- Produces: `CredentialAdmin.list()`, `.doctor(system=False)`, `.provision(request, apply=False)`, `.remove(name, apply=False)` and argparse commands `list`, `doctor`, `provision`, `remove`.

- [ ] **Step 1: Write failing administrative tests**

```python
def test_provision_dry_run_does_not_publish_or_copy(self) -> None:
    report = admin.provision(request, apply=False)
    self.assertEqual(report["action"], "provision")
    self.assertFalse((registry_dir / "example.toml").exists())
    self.assertFalse((broker_dir / "example").exists())

def test_remove_apply_refuses_broker_escape(self) -> None:
    with self.assertRaisesRegex(CredentialAdminError, "provider broker subtree"):
        admin.remove("../outside", apply=True)
```

- [ ] **Step 2: Run admin tests and confirm RED**

Run: `uv run python -m unittest tests.test_credential_admin -v`

Expected: import or operation methods are absent.

- [ ] **Step 3: Implement root-gated administrative operations**

```python
def require_root(*, operation: str, euid: int | None = None) -> None:
    if (os.geteuid() if euid is None else euid) != 0:
        raise CredentialAdminError(f"{operation} requires explicit root execution")

def remove(self, name: str, *, apply: bool) -> dict[str, object]:
    target = validated_provider_subtree(self.broker_dir, name)
    fragment = self.registry_dir / f"{name}.toml"
    plan = {"action": "remove", "provider": name, "fragment": str(fragment), "broker": str(target), "apply": apply}
    if not apply:
        return plan
    require_root(operation="remove")
    fragment.unlink(missing_ok=True)
    shutil.rmtree(target)
    return plan
```

Validate source trees as regular files/directories with no symlinks or device
nodes; stage copies under the broker parent; set directories `0700` and files
`0600`; chown to the configured service account; atomically publish only after
the staged broker tree is complete. `doctor --system` may call `systemctl show`
only when root explicitly requests it. Keep runtime modules free of imports
from `credential_admin`.

- [ ] **Step 4: Run admin tests and CLI smoke checks**

Run: `uv run python -m unittest tests.test_credential_admin -v && uv run python scripts/credentials.py --help`

Expected: dry runs have no side effects, unsafe removal is rejected, help lists
the four commands, and output contains no source-file contents.

## Task 6: Document Migration and Run Repository Verification

**Files:**
- Modify: `docs/services-launcher.md:450-535`
- Create: `docs/credential-provider-migration.md`
- Modify: `docs/superpowers/specs/2026-08-18-credential-provider-registry-design.md` only if implementation reveals a verified design correction.

**Interfaces:**
- Consumes: commands and metadata from Tasks 1-5.
- Produces: public generic operator guidance and exactly one documented root migration/rollback block. Unit/drop-in rollback is deterministic; credential provisioning is not blindly rerunnable and rollback never deletes credential state.

- [ ] **Step 1: Write documentation acceptance tests or command examples**

Use executable CLI examples, not source-text grep: create a temporary HostConfig
and registry, run `scripts/credentials.py doctor` and `list`, then assert
non-secret JSON/path-only output and exit codes in `tests/test_credential_admin.py`.

- [ ] **Step 2: Run the documentation-backed CLI test and confirm RED/GREEN as needed**

Run: `uv run python -m unittest tests.test_credential_admin.CredentialAdminCliTests -v`

Expected: only pass after the public command names and dry-run behavior match
the documentation.

- [ ] **Step 3: Write generic operator documentation**

Document the provider TOML format, the all-command Landlock trade-off, no-home
invariant, and the root-only migration sequence. The migration block must stop
the unit, provision copies, remove only credential bind paths, daemon-reload,
start, verify, and show rollback; it must never embed machine-specific paths,
provider store contents, tokens, tunnel identifiers, or a command that runs
automatically from MCP.

- [ ] **Step 4: Execute repository checks before any live migration**

Run:

```bash
uv run python -m unittest
uv run ruff check coding_tools_mcp tests scripts
uv run mypy coding_tools_mcp/credential_providers.py coding_tools_mcp/credential_admin.py coding_tools_mcp/host_config.py coding_tools_mcp/server.py
git diff --check
git status --short
```

Expected: full tests and Ruff exit zero; mypy output is clean for new code or
isolates the pre-existing `oauth_config` redeclaration; diff check exits zero.
Do not deploy, run the root block, push, or claim live isolation until an
operator applies the documented one-time migration.

## Plan Self-review

- Spec coverage: Tasks 1-2 cover dynamic root-managed fragments, atomic reload,
  generations, and fail-closed invalidation. Task 3 covers command/env policy,
  metadata, instance correlation, and registry health. Task 4 covers the
  mandatory non-provider and cross-provider filesystem boundary. Task 5
  separates privileged provisioning from runtime. Task 6 covers operator
  migration, diagnostics, docs, and complete repository verification.
- Placeholder scan: no task contains deferred placeholders; each test, command,
  and produced interface is named.
- Type consistency: `CredentialProviderRegistry.snapshot()` returns
  `CredentialRegistrySnapshot` in Tasks 2-5; `CredentialProvider` is the sole
  provider record across parsing, runtime, Landlock, and administration.
