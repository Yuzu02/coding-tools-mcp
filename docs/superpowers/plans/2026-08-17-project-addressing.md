# Project Addressing / Multi-Project Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the Phase 0 extension foundation into one stateless Coding Tools MCP endpoint that serves multiple explicitly registered projects through stable `project_id` addressing, while reusing the existing mother-core filesystem/Git/process implementations and preserving concurrency safety.

**Architecture:** The existing `Runtime` remains the process-wide MCP/protocol/auth/telemetry host. A generic mother-core `WorkspaceRuntimeState` seam makes the existing workspace-bound handlers reusable without copying them: the `projects` extension owns a lazy `ProjectRuntime` per configured project, resolves `project_id`, and invokes the existing bound core handler under a scoped, concurrency-safe workspace-state binding that is always reset after the call. Command ownership is maintained by lifecycle callbacks and a locked `command_id -> project_id` index, so opaque command handles remain stateless and routable without a mutable current project.

**Tech Stack:** Python 3.11+, stdlib `contextvars`, `dataclasses`, `threading`, `pathlib`, existing Phase 0 `ExtensionHost`/`ServiceRegistry`/`ToolDecorator` APIs, existing MCP runtime, `unittest`, Ruff, mypy, Mise/uv, Git.

## Baseline Snapshot

- Implementation branch: `main` by explicit user authorization; do not create a worktree or feature branch for this plan.
- Baseline HEAD: `f5bf954843daefdbcc41a473a08b22f68122cc1f`.
- Original upstream `xyTom/main`: `66b3f194a0252ec1903a84c4e1be4184eb9f4c47`, verified with `git ls-remote` on 2026-08-17.
- Baseline relation: `xyTom/main...main = 0 18`; upstream is an ancestor of fork `main`.
- Phase 0 foundation is green: extension suite 78/78, project/skills regression 16/16, full compliance 128/128, `mise run verify`, schema drift, dispatch-input, Ruff, mypy, integration, npm launcher, public/private config boundary, and bridge compatibility all passed immediately before this plan.
- Current default composed catalog: 22 tools; current `projects` extension contributes `list_skills` and `read_skill` in single-workspace mode.

## Global Constraints

- `project_id` is explicit request addressing, never mutable client/session state.
- Stable project IDs use `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` and are never derived from directory basenames.
- Project configuration lives only below `[extensions.projects]`; do not create a second top-level `[projects]` configuration namespace.
- Public composition/defaults remain in tracked `coding-tools.toml`; real host roots remain in ignored `coding-tools.local.toml`.
- A missing configured root fails startup unless that project explicitly sets `allow_unavailable = true`; an unavailable project stays unavailable until restart rather than mutating the immutable registry later.
- The configured `ProjectRegistry` is immutable after startup.
- Structural project discovery uses `scope_id`; stable configured identity uses `project_id`. Never overload one field for both concepts.
- Do not route by assigning to shared `Runtime.workspace`, `Runtime.command_manager`, patch state, runtime dirs, or any other process-wide “current project” field.
- Do not duplicate the mother-core read/list/search/patch/exec/Git/image implementations inside the `projects` extension.
- Reuse mother-core handlers through a generic workspace-runtime service and a scoped binding whose lifetime is exactly one handler invocation.
- The scoped binding must reset in `finally`, support nesting, and isolate concurrent threads/tasks. A missing binding falls back only to the Runtime bootstrap/default state for legacy core operation, never to a previously selected project.
- Every project-scoped public filesystem/Git/process tool requires `project_id` in its schema.
- `write_stdin`, `kill_command`, and `read_output` remain addressed only by opaque handles.
- `get_command(command_id=...)` needs no `project_id`; `get_command(client_request_id=...)` requires it semantically.
- `list_commands(project_id=...)` filters one project. `list_commands(client_request_id=...)` requires `project_id`. A bare `list_commands` aggregates active/retained command metadata across projects.
- Command ownership mappings must be removed on terminal eviction, TTL pruning, explicit kill eviction, manager close, and project-runtime shutdown; no unbounded stale index is acceptable.
- `check_exec_environment` becomes project-scoped and requires `project_id`.
- `server_info`, `list_projects`, `resolve_project`, and `request_permissions` remain global tools. `request_permissions.arguments.project_id` supplies the target project for project-scoped protected operations.
- Handshake/discovery instructions are project-neutral when `projects` is enabled. Do not expose one bootstrap workspace’s AGENTS/CLAUDE content globally.
- Project-local instruction paths remain available through project-scoped `list_skills` and `read_file`.
- `safe`/`trusted` route command filesystem checks and Landlock roots from the selected project. `dangerous` retains current semantics: permission gates/Landlock are disabled, so Phase A guarantees deterministic routing rather than hostile-tenant isolation.
- Direct path/workdir APIs must reject traversal/symlink escape and must not cross into another separately registered nested project root.
- Do not implement Serena, semantic tools, Work Items, hooks, gateway adapters, external plugins, hot reload, or dynamic project mutation in Phase A.
- Treat Phase A as runtime contract v0.4 in docs/compliance. Do not perform a package/release version bump or publish a release as part of this plan.
- Preserve the explicit upstream-sync boundary: fork-specific behavior stays behind the extension/core bridge and generic workspace-runtime seam.
- Preserve public-fork hygiene. Never commit real host paths, tunnel IDs, service units, credentials, or local deployment state.
- Follow TDD for every behavior change: write the failing test, verify the intended red failure, implement the smallest behavior, verify green, then refactor.
- All commits are local checkpoints. Do not push, merge, tag, force-push, or create a PR unless explicitly requested.

---

## File / Responsibility Map

New/expanded core seams:

```text
coding_tools_mcp/server.py
    Workspace                          add optional excluded registered roots
    WorkspaceCommandManager            generic command ownership callbacks
    WorkspaceRuntimeState              reusable workspace-local mutable state
    CoreWorkspaceRuntimeService        create/bind/close workspace states
    Runtime                            retain global protocol/auth/telemetry + bootstrap state

coding_tools_mcp/extensions/services.py
    WorkspaceRuntimeHandle protocol
    WorkspaceRuntimeService protocol
    CORE_WORKSPACE_RUNTIMES capability
```

Projects extension:

```text
coding_tools_mcp/extensions/projects/
    registry.py                         RegisteredProject + immutable ProjectRegistry
    runtime.py                          ProjectRuntime + ProjectRuntimeManager + CommandOwnershipIndex
    project_catalog.py                  structural scope_id model
    skill_catalog.py                    structural scopes only; no stable identity ownership
    extension.py                        config, services, tools, decorators, routing
    __init__.py                         stable public extension exports
```

Extension-host addition required by multi-project discovery:

```text
coding_tools_mcp/extensions/contributions.py
    ServerInstructionsContribution

coding_tools_mcp/extensions/api.py
    ExtensionContext.add_server_instructions(text, replace_default=False)

coding_tools_mcp/extensions/host.py
    server_instructions(default_text)
```

Primary tests:

```text
tests/extensions/test_workspace_runtime_scoping.py
tests/extensions/test_project_registry.py
tests/extensions/test_project_runtime_manager.py
tests/extensions/test_project_addressing_tools.py
tests/extensions/test_project_tool_routing.py
tests/extensions/test_project_command_routing.py
tests/extensions/test_project_server_context.py
tests/extensions/test_project_addressing_integration.py
tests/extensions/test_upstream_compatibility.py
tests/compliance/test_mcp_contract.py
tests/compliance/test_schema_drift.py
```

Contract/docs:

```text
docs/runtime-contract-v0.4.md
docs/extensions.md
docs/tools-and-schemas.md
docs/quickstart.md
docs/ci-and-tests.md
docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md
```

---

### Task 1: Generic Workspace Runtime State + Concurrency-Safe Scoped Binding

**Purpose:** Make existing bound `Runtime` handlers reusable against more than one workspace without moving/copying their implementations and without mutable process-wide project selection.

**Files:**
- Modify: `coding_tools_mcp/server.py:1195-1668`
- Modify: `coding_tools_mcp/extensions/services.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`
- Create: `tests/extensions/test_workspace_runtime_scoping.py`
- Modify: `tests/extensions/test_extension_services.py`

**Interfaces:**
- Produces `WorkspaceRuntimeHandle` and `WorkspaceRuntimeService` protocols.
- Produces `CORE_WORKSPACE_RUNTIMES = CapabilityKey[WorkspaceRuntimeService]("core.workspace_runtimes")`.
- Produces internal `WorkspaceRuntimeState` and `CoreWorkspaceRuntimeService` in the mother core.
- Produces `CoreWorkspaceRuntimeService.create(root, excluded_roots=(), on_command_registered=None, on_command_removed=None)` and `.invoke(handle, handler, arguments)`.
- `Runtime` continues to expose the same methods; default one-workspace behavior must remain identical before project decorators are introduced.

- [ ] **Step 1: Write red tests for scoped routing, reset, nesting, and concurrency**

Create `tests/extensions/test_workspace_runtime_scoping.py`. Use a synthetic extension to obtain `CORE_WORKSPACE_RUNTIMES` through the real service registry and decorate `read_file` without importing private Runtime state types.

The probe extension should create two workspace handles during `register()` and add this decorator:

```python
class ScopedReadProbe:
    manifest = ExtensionManifest(name="scoped_read_probe")
    roots: dict[str, Path] = {}

    def configure(self, config):
        return None

    def register(self, context):
        service = context.services.require(CORE_WORKSPACE_RUNTIMES)
        handles = {name: service.create(root) for name, root in self.roots.items()}

        def wrap(next_handler):
            def routed(args):
                clean = dict(args)
                target = str(clean.pop("target"))
                return service.invoke(handles[target], next_handler, clean)
            return routed

        context.add_decorator(
            ToolDecorator(
                targets=("read_file",),
                schema_patch=SchemaPatch(
                    properties={"target": {"type": "string", "enum": ["a", "b"]}},
                    required=("target",),
                ),
                wrap_handler=wrap,
            )
        )

    def start(self):
        return None

    def stop(self):
        return None
```

Tests must assert the actual routed calls directly:

```python
self.assertEqual(
    runtime.call_tool("read_file", {"target": "a", "path": "same.txt"})["structuredContent"]["content"],
    "A\n",
)
self.assertEqual(
    runtime.call_tool("read_file", {"target": "b", "path": "same.txt"})["structuredContent"]["content"],
    "B\n",
)
```

Add one decorator path that raises after binding; immediately call a bootstrap/default `read_file` through an undecorated test helper and prove the default state was restored. For nesting, define an outer handler bound to A that invokes this concrete inner handler under B and then reads A again:

```python
def read_identity(args):
    return runtime.read_file({"path": "same.txt"})

def outer_handler(args):
    before = runtime.read_file({"path": "same.txt"})["content"]
    inner = service.invoke(handle_b, read_identity, {})["content"]
    after = runtime.read_file({"path": "same.txt"})["content"]
    return {"before": before, "inner": inner, "after": after}

nested = service.invoke(handle_a, outer_handler, {})
self.assertEqual(nested, {"before": "A\n", "inner": "B\n", "after": "A\n"})
```

For concurrency, run at least 100 alternating A/B calls with `ThreadPoolExecutor(max_workers=16)` and assert every result matches its target. The production change that makes this test pass is per-context state binding rather than assignment to shared Runtime fields.

- [ ] **Step 2: Run the new tests and verify the intended red failure**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_workspace_runtime_scoping -v
```

Expected: import/lookup failure for `CORE_WORKSPACE_RUNTIMES` or inability to create/invoke multiple workspace states. A race-based nondeterministic red is not sufficient; at least one deterministic missing-API assertion must fail.

- [ ] **Step 3: Add generic workspace-runtime protocols to the extension service API**

In `extensions/services.py`, define structural protocols without importing `server.py`:

```python
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

WorkspaceToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
CommandLifecycleCallback = Callable[[str], None]


class WorkspaceRuntimeHandle(Protocol):
    @property
    def root(self) -> Path:
        raise NotImplementedError


class WorkspaceRuntimeService(Protocol):
    def validate_root(self, root: Path, *, require_exists: bool = True) -> Path:
        raise NotImplementedError

    def create(
        self,
        root: Path,
        *,
        excluded_roots: tuple[Path, ...] = (),
        on_command_registered: CommandLifecycleCallback | None = None,
        on_command_removed: CommandLifecycleCallback | None = None,
    ) -> WorkspaceRuntimeHandle:
        raise NotImplementedError

    def invoke(
        self,
        handle: WorkspaceRuntimeHandle,
        handler: WorkspaceToolHandler,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def resolve_existing(
        self,
        handle: WorkspaceRuntimeHandle,
        raw_path: str = ".",
    ) -> ResolvedPathLike:
        raise NotImplementedError

    def close(self, handle: WorkspaceRuntimeHandle) -> None:
        raise NotImplementedError


CORE_WORKSPACE_RUNTIMES = CapabilityKey[WorkspaceRuntimeService]("core.workspace_runtimes")
```

Do not expose `Runtime` itself as a capability.

- [ ] **Step 4: Introduce `WorkspaceRuntimeState` in the mother core**

Create one internal dataclass near `WorkspaceCommandManager`/`Runtime` that owns all workspace-local mutable state currently stored directly on `Runtime`:

```python
@dataclass
class WorkspaceRuntimeState:
    workspace: Workspace
    command_manager: WorkspaceCommandManager
    owns_command_manager: bool
    runtime_dir: Path
    fallback_runtime_dir: Path | None
    home_dir: Path
    tmp_dir: Path
    cache_dir: Path
    runtime_dir_lock: threading.Lock
    runtime_dir_resolved: bool
    patch_baselines: dict[str, str | None]
    patch_lock: threading.Lock
    patch_committer: AtomicPatchCommitter
    @property
    def root(self) -> Path:
        return self.workspace.root

    def set_runtime_dir(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.home_dir = runtime_dir / "home"
        self.tmp_dir = runtime_dir / "tmp"
        self.cache_dir = runtime_dir / "cache"
```

The default state is built from the existing constructor arguments. Do not change permission-mode or shell-env policy ownership; those remain process-wide on `Runtime`.

- [ ] **Step 5: Bind workspace state with an instance-local `ContextVar`**

`Runtime` owns:

```python
self._workspace_state_var: ContextVar[WorkspaceRuntimeState | None] = ContextVar(
    f"coding_tools_workspace_state_{id(self)}",
    default=None,
)
self._default_workspace_state = default_state
```

Add:

```python
def _workspace_state(self) -> WorkspaceRuntimeState:
    return self._workspace_state_var.get() or self._default_workspace_state
```

Convert `workspace`, `command_manager`, `runtime_dir`, `fallback_runtime_dir`, `home_dir`, `tmp_dir`, `cache_dir`, `patch_baselines`, `patch_lock`, `patch_committer`, `commands`, `output_commands`, `commands_lock`, and `starting_commands` access into properties backed by `_workspace_state()`.

`_ensure_runtime_dirs()` must read/write `state.runtime_dir_resolved` and lock `state.runtime_dir_lock`; runtime-dir resolution for one project must never mutate another project’s dirs.

- [ ] **Step 6: Implement `CoreWorkspaceRuntimeService`**

The service is bound to one process-wide `Runtime` and is the only supported way extensions create/bind additional core workspace states:

```python
class CoreWorkspaceRuntimeService:
    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._handles: dict[int, WorkspaceRuntimeState] = {}
        self._closed_handle_ids: set[int] = set()
        self._lock = threading.RLock()

    def validate_root(self, root: Path, *, require_exists: bool = True) -> Path:
        return resolve_workspace_root(root, require_exists=require_exists)

    def _require_handle(self, handle: WorkspaceRuntimeHandle) -> WorkspaceRuntimeState:
        with self._lock:
            state = self._handles.get(id(handle))
        if state is None or state is not handle:
            raise ValueError("workspace runtime handle belongs to another runtime")
        return state

    def create(
        self,
        root: Path,
        *,
        excluded_roots: tuple[Path, ...] = (),
        on_command_registered=None,
        on_command_removed=None,
    ) -> WorkspaceRuntimeState:
        workspace = Workspace(root, excluded_roots=excluded_roots)
        manager = WorkspaceCommandManager(
            workspace.root,
            on_command_registered=on_command_registered,
            on_command_removed=on_command_removed,
        )
        state = self._runtime._new_workspace_runtime_state(
            workspace,
            manager,
            owns_command_manager=True,
        )
        with self._lock:
            self._handles[id(state)] = state
        return state

    def invoke(self, handle, handler, arguments):
        state = self._require_handle(handle)
        token = self._runtime._workspace_state_var.set(state)
        try:
            return handler(arguments)
        finally:
            self._runtime._workspace_state_var.reset(token)

    def resolve_existing(self, handle, raw_path="."):
        return self._require_handle(handle).workspace.resolve_existing(raw_path)

    def close(self, handle) -> None:
        handle_id = id(handle)
        with self._lock:
            state = self._handles.pop(handle_id, None)
            if state is None:
                if handle_id in self._closed_handle_ids:
                    return
                raise ValueError("workspace runtime handle belongs to another runtime")
            self._closed_handle_ids.add(handle_id)
        self._runtime._close_workspace_runtime_state(state)
```

`close()` is idempotent through `WorkspaceCommandManager.close()` and state bookkeeping. Reject handles created by another Runtime service instance.

Add `validate_root()` to the same service. Refactor the existing `Workspace` root normalization/safety checks into one mother-core helper used by both `Workspace.__init__()` and this method. `require_exists=False` may resolve a missing path lexically, but must still reject `/`, the user home directory, a path that exists as a non-directory, and malformed roots. This lets the projects extension validate configuration at startup without creating a command manager.

- [ ] **Step 7: Publish the service before extension registration**

Seed both existing bootstrap workspace access and the new runtime service:

```python
self.workspace_runtime_service = CoreWorkspaceRuntimeService(self)
self.extension_host = ExtensionHost.build(
    registry=self.extension_registry,
    config=self.extension_config,
    core_tools=core_tool_contracts(self),
    seed_services=(
        (CORE_WORKSPACE, self._default_workspace_state.workspace),
        (CORE_WORKSPACE_RUNTIMES, self.workspace_runtime_service),
    ),
)
```

Do not expose project-specific behavior from the core service.

- [ ] **Step 8: Run scoped-binding tests plus existing bridge/runtime tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_workspace_runtime_scoping \
  tests.extensions.test_core_bridge \
  tests.extensions.test_upstream_compatibility \
  tests.compliance.test_runtime_helpers -v
```

Expected: PASS.

- [ ] **Step 9: Run lint/typecheck for the seam**

```bash
uv run --locked --extra dev python -m ruff check \
  coding_tools_mcp/server.py \
  coding_tools_mcp/extensions/services.py \
  tests/extensions/test_workspace_runtime_scoping.py
uv run --locked --extra dev python -m mypy \
  coding_tools_mcp/server.py \
  coding_tools_mcp/extensions
```

Expected: Ruff clean; mypy reports no issues.

- [ ] **Step 10: Commit Task 1**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/extensions tests/extensions
git commit -m "refactor: add scoped workspace runtime states"
```

---

### Task 2: Registered-Project Boundaries in `Workspace`

**Purpose:** Let a parent registered project remain path-confined without silently traversing into a separately registered nested project.

**Files:**
- Modify: `coding_tools_mcp/server.py` (`Workspace`)
- Modify: `tests/extensions/test_workspace_runtime_scoping.py`
- Create: `tests/extensions/test_project_workspace_boundaries.py`

**Interfaces:**
- `Workspace(root, excluded_roots=())`.
- `WorkspaceRuntimeService.create(..., excluded_roots=...)` from Task 1 remains the only extension-facing constructor.
- No public MCP schema changes yet.

- [ ] **Step 1: Write red tests for nested registered-root exclusion**

Create a parent root containing `child/` and ordinary `src/`. Build a workspace state for parent with `excluded_roots=(child.resolve(),)`.

Assert:

```python
with self.assertRaisesRegex(ToolFailure, "separately registered project"):
    service.invoke(parent_handle, runtime.read_file, {"path": "child/secret.txt"})

self.assertEqual(
    service.invoke(parent_handle, runtime.read_file, {"path": "src/ok.txt"})["content"],
    "ok\n",
)
```

Also assert:
- `resolve_for_write("child/new.txt")` is denied;
- `list_dir(path=".", recursive=True)` does not enumerate files inside child;
- `list_files(path=".")` does not enumerate files inside child;
- `search_text(path=".")` never reads/results from child;
- a symlink from parent into child is rejected;
- creating a state rooted directly at child still accesses child normally.

- [ ] **Step 2: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_workspace_boundaries -v
```

Expected: failure because `Workspace` does not yet understand excluded registered roots.

- [ ] **Step 3: Normalize and store exclusions at construction**

`Workspace.__init__` must canonicalize every exclusion with `resolve(strict=True)`, require each exclusion to be strictly inside `self.root`, deduplicate them, and sort deepest-first:

```python
self.excluded_roots = tuple(
    sorted(unique_exclusions, key=lambda path: len(path.parts), reverse=True)
)
```

Reject an exclusion equal to the workspace root as invalid configuration.

- [ ] **Step 4: Centralize project-boundary rejection**

Add:

```python
def _is_excluded(self, path: Path) -> bool:
    return any(is_relative_to(path, excluded) for excluded in self.excluded_roots)

def _require_allowed(self, path: Path) -> Path:
    if self._is_excluded(path):
        raise ToolFailure(
            "PATH_OUTSIDE_WORKSPACE",
            "Path enters a separately registered project.",
            category="security",
        )
    return path
```

Use it after canonicalization in `resolve_existing`, `resolve_for_write`, `_validate_base`, and `is_safe_existing_path`. `is_ignored_path` returns `True` for excluded paths so recursive walkers prune them before reading contents.

- [ ] **Step 5: Run workspace/security regressions**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_workspace_boundaries \
  tests.compliance.test_security \
  tests.compliance.test_tool_golden -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add coding_tools_mcp/server.py tests/extensions
git commit -m "feat: isolate nested registered project roots"
```

---

### Task 3: Stable `ProjectRegistry` + Structural `scope_id` Migration

**Purpose:** Establish immutable configured identity and remove the current ambiguity where structural display paths are named `project_id`.

**Files:**
- Create: `coding_tools_mcp/extensions/projects/registry.py`
- Modify: `coding_tools_mcp/extensions/projects/project_catalog.py`
- Modify: `coding_tools_mcp/extensions/projects/skill_catalog.py`
- Modify: `coding_tools_mcp/extensions/projects/__init__.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Create: `tests/extensions/test_project_registry.py`
- Modify: `tests/test_project_catalog.py`
- Modify: `tests/test_project_skills_runtime.py`
- Modify: `tests/extensions/test_projects_extension.py`

**Interfaces:**
- Produces `RegisteredProject`, `ProjectRegistry`, `ProjectRegistryError`, `PROJECT_ID_RE`.
- Structural `ProjectRecord.project_id` becomes `scope_id`; `parent_project_id` becomes `parent_scope_id`.
- Config schema becomes `[extensions.projects.registry.<project_id>]` with `root` and optional `allow_unavailable`.
- Empty registry config synthesizes one legacy project: `project_id="default"`, root = bootstrap `CORE_WORKSPACE.root`.

- [ ] **Step 1: Write red registry tests**

`tests/extensions/test_project_registry.py` must cover these exact behaviors:

```python
class ProjectRegistryTests(unittest.TestCase):
    def test_ids_are_stable_and_independent_of_root_basename(self) -> None:
        settings = {
            "registry": {
                "frontend": {"root": str(self.root / "same-name-a")},
                "api": {"root": str(self.root / "same-name-b")},
            }
        }
        registry = build_project_registry(settings, fallback_root=self.root)
        self.assertEqual(registry.ids(), ("frontend", "api"))

    def test_invalid_project_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "invalid project_id"):
            build_project_registry(
                {"registry": {"bad id": {"root": str(self.root / "project")}}},
                fallback_root=self.root,
            )

    def test_duplicate_canonical_roots_are_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "same canonical root"):
            build_project_registry(
                {
                    "registry": {
                        "a": {"root": str(self.project)},
                        "b": {"root": str(self.project)},
                    }
                },
                fallback_root=self.root,
            )

    def test_missing_root_requires_explicit_allow_unavailable(self) -> None:
        with self.assertRaisesRegex(ConfigError, "does not exist"):
            build_project_registry(
                {"registry": {"missing": {"root": str(self.root / "missing")}}},
                fallback_root=self.root,
            )

    def test_allow_unavailable_freezes_project_as_unavailable_until_restart(self) -> None:
        registry = build_project_registry(
            {
                "registry": {
                    "missing": {
                        "root": str(self.root / "missing"),
                        "allow_unavailable": True,
                    }
                }
            },
            fallback_root=self.root,
        )
        project = registry.get("missing")
        self.assertFalse(project.available)
        self.assertTrue(project.warnings)
```

Also test unknown ID, nested roots, longest-root absolute resolution, path outside every configured root, nonexistent absolute path, and symlink escape.

- [ ] **Step 2: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_registry -v
```

Expected: import failure for `projects.registry`.

- [ ] **Step 3: Rename structural IDs before introducing configured IDs**

Change `ProjectRecord` to:

```python
@dataclass(frozen=True)
class ProjectRecord:
    scope_id: str
    root: Path
    display_root: str
    markers: tuple[str, ...]
    kind: Literal["main", "subproject"]
    parent_scope_id: str | None
```

Its summary uses:

```python
{
    "scope_id": self.scope_id,
    "root": self.display_root,
    "markers": list(self.markers),
    "kind": self.kind,
    "parent_scope_id": self.parent_scope_id,
}
```

Update `SkillCatalog` to read `scope.scope_id`. Preserve existing `EffectiveSkillContext.main_project`, `subprojects`, and `SkillRecord.owner_project` response field names for Phase A compatibility, but their values now explicitly contain structural scope IDs. Add comments that these payload fields predate stable registered `project_id` and are structural metadata.

- [ ] **Step 4: Run structural catalog/skills regressions immediately after rename**

```bash
uv run --locked --extra dev python -m unittest \
  tests.test_project_catalog \
  tests.test_project_skills_runtime \
  tests.test_project_skills_integration -v
```

Expected: PASS after updating expected summary keys where they directly inspect `ProjectRecord.summary()`.

- [ ] **Step 5: Implement immutable registered-project records**

Create `registry.py`:

```python
PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class RegisteredProject:
    project_id: str
    root: Path
    markers: tuple[str, ...]
    available: bool
    warnings: tuple[str, ...] = ()

    def summary(self, *, expose_root: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.project_id,
            "markers": list(self.markers),
            "available": self.available,
            "warnings": list(self.warnings),
        }
        if expose_root:
            payload["root"] = str(self.root)
        return payload


class ProjectRegistryError(LookupError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
```

`ProjectRegistry` stores an insertion-ordered tuple and ID mapping. `build_project_registry()` receives the root validator from `CORE_WORKSPACE_RUNTIMES`; do not duplicate core unsafe-root rules inside the extension. It must expose:

```python
def ids(self) -> tuple[str, ...]
def projects(self) -> tuple[RegisteredProject, ...]
def get(self, project_id: str) -> RegisteredProject
def require_available(self, project_id: str) -> RegisteredProject
def resolve_absolute(self, path: Path | str) -> tuple[RegisteredProject, Path]
def excluded_roots_for(self, project_id: str) -> tuple[Path, ...]
```

`resolve_absolute` requires an absolute input, resolves it strictly, chooses the deepest containing configured root, and returns `(project, resolved_path)`. If the lexical input lies under a configured root but canonicalization escapes through a symlink, raise `ProjectRegistryError("INVALID_PROJECT_PATH", "Project path escapes the registered root through a symlink.")`; if no project contains it, use `PROJECT_NOT_FOUND`.

- [ ] **Step 6: Implement config parsing under the extension namespace**

Change `ProjectsExtension.manifest.config_schema` to:

```python
config_schema=table(
    {
        "registry": map_of(
            table(
                {
                    "root": scalar(str),
                    "allow_unavailable": scalar(bool),
                }
            )
        )
    }
)
```

`ProjectsExtension.configure()` stores a normalized immutable copy of the settings but does not resolve roots yet because `CORE_WORKSPACE` is only available during registration. `register()` builds the registry using the configured entries or the `default` fallback.

Each explicit registry record must contain `root`; missing root is a `ConfigError`. `allow_unavailable` defaults to `False`.

Use a public helper from `project_catalog.py`:

```python
def project_markers(path: Path) -> tuple[str, ...]:
    return tuple(marker for marker in PROJECT_MARKERS if (path / marker).exists())
```

Do not build a complete `ProjectCatalog` merely to obtain top-level markers. `build_project_registry(settings, fallback_root, validate_root=workspace_runtimes.validate_root)` uses `require_exists=not allow_unavailable` for each explicit entry.

- [ ] **Step 7: Run registry/config/structural tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_registry \
  tests.extensions.test_projects_extension \
  tests.test_project_catalog \
  tests.test_project_skills_runtime \
  tests.test_project_skills_integration -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add coding_tools_mcp/extensions/projects tests
git commit -m "feat: add stable project registry"
```

---

### Task 4: Lazy `ProjectRuntimeManager` + Project-Local Catalogs/Context

**Purpose:** Give every registered project its own workspace state, command manager, structural catalog, skills, patch state, runtime dirs, and project context without giving it MCP transport/auth/global ExtensionHost ownership.

**Files:**
- Create: `coding_tools_mcp/extensions/projects/runtime.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/extensions/projects/__init__.py`
- Create: `tests/extensions/test_project_runtime_manager.py`

**Interfaces:**
- Produces `ProjectRuntime`, `ProjectRuntimeManager`, `CommandOwnershipIndex`.
- Produces capabilities `PROJECT_REGISTRY` and `PROJECT_RUNTIMES`.
- Consumes `CORE_WORKSPACE_RUNTIMES` and the immutable `ProjectRegistry`.

- [ ] **Step 1: Write red lazy-runtime tests**

Tests must assert:
- constructing the manager creates zero workspace handles;
- first `require("a")` creates exactly one handle;
- repeated `require("a")` returns the same `ProjectRuntime`;
- concurrent `require("a")` calls create one handle only;
- `require("b")` gets a distinct workspace/command/patch state;
- unavailable project returns typed `PROJECT_UNAVAILABLE` without creating a handle;
- unknown project returns typed `PROJECT_NOT_FOUND`;
- closing the manager closes each created handle exactly once;
- a parent project handle receives nested registered roots from `registry.excluded_roots_for(parent_id)`.

- [ ] **Step 2: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_runtime_manager -v
```

Expected: import failure for `projects.runtime`.

- [ ] **Step 3: Implement command ownership as a locked independent object**

Create:

```python
class CommandOwnershipIndex:
    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, project_id: str, command_id: str) -> None:
        with self._lock:
            existing = self._owners.get(command_id)
            if existing is not None and existing != project_id:
                raise RuntimeError(f"command ownership collision: {command_id}")
            self._owners[command_id] = project_id

    def remove(self, project_id: str, command_id: str) -> None:
        with self._lock:
            if self._owners.get(command_id) == project_id:
                self._owners.pop(command_id, None)

    def owner(self, command_id: str) -> str:
        with self._lock:
            owner = self._owners.get(command_id)
        if owner is None:
            raise ToolFailure(
                "COMMAND_NOT_FOUND",
                "Command is not retained by any configured project.",
                category="not_found",
            )
        return owner
```

The core command lifecycle wiring is added in Task 7. The index is created now so each project handle can receive stable callbacks at creation time.

- [ ] **Step 4: Implement `ProjectRuntime` and lazy manager**

Use:

```python
@dataclass(frozen=True)
class ProjectRuntime:
    project: RegisteredProject
    workspace: WorkspaceRuntimeHandle
    catalog: ProjectCatalog
    skills: SkillCatalog
    project_context: ProjectContext


class ProjectRuntimeManager:
    def __init__(
        self,
        registry: ProjectRegistry,
        workspace_runtimes: WorkspaceRuntimeService,
    ) -> None:
        self.registry = registry
        self.workspace_runtimes = workspace_runtimes
        self.command_owners = CommandOwnershipIndex()
        self._runtimes: dict[str, ProjectRuntime] = {}
        self._lock = threading.RLock()
        self._closed = False
```

`require(project_id)` first validates registry availability, then creates under the lock:

```python
handle = self.workspace_runtimes.create(
    project.root,
    excluded_roots=self.registry.excluded_roots_for(project_id),
    on_command_registered=lambda command_id: self.command_owners.register(project_id, command_id),
    on_command_removed=lambda command_id: self.command_owners.remove(project_id, command_id),
)
catalog = build_project_catalog(project.root)
runtime = ProjectRuntime(
    project=project,
    workspace=handle,
    catalog=catalog,
    skills=SkillCatalog(catalog),
    project_context=load_project_context(project.root),
)
```

Build `ProjectCatalog` once and reuse the same object for `SkillCatalog`; do not call `build_project_catalog` twice in production code.

Add:

```python
def invoke(self, project_id, handler, args):
    runtime = self.require(project_id)
    return self.workspace_runtimes.invoke(runtime.workspace, handler, args)

def active(self) -> tuple[ProjectRuntime, ...]:
    with self._lock:
        return tuple(self._runtimes.values())

def active_for(self, project_id: str) -> ProjectRuntime | None:
    with self._lock:
        return self._runtimes.get(project_id)

def resolve_existing(self, project_id: str, raw_path: str = ".") -> ResolvedPathLike:
    runtime = self.require(project_id)
    return self.workspace_runtimes.resolve_existing(runtime.workspace, raw_path)
```

`close()` marks the manager closed, snapshots runtimes, clears the map, and closes every workspace handle even if one close fails; return bounded warning strings for failures.

- [ ] **Step 5: Publish registry/runtime capabilities from `ProjectsExtension.register()`**

Add keys in `runtime.py` or `extension.py`:

```python
PROJECT_REGISTRY = CapabilityKey[ProjectRegistry]("projects.registry")
PROJECT_RUNTIMES = CapabilityKey[ProjectRuntimeManager]("projects.runtimes")
```

Registration order:

```text
require CORE_WORKSPACE
require CORE_WORKSPACE_RUNTIMES
build ProjectRegistry
build ProjectRuntimeManager
provide PROJECT_REGISTRY
provide PROJECT_RUNTIMES
register tools/decorators
```

`ProjectsExtension.stop()` closes the `ProjectRuntimeManager` and is idempotent.

- [ ] **Step 6: Run runtime-manager and lifecycle tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_runtime_manager \
  tests.extensions.test_extension_lifecycle \
  tests.extensions.test_projects_extension -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add coding_tools_mcp/extensions/projects tests/extensions
git commit -m "feat: add lazy project runtimes"
```

---

### Task 5: Public Project Discovery + Project-Scoped Skills

**Purpose:** Give clients stable discovery/addressing primitives and migrate the two existing projects-extension tools from implicit single-workspace behavior to explicit `project_id`.

**Files:**
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/extensions/projects/runtime.py`
- Create: `tests/extensions/test_project_addressing_tools.py`
- Modify: `tests/test_project_skills_runtime.py`
- Modify: `tests/test_project_skills_integration.py`
- Modify: `tests/extensions/test_projects_extension.py`

**Interfaces:**
- Adds global tools `list_projects` and `resolve_project`.
- `list_skills` and `read_skill` now require `project_id`.
- Default fallback registry exposes one `default` project.
- Default composed catalog becomes 24 tools; disabling `projects` remains 20.

- [ ] **Step 1: Write red discovery contract tests**

Build a Runtime with explicit settings equivalent to the following two-project map, substituting the two temporary roots created by the test:

```python
settings = {
    "projects": {
        "registry": {
            "alpha": {"root": str(alpha)},
            "beta": {"root": str(beta)},
        }
    }
}
config = RuntimeConfig.defaults(enabled=("projects",), settings=settings)
```

Assert:

```python
listed = runtime.call_tool("list_projects", {})["structuredContent"]
self.assertEqual([item["id"] for item in listed["projects"]], ["alpha", "beta"])
self.assertEqual(listed["project_count"], 2)

resolved = runtime.call_tool(
    "resolve_project",
    {"path": str(alpha / "src" / "module.py")},
)["structuredContent"]
self.assertEqual(resolved["project_id"], "alpha")
self.assertEqual(resolved["relative_path"], "src/module.py")
```

Add nested structural markers and assert `scope_chain` contains `scope_root`, `kind`, and `markers`, never a second stable `project_id`.

Error assertions:
- relative path to `resolve_project` -> `INVALID_PROJECT_PATH`;
- outside path -> `PROJECT_NOT_FOUND`;
- unavailable record -> `PROJECT_UNAVAILABLE`;
- symlink path escaping registered roots -> `INVALID_PROJECT_PATH`.

- [ ] **Step 2: Write red skill-schema/routing tests**

Assert `tools/list` schemas for `list_skills` and `read_skill` both contain required `project_id`. Put identically named skill trees in alpha/beta with different body text and prove:

```python
alpha_skill = runtime.call_tool(
    "read_skill",
    {"project_id": "alpha", "workdir": ".", "skill": "shared"},
)
beta_skill = runtime.call_tool(
    "read_skill",
    {"project_id": "beta", "workdir": ".", "skill": "shared"},
)
self.assertIn("ALPHA BODY", tool_text(alpha_skill))
self.assertIn("BETA BODY", tool_text(beta_skill))
```

Call without project_id and verify JSON-RPC invalid arguments before handler execution.

- [ ] **Step 3: Verify red state**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_addressing_tools \
  tests.test_project_skills_runtime -v
```

Expected: missing tools / missing required `project_id` failures.

- [ ] **Step 4: Contribute `list_projects`**

Schema:

```python
{
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
```

Handler returns:

```python
{
    "ok": True,
    "projects": [project.summary(expose_root=True) for project in registry.projects()],
    "project_count": len(registry.projects()),
    "warnings": [],
}
```

Annotations: read-only + idempotent. Do not instantiate project runtimes merely to list registry records.

- [ ] **Step 5: Contribute `resolve_project`**

Schema requires one absolute `path` string. Handler:

1. validates absolute syntax before filesystem access;
2. calls `registry.resolve_absolute(path)`;
3. obtains selected `ProjectRuntime` only for available project;
4. computes relative path from `project.root`;
5. calls structural `runtime.catalog.resolve(resolved_path)`;
6. returns scope-chain structural metadata.

Normalize registry errors to `ToolFailure` with the same code and `category="not_found"` for `PROJECT_NOT_FOUND`/`PROJECT_UNAVAILABLE`, otherwise `category="validation"` for `INVALID_PROJECT_PATH`.

- [ ] **Step 6: Make skill tools explicitly project-scoped**

Add required schema property:

```python
"project_id": {"type": "string", "minLength": 1, "maxLength": 128}
```

`list_skills` / `read_skill` resolve `ProjectRuntimeManager.require(project_id)` and validate `workdir` through `ProjectRuntimeManager.resolve_existing(project_id, raw_workdir)` before calling that runtime’s `SkillCatalog`. Preserve the current `NOT_A_DIRECTORY` behavior for file workdirs; do not reimplement core path/symlink rules with raw `Path.resolve()` in the extension. Do not keep extension-global `_workspace` or `_skills` fields after this migration.

The returned skill payload remains structural and may include `main_project`/`owner_project` historical field names; also add top-level `project_id` so stable addressing is unambiguous.

- [ ] **Step 7: Verify catalog counts and default fallback**

Assertions:

```python
self.assertEqual(len(default_runtime.exposed_tool_names()), 24)
self.assertIn("list_projects", default_runtime.exposed_tool_names())
self.assertIn("resolve_project", default_runtime.exposed_tool_names())
self.assertEqual(
    default_runtime.call_tool("list_projects", {})["structuredContent"]["projects"][0]["id"],
    "default",
)
self.assertEqual(len(disabled_runtime.exposed_tool_names()), 20)
```

- [ ] **Step 8: Run project discovery/skills tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_addressing_tools \
  tests.extensions.test_projects_extension \
  tests.test_project_catalog \
  tests.test_project_skills_runtime \
  tests.test_project_skills_integration -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 5**

```bash
git add coding_tools_mcp/extensions/projects tests
git commit -m "feat: add explicit project discovery"
```

---

### Task 6: Project-Scoped Core Tool Decorators

**Purpose:** Require `project_id` on every filesystem/Git/exec/image/environment operation and route the unchanged mother-core handler through the selected project runtime.

**Files:**
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/extensions/projects/runtime.py`
- Create: `tests/extensions/test_project_tool_routing.py`
- Modify: `tests/extensions/test_core_bridge.py`
- Modify: `tests/compliance/test_schema_drift.py` only for composed-schema expectations that intentionally change

**Interfaces:**
- Defines `PROJECT_SCOPED_CORE_TOOLS` exactly as:

```python
PROJECT_SCOPED_CORE_TOOLS = (
    "check_exec_environment",
    "read_file",
    "list_dir",
    "list_files",
    "search_text",
    "apply_patch",
    "exec_command",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_blame",
    "view_image",
)
```

- Each schema gains required `project_id` through `ToolDecorator`.
- Wrapper strips `project_id` before invoking existing core handler under `ProjectRuntimeManager.invoke()`.
- Project-addressed continuation actions regain `project_id` before returning to the client.

- [ ] **Step 1: Write a table-driven red schema test for every scoped tool**

```python
for name in PROJECT_SCOPED_CORE_TOOLS:
    with self.subTest(name=name):
        schema = tools[name]["inputSchema"]
        self.assertIn("project_id", schema["properties"])
        self.assertIn("project_id", schema["required"])
```

Also assert global/opaque tools do not accidentally require it:

```python
for name in ("server_info", "list_projects", "resolve_project", "request_permissions", "write_stdin", "kill_command", "read_output"):
    self.assertNotIn("project_id", tools[name]["inputSchema"].get("required", []))
```

- [ ] **Step 2: Write red A/B routing tests using identical relative paths**

Create alpha/beta each with:

```text
same.txt
repo/.git
src/value.txt
```

Assert `read_file`, `list_files`, `search_text`, `git_status`, and `check_exec_environment` return data/root from the chosen project only. For `apply_patch`, patch `same.txt` in alpha and prove beta is unchanged.

For `exec_command`, run a platform-neutral command that prints the process cwd and assert it is inside the selected project. In `safe`/`trusted`, explicitly passing a sibling project path in `workdir`/command path must fail. In `dangerous`, assert selected default cwd is still the chosen project but do not assert hostile-tenant Landlock isolation.

- [ ] **Step 3: Write red continuation-addressing tests**

Force a truncated `read_file`, paged `git_log`, and paged `git_blame`. For every `next_action` whose `tool` is project-scoped, assert:

```python
self.assertEqual(next_action["arguments"]["project_id"], "alpha")
```

Opaque command actions such as `write_stdin` must not receive `project_id`.

- [ ] **Step 4: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_tool_routing -v
```

Expected: schema/routing failures because current core tools remain single-workspace.

- [ ] **Step 5: Register one deterministic project-routing decorator**

Use one `ToolDecorator` targeting `PROJECT_SCOPED_CORE_TOOLS`:

```python
ToolDecorator(
    targets=PROJECT_SCOPED_CORE_TOOLS,
    schema_patch=SchemaPatch(
        properties={
            "project_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": PROJECT_ID_RE.pattern,
            }
        },
        required=("project_id",),
    ),
    wrap_handler=self._wrap_project_scoped_core_handler,
)
```

Wrapper:

```python
def wrap(next_handler):
    def routed(args):
        project_id = str(args["project_id"])
        clean = dict(args)
        clean.pop("project_id", None)
        payload = runtimes.invoke(project_id, next_handler, clean)
        return _restore_project_addressing(payload, project_id)
    return routed
```

`_restore_project_addressing()` deep-copies only action dictionaries it modifies, adds `project_id` to `next_action.arguments` / every `next_actions[*].arguments` when the referenced tool is in the complete public project-scoped set, and adds top-level `project_id` to the current payload. It must not add project_id to `write_stdin`, `kill_command`, or `read_output` actions.

- [ ] **Step 6: Verify project boundary behavior across direct APIs**

Add an explicitly nested registered project fixture. A parent `read_file/list_files/search_text/apply_patch/git_*` request into the child path must return `PATH_OUTSIDE_WORKSPACE`; selecting the child `project_id` must succeed.

- [ ] **Step 7: Run focused routing/security tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_tool_routing \
  tests.extensions.test_project_workspace_boundaries \
  tests.compliance.test_security \
  tests.test_git_workdir_resolution -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 6**

```bash
git add coding_tools_mcp/extensions/projects tests
git commit -m "feat: route core tools by project id"
```

---

### Task 7: Command Ownership Lifecycle + Opaque Handle Routing

**Purpose:** Keep commands stateless across reconnects while routing each opaque command/output handle back to the correct project runtime and preventing stale ownership records.

**Files:**
- Modify: `coding_tools_mcp/server.py` command registration/eviction lifecycle
- Modify: `coding_tools_mcp/extensions/projects/runtime.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Create: `tests/extensions/test_project_command_routing.py`
- Modify: `tests/test_reliable_command_recovery_http.py`

**Interfaces:**
- `WorkspaceCommandManager` callbacks receive command registration/removal exactly once per retained command lifetime.
- `CommandOwnershipIndex` routes `command_id` and command IDs parsed from `output_ref`.
- `list_commands` gains optional `project_id`.
- `get_command` gains optional `project_id`, required only for its `client_request_id` addressing mode.
- Opaque `write_stdin`, `kill_command`, `read_output` schemas remain unchanged.

- [ ] **Step 1: Write red ownership-lifecycle unit tests**

Use callbacks that append command IDs to `registered` / `removed`. Assert:

1. successful `exec_command` registers once;
2. transition active -> retained output does **not** remove ownership;
3. explicit kill with eviction removes once;
4. retained-output capacity eviction removes once;
5. completed-command TTL pruning removes once;
6. workspace manager close removes every remaining command exactly once;
7. repeated cleanup paths are idempotent.

Patch time/constants inside tests rather than sleeping for the production TTL.

- [ ] **Step 2: Write red cross-project command routing tests**

Run long-lived commands in alpha/beta concurrently. Assert:

```python
alpha_id = alpha_start["command_id"]
beta_id = beta_start["command_id"]

alpha_poll = runtime.call_tool("write_stdin", {"command_id": alpha_id, "chars": "alpha\n"})
beta_poll = runtime.call_tool("write_stdin", {"command_id": beta_id, "chars": "beta\n"})
self.assertIn("alpha", tool_text(alpha_poll))
self.assertIn("beta", tool_text(beta_poll))
```

Neither opaque call includes `project_id`.

Also force retained output in both projects and prove `read_output(output_ref)` routes correctly after command completion.

- [ ] **Step 3: Write red client-request/idempotency tests**

Use the same `client_request_id="same-id"` in alpha and beta with different commands; both must start independently. Reuse `same-id` with a different fingerprint inside alpha and preserve `IDEMPOTENCY_CONFLICT`.

`get_command(client_request_id="same-id")` without project_id must fail `INVALID_ARGUMENT`; with `project_id="alpha"` and `"beta"` it must recover the corresponding command. `get_command(command_id=alpha_id)` must succeed without project_id.

`list_commands(client_request_id="same-id")` without project_id must also fail. `list_commands(project_id="alpha")` must contain only alpha metadata. Bare `list_commands({})` must aggregate both and annotate every item with its `project_id`.

- [ ] **Step 4: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_command_routing -v
```

Expected: opaque handles look only in the bootstrap/default command manager or ownership callbacks are absent.

- [ ] **Step 5: Wire generic command lifecycle callbacks in the mother core**

Do not import the projects extension into `server.py`. The callbacks belong on `WorkspaceCommandManager`, because that object owns command retention and close semantics. Add:

```python
def notify_command_registered(self, command_id: str) -> None:
    if self.on_command_registered is not None:
        self.on_command_registered(command_id)

def notify_command_removed(self, command_id: str) -> None:
    if self.on_command_removed is not None:
        self.on_command_removed(command_id)
```

Call registration only after the command is inserted successfully into `commands`. Do not remove when `_complete_command()` merely transfers it to retained output.

Call removal at all true ownership-loss sites:

```text
_evict_retained_locked capacity eviction
_prune_commands TTL eviction
kill_command when evict=True
WorkspaceCommandManager.close for union(active, retained)
any explicit retained-record deletion introduced later
```

Keep client-request binding cleanup coupled to the same removal paths.

- [ ] **Step 6: Add opaque routing decorators in `ProjectsExtension`**

Register separate wrappers because addressing shapes differ:

```text
write_stdin / kill_command -> args.command_id
read_output                -> command:<id>:<stream>
get_command(command_id)    -> args.command_id
get_command(client_request_id) -> required args.project_id
list_commands(project_id)  -> selected runtime
list_commands()            -> aggregate active runtimes
```

Add optional `project_id` schema patches only to `get_command` and `list_commands`. Do not add it to opaque-only tools.

For a command-id call with an optional supplied project_id, verify it matches the ownership index rather than silently trusting the redundant field. For `get_command(client_request_id=...)` and `list_commands(client_request_id=...)`, use `active_for(project_id)` first: if no runtime has ever existed for that project there cannot be a retained client-request binding, so return `COMMAND_NOT_FOUND` without allocating a new command manager merely for recovery lookup.

- [ ] **Step 7: Aggregate bare `list_commands` deterministically**

Do not create project runtimes merely to list zero commands. Iterate `ProjectRuntimeManager.active()` only, invoke the core `list_commands` handler in each state with `limit=100` and no project filter, attach project_id to every item, merge/sort by `started_at` descending, then apply the caller’s global `status`/`limit`. `total` is the aggregate pre-limit count.

If no project runtime is active, return the existing empty shape with `pending=False`.

- [ ] **Step 8: Add project ownership to returned command metadata**

Every command result produced through project-routed `exec_command`, `get_command`, `list_commands`, `write_stdin`, `kill_command`, and `read_output` should include `project_id` when useful for diagnostics. Do not change opaque handle formats.

- [ ] **Step 9: Run command recovery/concurrency regressions**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_command_routing \
  tests.test_reliable_command_recovery_http \
  tests.compliance.test_runtime_semantics \
  tests.compliance.test_e2e -v
```

Expected: PASS.

- [ ] **Step 10: Commit Task 7**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/extensions/projects tests
git commit -m "feat: route command handles across projects"
```

---

### Task 8: Project-Neutral Handshake, Global Server Info, and Permission Targeting

**Purpose:** Remove the remaining implicit bootstrap-workspace leakage from global discovery while preserving project-local instructions and correct permission diagnostics.

**Files:**
- Modify: `coding_tools_mcp/extensions/contributions.py`
- Modify: `coding_tools_mcp/extensions/api.py`
- Modify: `coding_tools_mcp/extensions/host.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`
- Modify: `coding_tools_mcp/extensions/projects/extension.py`
- Modify: `coding_tools_mcp/server.py` (`initialize_result`, `discover_payload`)
- Create: `tests/extensions/test_project_server_context.py`
- Modify: `tests/extensions/test_extension_lifecycle.py`
- Modify: `tests/extensions/test_tool_contributions.py`

**Interfaces:**
- Adds explicit `ServerInstructionsContribution` to Phase 0 contribution API.
- Adds `ExtensionHost.server_instructions(default_text)`.
- Projects extension replaces bootstrap project instructions with project-neutral addressing instructions.
- `server_info` remains global and no longer presents one bootstrap workspace as the endpoint’s active project.
- `request_permissions` remains global but binds to `arguments.project_id` for `exec_command`/`apply_patch` targets.

- [ ] **Step 1: Write red server-instruction contribution tests**

Contribution semantics:
- zero replacements -> return mother-core default instructions;
- one `replace_default=True` contribution -> replacement becomes base;
- append contributions are joined deterministically in extension order;
- more than one replacement -> startup `ContributionError`;
- contributions freeze with the registry.

Use an immutable record:

```python
@dataclass(frozen=True)
class ServerInstructionsContribution:
    text: str
    replace_default: bool = False
```

- [ ] **Step 2: Write red multi-project handshake/discovery leakage tests**

Put a unique secret-looking marker string in bootstrap `AGENTS.md` and different markers in alpha/beta AGENTS files. Initialize/discover the multi-project Runtime and assert none of those project-specific strings appears in global `instructions`.

Assert the instructions contain all of:

```text
project_id
list_projects
project-scoped
```

Then call `list_skills(project_id="alpha")` and verify alpha’s instruction file path remains discoverable.

- [ ] **Step 3: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_project_server_context -v
```

Expected: bootstrap `ProjectContext.server_instructions()` is still emitted globally.

- [ ] **Step 4: Implement server instruction contributions**

Add registration/snapshot/freeze support in `ContributionRegistry`, a bounded `ExtensionContext.add_server_instructions(text, replace_default=False)`, and:

```python
def server_instructions(self, default_text: str) -> str:
    contributions = self._contributions.instruction_entries()
    replacements = [item for _owner, item in contributions if item.replace_default]
    if len(replacements) > 1:
        raise ContributionError("multiple extensions replace default server instructions")
    base = replacements[0].text if replacements else default_text
    appended = [item.text for _owner, item in contributions if not item.replace_default]
    return "\n\n".join(part for part in (base, *appended) if part.strip())
```

Validate replacement conflicts during host build before registries freeze/start.

- [ ] **Step 5: Route handshake/discovery through `ExtensionHost`**

Replace direct calls to `self.project_context.server_instructions()` with:

```python
self.extension_host.server_instructions(self.project_context.server_instructions())
```

When `projects` is disabled, the output remains byte-for-byte equivalent to current core behavior.

- [ ] **Step 6: Projects extension contributes one project-neutral replacement**

Use concise text equivalent to:

```text
This endpoint serves multiple explicitly registered projects. Call list_projects to discover stable IDs. Every project-scoped filesystem, Git, process, image, environment, or skill request must include project_id; paths/workdirs remain relative to that selected project. No previous request selects a current project. Read project-scoped instruction files returned by list_skills/read_file before modifying that scope.
```

Do not include configured project roots or local config values.

- [ ] **Step 7: Decorate global `server_info` into a multi-project view**

The wrapper invokes the existing core handler, then removes bootstrap-specific fields:

```text
workspace
runtime_dir
home
tmpdir
cache_dir
project_context
```

Add:

```python
"projects": {
    "count": len(registry.ids()),
    "ids": list(registry.ids()),
    "available": sum(project.available for project in registry.projects()),
}
```

Keep process-wide permission/auth/protocol/output-retention fields unchanged. Extension metadata under `extensions` remains available.

- [ ] **Step 8: Route `request_permissions` by nested target arguments**

For `tool_name` in `{"exec_command", "apply_patch"}`:

```python
target = args.get("arguments")
if not isinstance(target, dict):
    raise ToolFailure("INVALID_ARGUMENT", "arguments must be an object.", category="validation")
project_id = target.get("project_id")
if not isinstance(project_id, str) or not project_id:
    raise ToolFailure(
        "INVALID_ARGUMENT",
        "arguments.project_id is required for project-scoped permission requests.",
        category="validation",
    )
clean_outer = dict(args)
clean_target = dict(target)
clean_target.pop("project_id", None)
clean_outer["arguments"] = clean_target
return runtimes.invoke(project_id, next_handler, clean_outer)
```

After invocation, restore `project_id` into any echoed `constraints.requested.arguments` or error `details.requested.arguments` mapping so diagnostics retain the complete original target. In dangerous mode, the returned constraints workspace must be the selected project root. In safe/trusted, the existing unsupported elicitation behavior remains, after target validation.

- [ ] **Step 9: Run handshake/server-info/permission regressions**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_project_server_context \
  tests.extensions.test_tool_contributions \
  tests.extensions.test_extension_lifecycle \
  tests.compliance.test_mcp_contract -v
```

Expected: PASS.

- [ ] **Step 10: Commit Task 8**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/extensions tests
git commit -m "feat: make server discovery project neutral"
```

---

### Task 9: Four-Project End-to-End Startup + Stateless Concurrent Routing

**Purpose:** Prove the complete Phase A contract through a real stdio server process and layered TOML config rather than only in-process Runtime unit tests.

**Files:**
- Create: `tests/extensions/test_project_addressing_integration.py`
- Modify: `tests/compliance/mcp_client.py` to add an explicit test-only default-project option used by legacy behavior vectors.
- Modify: `tests/compliance/test_support.py` so shared behavioral fixtures opt into `default_project_id="default"`.
- Modify: `tests/compliance/test_mcp_contract.py`
- Modify: `tests/compliance/test_dual_era.py` only where old tool calls need explicit default project addressing.
- Modify: `tests/compliance/test_e2e.py`
- Modify: `tests/compliance/test_runtime_helpers.py`
- Modify: `tests/compliance/test_runtime_semantics.py`
- Modify: `tests/compliance/test_security.py`
- Modify: `tests/compliance/test_tool_golden.py`
- Modify: `tests/test_git_workdir_resolution.py`
- Modify: `tests/test_project_skills_integration.py`
- Modify: `tests/test_project_skills_runtime.py`
- Modify: `tests/test_reliable_command_recovery_http.py`
- Modify: `tests/test_telemetry.py`

**Interfaces:**
- Real server launch uses existing `--config` / `--local-config` Phase 0 flags.
- Test-only helper may inject `project_id="default"` for legacy behavior vectors, but explicit contract tests must call raw tools and verify missing project_id is rejected.
- No production compatibility mode is introduced in Phase A.

- [ ] **Step 1: Create four temporary registered projects and private local overlay**

Fixture layout:

```text
bootstrap/
alpha/
beta/
gamma/
delta/
public.toml
local.toml
```

Public config:

```toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

Local overlay:

```toml
config_version = 1

[extensions.projects.registry.alpha]
root = "/temporary/alpha"

[extensions.projects.registry.beta]
root = "/temporary/beta"

[extensions.projects.registry.gamma]
root = "/temporary/gamma"

[extensions.projects.registry.delta]
root = "/temporary/delta"
```

The test writes actual temporary paths dynamically; no absolute host path is committed.

- [ ] **Step 2: Launch the real stdio server with layered config**

Use existing `StdioMCPClient(bootstrap, extra_args=["--config", str(public), "--local-config", str(local)])`.

Assert `tools/list` has 24 names, all 13 core project-scoped schemas require project_id, skill schemas require it, and project discovery tools are global.

- [ ] **Step 3: Prove four-project concurrent stateless reads**

Issue overlapping calls from multiple client threads/process clients using the same endpoint/runtime where supported. Each project contains `identity.txt` with its own ID. Repeat at least 25 rounds per project and assert zero cross-project responses.

No call may depend on a prior `resolve_project` or list call; every read supplies project_id directly.

- [ ] **Step 4: Prove four-project command ownership and reconnect recovery**

Start a command in alpha and beta with stable client_request IDs, create a fresh client connection to the same HTTP runtime where the existing recovery harness permits, and recover by command_id without project_id. Recover by client_request_id only with its project_id. Preserve all existing stateless reconnect guarantees.

If stdio cannot share a process across client instances, keep command reconnect coverage in the existing HTTP recovery suite and use stdio integration for configuration/tool routing only.

- [ ] **Step 5: Prove no mutable current project exists**

Search the live tool catalog for forbidden activation names and assert absence:

```python
for forbidden in ("activate_project", "set_project", "select_project", "cd"):
    self.assertNotIn(forbidden, names)
```

Interleave alpha/beta requests and show ordering never changes target behavior.

- [ ] **Step 6: Update existing behavioral test helpers deliberately**

Existing compliance/golden tests that test read/list/search/patch/exec/Git behavior rather than project addressing should explicitly route through the fallback project. Add one test-only helper used by both HTTP and stdio test clients when their constructor receives a non-null `default_project_id`:

```python
PROJECT_SCOPED_TOOL_NAMES = frozenset(
    {
        "check_exec_environment",
        "read_file",
        "list_dir",
        "list_files",
        "search_text",
        "list_skills",
        "read_skill",
        "apply_patch",
        "exec_command",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_blame",
        "view_image",
    }
)

def with_default_project(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = dict(arguments)
    if name in PROJECT_SCOPED_TOOL_NAMES:
        payload.setdefault("project_id", "default")
    return payload
```

Use it only in shared test-client fixtures. Contract tests for schema/missing-address behavior bypass the helper.

Add `default_project_id: str | None = None` to `MCPClient` and `StdioMCPClient`. Their `call_tool()` methods copy the caller arguments and set `project_id` only when the option is non-null and the tool is in the test-only set. `ComplianceTestCase.setUp()` constructs `MCPClient(self.workspace.root, default_project_id="default")`; `session_for_fixture()` constructs `MCPClient(workspace.root, default_project_id="default")`. Tests whose purpose is project addressing instantiate clients without that option.

Do not add implicit default injection to production MCP code.

- [ ] **Step 7: Run full protocol/integration gates**

```bash
uv run --locked --extra dev make test-protocol test-integration test-schema-drift check-dispatch-inputs
```

Expected: PASS.

- [ ] **Step 8: Commit Task 9**

```bash
git add tests
git commit -m "test: prove multi-project runtime isolation"
```

---

### Task 10: Runtime Contract v0.4 + Operator/Developer Documentation

**Purpose:** Make the checked-in contract and operator docs describe the actual multi-project runtime rather than leaving v0.3 as misleading current documentation.

**Files:**
- Create: `docs/runtime-contract-v0.4.md`
- Modify: `docs/tools-and-schemas.md`
- Modify: `docs/extensions.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/ci-and-tests.md`
- Modify: `README.md`
- Modify: `tests/compliance/test_schema_drift.py`
- Modify: `tests/compliance/test_docs_required.py`
- Modify: `docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md`

**Interfaces:**
- v0.3 remains as historical documentation.
- v0.4 becomes the current schema-drift source of truth.
- Package version remains unchanged in this task; release/version bump is separate work.

- [ ] **Step 1: Write red docs/schema-drift expectations for v0.4**

Update `SchemaDriftTests.CONTRACT_PATH` to `docs/runtime-contract-v0.4.md` before creating the file and verify the docs/schema gate fails because the contract is absent.

Add required-doc assertion for the v0.4 file while retaining v0.3/v0.2 historical files.

- [ ] **Step 2: Verify red docs gate**

```bash
uv run --locked --extra dev python -m unittest \
  tests.compliance.test_schema_drift \
  tests.compliance.test_docs_required -v
```

Expected: failure because `runtime-contract-v0.4.md` does not yet exist/current docs still show the old schema/count.

- [ ] **Step 3: Create `runtime-contract-v0.4.md` from the live composed catalog**

The document must explicitly state:

```text
Contract: v0.4
Default projects extension: enabled
Default composed tool count: 24
Project addressing: explicit project_id; no activate/current project
Legacy --workspace launch: synthesized project_id "default" when no explicit registry exists
```

Document exact project-scoped tools:

```text
check_exec_environment
read_file
list_dir
list_files
search_text
list_skills
read_skill
apply_patch
exec_command
git_status
git_diff
git_log
git_show
git_blame
view_image
```

Document opaque/global command rules, `list_projects`, `resolve_project`, command client-request scoping, typed project errors, safe/trusted/dangerous semantics, and the fact that root exposure is operator metadata.

Do not copy stale tool tables manually without checking `Runtime.list_tools()` after implementation. The schema-drift gate must verify every live input property/annotation.

- [ ] **Step 4: Update public tools/config docs**

`docs/extensions.md` must show only safe example paths:

```toml
[extensions.projects.registry.app]
root = "/srv/projects/app"

[extensions.projects.registry.api]
root = "/srv/projects/api"
```

State that real paths belong in `coding-tools.local.toml` and that the file is gitignored.

`docs/tools-and-schemas.md` must list 24 default tools and show project_id examples for filesystem/Git/exec. `docs/quickstart.md` must show `list_projects` followed by an explicitly addressed call, while emphasizing that listing is discovery rather than activation.

`docs/ci-and-tests.md` keeps the canonical clean-checkout invocation:

```bash
uv run --locked --extra dev make compliance
uv run --locked --extra dev make ci
```

Do not change documented toolchain flow based on the earlier raw-`make` invocation outside uv; that behavior was already verified to be user-environment misuse, not a repo gap.

- [ ] **Step 5: Update README minimally**

Add a concise note that the fork can compose internal extensions and address multiple configured projects explicitly. Link to `docs/extensions.md` and `docs/runtime-contract-v0.4.md`; do not duplicate the full contract.

- [ ] **Step 6: Mark the design spec Phase A implementation status accurately**

After code/tests are green, update the design status to say Phase A implemented/verified and Phase B semantic navigation pending. Update its repository validation snapshot to the resulting HEAD only after the final Phase A implementation commit exists.

- [ ] **Step 7: Run docs/schema/privacy gates**

```bash
uv run --locked --extra dev python -m unittest \
  tests.compliance.test_schema_drift \
  tests.compliance.test_docs_required \
  tests.test_public_fork_hygiene -v
uv run --locked --extra dev python -m ruff check tests/compliance
```

Expected: PASS; zero private host markers.

- [ ] **Step 8: Commit Task 10**

```bash
git add docs README.md tests/compliance
git commit -m "docs: define multi-project runtime contract v0.4"
```

---

### Task 11: Phase A Full Acceptance + Upstream Bridge Guard

**Purpose:** Prove the resulting tree satisfies the project-addressing design and remains a clean upstream-syncable fork before any Serena work begins.

**Files:**
- Modify tests/code/docs only if a gate exposes a real defect; every defect fix starts with a failing regression test.
- No generated compliance report should remain modified after verification.

- [ ] **Step 1: Reconfirm Git/upstream state before the final gate**

```bash
git status --short --branch
git ls-remote https://github.com/xyTom/coding-tools-mcp.git refs/heads/main
git rev-list --left-right --count xyTom/main...HEAD
git diff --check
```

If upstream moved since planning, stop feature finalization, synchronize through the established upstream integration lane, resolve bridge conflicts, and rerun all relevant gates before claiming Phase A complete.

- [ ] **Step 2: Run every extension/project-specific suite**

```bash
uv run --locked --extra dev python -m unittest discover -s tests/extensions -p 'test_*.py' -v
uv run --locked --extra dev python -m unittest \
  tests.test_project_catalog \
  tests.test_project_skills_integration \
  tests.test_project_skills_runtime -v
```

Expected: PASS.

- [ ] **Step 3: Run static/schema/protocol gates**

```bash
uv run --locked --extra dev make \
  test-protocol \
  test-schema-drift \
  check-dispatch-inputs \
  lint \
  typecheck
```

Expected: PASS; Ruff and mypy clean.

- [ ] **Step 4: Run unit/integration/npm and canonical local verify**

```bash
uv run --locked --extra dev make test test-integration check-npm-launcher
mise run verify
```

Expected: PASS.

- [ ] **Step 5: Run full compliance**

```bash
uv run --locked --extra dev make compliance
```

Expected: all compliance tests pass. Inspect `reports/compliance/latest.{json,md}` only as generated evidence, then restore them unless the repository intentionally versions refreshed evidence for this change.

- [ ] **Step 6: Run direct Phase A acceptance smoke**

Programmatically create four projects and assert:

```text
one Runtime / one MCP endpoint
list_projects -> four stable IDs
same relative path resolves independently by project_id
project A direct path cannot escape into project B
no activate/current-project tool exists
client_request_id can be reused independently in A/B
command_id recovers without project_id
get_command(client_request_id) fails without project_id
bare list_commands aggregates and labels project ownership
handshake/discover contains no project-specific AGENTS content
dangerous selected cwd still follows project_id without claiming Landlock isolation
```

- [ ] **Step 7: Re-run public fork hygiene and bridge isolation guards**

```bash
uv run --locked --extra dev python -m unittest \
  tests.test_public_fork_hygiene \
  tests.extensions.test_upstream_compatibility -v

if rg -n 'extensions\.(projects|semantic|work_items|hooks|gateway)' coding_tools_mcp/server.py; then
  echo 'mother core imports extension-private modules' >&2
  exit 1
fi
```

Expected: no private extension import in `server.py`; the core knows only generic extension/service APIs.

- [ ] **Step 8: Verify clean final tree**

```bash
git status --short --branch
git diff --check
```

Expected: clean working tree. `main` may be ahead of `origin/main`; do not push automatically.

---

## Spec Coverage Checklist

| Approved Phase A requirement | Plan coverage |
| --- | --- |
| Stable configured project IDs | Task 3 |
| Config under extension TOML boundary | Tasks 3, 9, 10 |
| Immutable ProjectRegistry | Task 3 |
| Structural identity separated as scope_id | Task 3 |
| Lazy ProjectRuntime per configured project | Task 4 |
| Workspace/command/patch/runtime-dir separation | Tasks 1, 4 |
| No mutable current project / concurrency-safe routing | Tasks 1, 6, 9 |
| Nested configured-root boundary | Tasks 2, 6 |
| list_projects | Task 5 |
| resolve_project + longest-root resolution | Tasks 3, 5 |
| project_id on filesystem/Git/exec/image/environment tools | Task 6 |
| project-scoped list_skills/read_skill | Task 5 |
| Project-aware command ownership | Task 7 |
| command_id opaque recovery | Task 7 |
| client_request_id scoped by project | Task 7 |
| list_commands optional project filter + aggregate | Task 7 |
| Project-neutral handshake/discovery | Task 8 |
| Multi-project server_info | Task 8 |
| request_permissions target project | Task 8 |
| safe/trusted vs dangerous semantics | Tasks 6, 10, 11 |
| Four-project one-endpoint acceptance | Tasks 9, 11 |
| Runtime contract v0.4 | Task 10 |
| Upstream-sync bridge remains generic/localized | Tasks 1, 11 |
| Public/private configuration hygiene | Tasks 9, 10, 11 |
| Serena remains out of scope until Phase A green | Global constraints + Task 11 |

## Execution Order / Checkpoints

Execute strictly in this order:

```text
Task 1  generic scoped workspace runtime seam
Task 2  nested registered-root boundary
Task 3  stable ProjectRegistry + scope_id separation
Task 4  lazy ProjectRuntimeManager
Task 5  list/resolve projects + scoped skills
Task 6  project-scoped core tool routing
Task 7  command ownership + opaque routing
Task 8  global server context / permissions
Task 9  four-project real-server integration
Task 10 runtime contract v0.4 + docs
Task 11 full acceptance
```

Do not start Task 6 before Tasks 1-5 are individually green: routing existing bound core handlers is safe only after the state seam and registry/runtime ownership model exist. Do not start Serena/Phase B until Task 11 is fully green on a clean tree.
