# Semantic Navigation / Serena Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four backend-neutral, read-only semantic navigation tools to Coding Tools MCP through the internal `semantic` extension, backed initially by isolated per-project Serena 1.5.3 worker processes.

**Architecture:** `SemanticExtension` depends on the existing `projects` extension and contributes `list_symbols`, `find_symbol`, `find_definition`, and `find_references` only when the exact supported Serena backend is available at startup. The parent MCP process never imports Serena internals for request execution; `SerenaSemanticBackend` manages one lazy JSON-lines worker subprocess per `project_id`, and each worker imports the pinned Serena direct LSP API for exactly one registered project. Different projects therefore have physically separate Serena state, while same-project requests are serialized through that project's worker lock.

**Tech Stack:** Python 3.11+, stdlib `subprocess`/`threading`/`queue`/`json`, existing ExtensionHost/ProjectRegistry/ProjectRuntimeManager, optional `serena-agent==1.5.3`, Serena LSP backend, `unittest`, Ruff, mypy, Mise/uv, GitHub Actions.

## Global Constraints

- Work directly on `main`; the user explicitly rejected worktrees/feature branches for this repository.
- Commits are local checkpoints only. Do not push, tag, merge, force-push, or create a PR unless explicitly requested.
- `semantic` is an internal statically registered extension with `requires=("projects",)`; it is not a dynamically imported plugin.
- Keep the default enabled extension set `("projects",)`. Semantic support is opt-in through `extensions.enabled = ["projects", "semantic"]`.
- Add exactly one optional dependency extra: `semantic = ["serena-agent==1.5.3"]`. Do not widen the Serena range in Phase B.
- Serena 1.5.3 declares `Requires-Python: >=3.11,<3.15`, compatible with the repository's current Python floor.
- Do not expose Serena MCP schemas, `activate_project`, memories, editing tools, shell tools, onboarding, or Serena configuration concepts through Coding Tools MCP.
- Do not run one shared Serena `ProjectServer` across projects. Serena 1.5.3 starts that server with Flask `threaded=True`, while `SerenaAgent.active_project_context()` mutates one shared `_active_project`; a shared server therefore cannot satisfy cross-project concurrency isolation.
- Use one dedicated Serena worker subprocess per active `project_id`. Never switch a worker between projects.
- The worker protocol is private JSON-lines over stdin/stdout, protocol version `1`; it is not MCP and is never exposed to clients.
- Serena imports occur only inside `coding_tools_mcp.extensions.semantic.serena_worker` and compatibility probes. The long-lived parent adapter must stay backend-neutral except for process/version management.
- Worker startup uses the current `sys.executable` and `python -m coding_tools_mcp.extensions.semantic.serena_worker`; no arbitrary command path comes from TOML.
- Set `SERENA_HOME` to worker-owned runtime state. Do not read or write the user's global Serena config and do not create `.serena/` under registered project roots.
- Build Serena `ProjectConfig` in memory with `save_to_disk=False`, `interactive=False`, LSP backend, dashboard/log-window disabled, and a worker-owned `project_serena_folder_location` for caches.
- Pass nested registered project roots as ignored relative paths so semantic indexing for a parent cannot traverse separately registered child projects.
- Semantic public positions are 1-based line and 1-based column. Worker/Serena positions are translated to/from 0-based coordinates explicitly.
- Semantic tools are read-only, idempotent, and backend-neutral. `apply_patch` remains the structured direct-edit primitive.
- Never silently fall back to `search_text`. Semantic failure returns a typed semantic error and may include a bounded lexical-search suggestion.
- The final tool catalog is still frozen at startup. If semantic is enabled but the exact Serena backend is unavailable at startup, contribute no semantic tools and report bounded extension metadata; installing Serena later requires restart.
- If semantic tools are present and a worker later crashes, times out, or fails to start for one project, keep the catalog unchanged and return typed tool failures. Filesystem/Git tools and other projects must remain functional.
- Same-project semantic calls are serialized by a project-local worker lock. Different project workers may execute concurrently; do not add a global request lock.
- Required lifecycle configuration keys and defaults for Phase B:
  - `backend = "serena"`
  - `max_semantic_projects = 4`
  - `semantic_idle_timeout_seconds = 900`
  - `semantic_start_timeout_seconds = 60`
  - `semantic_request_timeout_seconds = 60`
  - `allow_dependency_install = false`
- Serena/SolidLSP may bootstrap language servers (`pyright` via `uvx`; TypeScript through pinned npm packages). Worker subprocesses are offline by default with `UV_OFFLINE=1` and `NPM_CONFIG_OFFLINE=true`. Network bootstrap occurs only when the operator explicitly sets `allow_dependency_install = true`; this belongs in host/local configuration when needed.
- When the worker limit is reached, evict only a worker with no in-flight request. Prefer an idle-timeout-expired worker; otherwise evict the least-recently-used idle worker. Never kill an in-flight worker.
- Bound worker protocol lines to 4 MiB and individual returned source bodies to 32 KiB UTF-8. Results use `max_results` caps before serialization.
- Worker stderr is retained only as a bounded diagnostic tail and is never returned unbounded or with raw environment/command-line data.
- Use the existing stable `RegisteredProject.project_id`; semantic state keys by `project_id`, never by basename or mutable current cwd.
- Preserve public-fork hygiene: no local roots, Serena cache paths, tunnel IDs, host config, or secrets in tracked files/reports.

---

## Verified Serena 1.5.3 Boundary

The plan is based on direct inspection/prototyping of the exact package, not memory:

```text
serena-agent==1.5.3
Requires-Python: >=3.11,<3.15

LanguageServerSymbolRetriever(project)
  .get_symbol_overview(relative_path)
  .find(name_path_pattern, substring_matching=False, within_relative_path=relative_path)
  .find_declaration(relative_file_path, line, column, include_body=False)
  .find_referencing_symbols_by_location(symbol_location, include_body=False)

ProjectConfig.autogenerate(
  project_root,
  serena_config,
  project_name=project_id,
  save_to_disk=False,
  interactive=False,
)

Project.shutdown(timeout=2.0)
```

Real temporary fixtures were probed successfully for Python and TypeScript before this plan was written. The worker will use those APIs behind a version-pinned process boundary.

---

## File / Responsibility Map

Create:

```text
coding_tools_mcp/extensions/semantic/
    __init__.py          stable semantic extension exports/capability key
    model.py             backend-neutral request/result dataclasses + payload helpers
    backend.py           SemanticBackend Protocol + SemanticBackendError
    protocol.py          strict private JSON-lines worker protocol codecs/bounds
    extension.py         config parsing, tool schemas/handlers/renderers, error mapping
    serena.py            parent SerenaSemanticBackend + lazy bounded worker manager
    serena_worker.py     per-project subprocess; the only production Serena-importing module

tests/extensions/
    test_semantic_model.py
    test_semantic_extension.py
    test_semantic_worker_protocol.py
    test_semantic_serena_backend.py
    test_semantic_serena_integration.py
    fixtures/semantic/python/sample.py
    fixtures/semantic/typescript/sample.ts
```

Modify:

```text
coding_tools_mcp/extensions/__init__.py        register SemanticExtension, keep default projects-only
coding_tools_mcp/extensions/services.py        expose runtime_dir on WorkspaceRuntimeHandle protocol
pyproject.toml                                 add exact optional semantic extra
coding-tools.toml                              document semantic table without enabling it
tests/compliance/test_schema_drift.py          preserve 24-tool default; validate optional semantic docs separately
.github/workflows/compliance.yml               dedicated Serena integration job
docs/extensions.md                             semantic extension/operator config
docs/tools-and-schemas.md                      optional 28-tool composition and schemas
docs/runtime-contract-v0.4.md                  optional semantic extension contract
docs/quickstart.md                              installation/enablement example
docs/ci-and-tests.md                            semantic integration gate
docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md
```

Do not put Serena-specific code in `coding_tools_mcp/server.py`.

---

### Task 1: Backend-Neutral Semantic Model and Error Contract

**Files:**
- Create: `coding_tools_mcp/extensions/semantic/__init__.py`
- Create: `coding_tools_mcp/extensions/semantic/model.py`
- Create: `coding_tools_mcp/extensions/semantic/backend.py`
- Create: `tests/extensions/test_semantic_model.py`

**Interfaces:**
- Produces `SemanticPosition`, `SemanticRange`, `SemanticSymbol`, `SemanticReference`.
- Produces request dataclasses `ListSymbolsRequest`, `FindSymbolRequest`, `FindDefinitionRequest`, `FindReferencesRequest`.
- Produces result dataclasses `ListSymbolsResult`, `FindSymbolResult`, `FindDefinitionResult`, `FindReferencesResult`.
- Produces `SemanticBackend` and `SemanticBackendError`.
- All result objects expose `payload()` returning JSON-safe dict/list primitives.

- [ ] **Step 1: Write RED tests for normalized positions/ranges/symbol payloads**

Create `tests/extensions/test_semantic_model.py` with concrete assertions:

```python
from coding_tools_mcp.extensions.semantic.model import (
    SemanticPosition,
    SemanticRange,
    SemanticReference,
    SemanticSymbol,
)


class SemanticModelTests(unittest.TestCase):
    def test_symbol_payload_is_backend_neutral_and_json_safe(self) -> None:
        symbol = SemanticSymbol(
            name="hello",
            name_path="Greeter/hello",
            kind="method",
            path="src/sample.py",
            range=SemanticRange(
                start=SemanticPosition(line=2, column=5),
                end=SemanticPosition(line=3, column=33),
            ),
            children=(),
        )
        self.assertEqual(
            symbol.payload(),
            {
                "name": "hello",
                "name_path": "Greeter/hello",
                "kind": "method",
                "path": "src/sample.py",
                "range": {
                    "start": {"line": 2, "column": 5},
                    "end": {"line": 3, "column": 33},
                },
                "children": [],
            },
        )

    def test_optional_body_is_bounded_metadata_not_backend_blob(self) -> None:
        symbol = SemanticSymbol(
            name="f",
            name_path="f",
            kind="function",
            path="a.py",
            range=SemanticRange(
                start=SemanticPosition(1, 1),
                end=SemanticPosition(1, 10),
            ),
            body="def f(): pass",
            body_truncated=True,
        )
        payload = symbol.payload()
        self.assertEqual(payload["body"], "def f(): pass")
        self.assertIs(payload["body_truncated"], True)
        self.assertNotIn("serena", payload)

    def test_reference_payload_includes_reference_range_and_containing_symbol(self) -> None:
        containing = SemanticSymbol.summary(
            name="run",
            name_path="run",
            kind="function",
            path="src/sample.py",
        )
        ref = SemanticReference(
            path="src/sample.py",
            range=SemanticRange(
                start=SemanticPosition(10, 12),
                end=SemanticPosition(10, 19),
            ),
            containing_symbol=containing,
        )
        self.assertEqual(ref.payload()["containing_symbol"]["name_path"], "run")
```

- [ ] **Step 2: Run the tests and verify import failure**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_semantic_model -v
```

Expected RED: `coding_tools_mcp.extensions.semantic` model module does not exist.

- [ ] **Step 3: Implement immutable normalized model dataclasses**

Create `model.py` with these exact public fields:

```python
@dataclass(frozen=True)
class SemanticPosition:
    line: int
    column: int

    def payload(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True)
class SemanticRange:
    start: SemanticPosition
    end: SemanticPosition

    def payload(self) -> dict[str, object]:
        return {"start": self.start.payload(), "end": self.end.payload()}


@dataclass(frozen=True)
class SemanticSymbol:
    name: str
    name_path: str
    kind: str
    path: str
    range: SemanticRange | None = None
    children: tuple["SemanticSymbol", ...] = ()
    body: str | None = None
    body_truncated: bool = False

    @classmethod
    def summary(cls, *, name: str, name_path: str, kind: str, path: str) -> "SemanticSymbol":
        return cls(name=name, name_path=name_path, kind=kind, path=path)

    def payload(self) -> dict[str, object]:
        value: dict[str, object] = {
            "name": self.name,
            "name_path": self.name_path,
            "kind": self.kind,
            "path": self.path,
        }
        if self.range is not None:
            value["range"] = self.range.payload()
        if self.children:
            value["children"] = [child.payload() for child in self.children]
        elif self.range is not None:
            value["children"] = []
        if self.body is not None:
            value["body"] = self.body
            value["body_truncated"] = self.body_truncated
        return value


@dataclass(frozen=True)
class SemanticReference:
    path: str
    range: SemanticRange
    containing_symbol: SemanticSymbol | None = None

    def payload(self) -> dict[str, object]:
        value = {"path": self.path, "range": self.range.payload()}
        if self.containing_symbol is not None:
            value["containing_symbol"] = self.containing_symbol.payload()
        return value
```

Request dataclasses:

```python
@dataclass(frozen=True)
class ListSymbolsRequest:
    path: str
    depth: int = 1
    max_results: int = 500

@dataclass(frozen=True)
class FindSymbolRequest:
    query: str
    path: str = ""
    include_body: bool = False
    max_results: int = 50

@dataclass(frozen=True)
class FindDefinitionRequest:
    path: str
    line: int
    column: int

@dataclass(frozen=True)
class FindReferencesRequest:
    path: str
    line: int
    column: int
    include_declaration: bool = False
    max_results: int = 500
```

Result dataclasses always carry `truncated` and `warnings` where bounding can occur. Their `payload()` methods expose only normalized fields.

Use these exact result fields:

```text
ListSymbolsResult(symbols: tuple[SemanticSymbol, ...], truncated: bool = false, warnings: tuple[str, ...] = ())
FindSymbolResult(symbols: tuple[SemanticSymbol, ...], truncated: bool = false, warnings: tuple[str, ...] = ())
FindDefinitionResult(definitions: tuple[SemanticSymbol, ...], truncated: bool = false, warnings: tuple[str, ...] = ())
FindReferencesResult(references: tuple[SemanticReference, ...], truncated: bool = false, warnings: tuple[str, ...] = ())
```

Each `payload()` emits the corresponding collection as JSON-safe dictionaries plus `truncated` and `warnings` as a list.

- [ ] **Step 4: Implement the backend protocol and typed backend error**

Create `backend.py`:

```python
class SemanticBackendError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})


class SemanticBackend(Protocol):
    backend_name: str
    backend_version: str | None
    available: bool
    availability_reason: str | None

    def list_symbols(self, project: RegisteredProject, request: ListSymbolsRequest) -> ListSymbolsResult:
        raise NotImplementedError

    def find_symbol(self, project: RegisteredProject, request: FindSymbolRequest) -> FindSymbolResult:
        raise NotImplementedError

    def find_definition(self, project: RegisteredProject, request: FindDefinitionRequest) -> FindDefinitionResult:
        raise NotImplementedError

    def find_references(self, project: RegisteredProject, request: FindReferencesRequest) -> FindReferencesResult:
        raise NotImplementedError

    def close_project(self, project_id: str) -> None:
        raise NotImplementedError

    def close(self) -> tuple[str, ...]:
        raise NotImplementedError
```

Define semantic code constants in this module:

```python
SEMANTIC_BACKEND_UNAVAILABLE = "SEMANTIC_BACKEND_UNAVAILABLE"
SEMANTIC_PROJECT_START_FAILED = "SEMANTIC_PROJECT_START_FAILED"
SEMANTIC_LANGUAGE_UNSUPPORTED = "SEMANTIC_LANGUAGE_UNSUPPORTED"
SEMANTIC_FILE_UNSUPPORTED = "SEMANTIC_FILE_UNSUPPORTED"
SEMANTIC_SYMBOL_NOT_FOUND = "SEMANTIC_SYMBOL_NOT_FOUND"
SEMANTIC_POSITION_INVALID = "SEMANTIC_POSITION_INVALID"
SEMANTIC_TIMEOUT = "SEMANTIC_TIMEOUT"
SEMANTIC_BACKEND_ERROR = "SEMANTIC_BACKEND_ERROR"
```

Define `SEMANTIC_BACKEND = CapabilityKey[SemanticBackend]("semantic.backend")` in `backend.py` after the Protocol and re-export it from `semantic/__init__.py`. Keeping the key beside the Protocol avoids a circular import between `semantic.__init__` and `extension.py`.

- [ ] **Step 5: Run model tests, Ruff, and mypy**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_semantic_model -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_model.py
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions/semantic/model.py coding_tools_mcp/extensions/semantic/backend.py
```

Expected: all green.

- [ ] **Step 6: Commit Task 1**

```bash
git add coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_model.py
git commit -m "feat: add semantic backend contract"
```

---

### Task 2: Semantic Extension, Strict Configuration, and Four Public Tools

**Files:**
- Create: `coding_tools_mcp/extensions/semantic/extension.py`
- Create: `tests/extensions/test_semantic_extension.py`
- Modify: `coding_tools_mcp/extensions/semantic/__init__.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`
- Modify: `coding-tools.toml`

**Interfaces:**
- Produces `SemanticExtension`, `SEMANTIC_BACKEND` capability, four tool contributions and model-facing renderers.
- Consumes `PROJECT_REGISTRY` and `PROJECT_RUNTIMES` from the `projects` extension.
- Extension constructor accepts an optional backend factory for direct unit tests; normal registry construction uses `SerenaSemanticBackend`.
- Registration contributes tools only when the selected backend reports exact startup availability.

`SemanticExtension.register()` creates exactly one backend instance, publishes it through `context.services.provide(SEMANTIC_BACKEND, backend)`, and contributes bounded metadata in both available and unavailable cases. It contributes the four tools only when `backend.available is True`.

Use this injectable factory type in `extension.py`:

```python
SemanticBackendFactory = Callable[
    [SemanticConfig, ProjectRegistry, ProjectRuntimeManager],
    SemanticBackend,
]
```

The no-argument production constructor stores the real Serena factory; unit tests may pass a fake factory directly.

- [ ] **Step 1: Write RED config/dependency/availability tests**

Create a fake backend and factory in `test_semantic_extension.py`:

```python
class FakeBackend:
    backend_name = "fake"
    backend_version = "1"

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.availability_reason = None if available else "fake backend unavailable"
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def close_project(self, project_id: str) -> None:
        self.calls.append(("close_project", project_id))

    def close(self) -> tuple[str, ...]:
        self.closed = True
        return ()
```

Tests must assert:

```python
def test_semantic_manifest_requires_projects(self) -> None:
    self.assertEqual(SemanticExtension.manifest.requires, ("projects",))

def test_builtin_registry_knows_semantic_but_does_not_enable_it_by_default(self) -> None:
    registry = builtin_extension_registry()
    self.assertEqual(registry.default_enabled, ("projects",))
    self.assertIs(registry.extension_type("semantic"), SemanticExtension)

def test_semantic_config_rejects_unknown_backend(self) -> None:
    extension = SemanticExtension(backend_factory=lambda config, registry, runtimes: FakeBackend())
    with self.assertRaisesRegex(ConfigError, "extensions.semantic.backend"):
        extension.configure({"backend": "unknown"})

def test_unavailable_backend_contributes_no_tools_but_metadata_is_bounded(self) -> None:
    backend = FakeBackend(available=False)
    extension, context, contributions = semantic_extension_fixture(backend)
    extension.configure({})
    extension.register(context)
    self.assertEqual(contributions.tool_entries(), ())
    self.assertEqual(
        contributions.metadata_snapshot()["semantic"],
        {
            "backend": "fake",
            "backend_version": "1",
            "available": False,
            "reason": "fake backend unavailable",
        },
    )
```

Use a tiny `ProjectRegistry`/`ProjectRuntimeManager` fixture from existing project test helpers instead of mocking server globals.

- [ ] **Step 2: Verify RED**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_semantic_extension -v
```

Expected: semantic extension module/registry entry missing.

- [ ] **Step 3: Add the strict semantic config schema and normalized config object**

Define in `extension.py`:

```python
@dataclass(frozen=True)
class SemanticConfig:
    backend: str = "serena"
    max_semantic_projects: int = 4
    semantic_idle_timeout_seconds: int = 900
    semantic_start_timeout_seconds: int = 60
    semantic_request_timeout_seconds: int = 60
    allow_dependency_install: bool = False


SEMANTIC_CONFIG_SCHEMA = table(
    {
        "backend": scalar(str),
        "max_semantic_projects": scalar(int),
        "semantic_idle_timeout_seconds": scalar(int),
        "semantic_start_timeout_seconds": scalar(int),
        "semantic_request_timeout_seconds": scalar(int),
        "allow_dependency_install": scalar(bool),
    }
)
```

`configure()` validates:

```text
backend == "serena"
1 <= max_semantic_projects <= 32
1 <= semantic_idle_timeout_seconds <= 86400
1 <= semantic_start_timeout_seconds <= 600
1 <= semantic_request_timeout_seconds <= 600
allow_dependency_install is boolean
```

Do not accept floats for these integer fields.

- [ ] **Step 4: Define exact public schemas**

Use local schema helpers in `extension.py`; do not reach into `server.input_schemas()`.

```python
LIST_SYMBOLS_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "minLength": 1},
        "path": {"type": "string", "minLength": 1},
        "depth": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 500},
    },
    "required": ["project_id", "path"],
    "additionalProperties": False,
}

FIND_SYMBOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 1},
        "path": {"type": "string", "default": ""},
        "include_body": {"type": "boolean", "default": False},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
    "required": ["project_id", "query"],
    "additionalProperties": False,
}

POSITION_SCHEMA_PROPERTIES = {
    "project_id": {"type": "string", "minLength": 1},
    "path": {"type": "string", "minLength": 1},
    "line": {"type": "integer", "minimum": 1},
    "column": {"type": "integer", "minimum": 1},
}
```

`find_definition` requires those four fields. `find_references` adds
`include_declaration` default false and `max_results` 1..1000 default 500.

- [ ] **Step 5: Implement project/path validation and backend error mapping**

Handlers obtain the project through `PROJECT_REGISTRY.require_available()` and validate requested files/workdirs through `PROJECT_RUNTIMES` before backend execution:

```python
project = self._registry.require_available(project_id)
resolved = self._runtimes.resolve_existing(project_id, path)
```

For `list_symbols`, `find_definition`, and `find_references`, require a file. For `find_symbol`, an empty path means project root; a non-empty path may be file or directory but must resolve inside the selected project boundary.

After validation, build the backend request with `resolved.display`, not the caller's raw path. This canonical project-relative display path is the only path passed into Serena. For `find_symbol(path="")`, pass `""`; otherwise use the canonical display path.

Map `SemanticBackendError` to `ToolFailure`:

```python
details = {
    "project_id": project_id,
    "backend": self._backend.backend_name,
    **bounded_backend_details(exc.details),
}
if exc.code in {SEMANTIC_BACKEND_UNAVAILABLE, SEMANTIC_TIMEOUT, SEMANTIC_BACKEND_ERROR}:
    details.setdefault("retry_hint", "Retry the semantic request once; if it remains unavailable, use search_text explicitly as a lexical fallback.")
raise ToolFailure(
    exc.code,
    exc.message,
    category="semantic",
    retryable=exc.retryable,
    details=details,
)
```

Never copy absolute worker paths, environment values, traceback text, or raw stderr into `details`.

- [ ] **Step 6: Implement four tool contributions and useful text renderers**

All four use:

```python
ToolAnnotations(read_only=True, idempotent=True)
```

Success payload shapes:

```python
{
    "project_id": project_id,
    "backend": backend.backend_name,
    "symbols": [symbol.payload() for symbol in result.symbols],
    "truncated": result.truncated,
    "warnings": list(result.warnings),
}

{
    "project_id": project_id,
    "backend": backend.backend_name,
    "definitions": [symbol.payload() for symbol in result.definitions],
    "truncated": result.truncated,
    "warnings": list(result.warnings),
}

{
    "project_id": project_id,
    "backend": backend.backend_name,
    "references": [reference.payload() for reference in result.references],
    "truncated": result.truncated,
    "warnings": list(result.warnings),
}
```

Renderers output bounded lines such as:

```text
class Greeter — src/sample.py:1
method Greeter/hello — src/sample.py:2
```

Do not mirror JSON into model-facing text.

Lifecycle remains lazy and bounded:

```python
def start(self) -> None:
    return None

def stop(self) -> None:
    if self._backend is None:
        return
    warnings = self._backend.close()
    if warnings:
        raise RuntimeError("; ".join(warnings[:4]))
```

`start()` launches no Serena workers. The ExtensionHost already attempts all extension stops even if one stop reports a failure.

- [ ] **Step 7: Register SemanticExtension statically but keep it disabled by default**

Update `builtin_extension_registry()`:

```python
from .projects import ProjectsExtension
from .semantic import SemanticExtension

return ExtensionRegistry(
    [ProjectsExtension, SemanticExtension],
    default_enabled=("projects",),
)
```

Add a non-activating public config table:

```toml
[extensions.semantic]
backend = "serena"
max_semantic_projects = 4
semantic_idle_timeout_seconds = 900
semantic_start_timeout_seconds = 60
semantic_request_timeout_seconds = 60
allow_dependency_install = false
```

- [ ] **Step 8: Run fake-backend semantic extension tests and core compatibility**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_semantic_extension \
  tests.extensions.test_extension_dependencies \
  tests.extensions.test_upstream_compatibility \
  tests.compliance.test_mcp_contract -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_extension.py
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions/semantic
```

Expected: default runtime remains 24 tools; semantic fake-available runtime exposes 28.

- [ ] **Step 9: Commit Task 2**

```bash
git add coding_tools_mcp/extensions coding-tools.toml tests/extensions/test_semantic_extension.py
git commit -m "feat: add semantic extension tools"
```

---

### Task 3: Private Worker Protocol and Cross-Platform Subprocess Transport

**Files:**
- Create: `coding_tools_mcp/extensions/semantic/protocol.py`
- Create: `coding_tools_mcp/extensions/semantic/serena.py`
- Create: `tests/extensions/test_semantic_worker_protocol.py`
- Create: `tests/extensions/test_semantic_serena_backend.py`
- Modify: `coding_tools_mcp/extensions/services.py`

**Interfaces:**
- Produces protocol version 1 message codecs and size limits.
- Produces `SerenaSemanticBackend` and internal `_SerenaWorker`.
- `WorkspaceRuntimeHandle` gains a read-only `runtime_dir: Path` protocol property; the existing server runtime state already supplies it.
- Parent process never imports `serena.*`.

- [ ] **Step 1: Write RED protocol codec tests**

Required protocol messages:

```json
{"type":"ready","protocol":1,"project_id":"app","backend":"serena","backend_version":"1.5.3","languages":["python"]}
{"type":"request","protocol":1,"id":"r1","op":"list_symbols","params":{"path":"a.py","depth":1,"max_results":10}}
{"type":"response","protocol":1,"id":"r1","ok":true,"result":{"symbols":[],"truncated":false,"warnings":[]}}
{"type":"response","protocol":1,"id":"r1","ok":false,"error":{"code":"SEMANTIC_FILE_UNSUPPORTED","message":"unsupported file","retryable":false,"details":{}}}
```

Tests reject:

- wrong protocol version;
- unknown message type;
- missing request ID;
- unknown operation;
- non-object params/result/details;
- a line larger than `MAX_WORKER_MESSAGE_BYTES = 4 * 1024 * 1024`;
- malformed UTF-8/JSON.

- [ ] **Step 2: Verify protocol RED**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_semantic_worker_protocol -v
```

- [ ] **Step 3: Implement strict protocol codecs**

In `protocol.py` define:

```python
WORKER_PROTOCOL_VERSION = 1
MAX_WORKER_MESSAGE_BYTES = 4 * 1024 * 1024
SEMANTIC_OPERATIONS = frozenset({
    "list_symbols",
    "find_symbol",
    "find_definition",
    "find_references",
})
```

`encode_message(message) -> bytes` appends exactly one `b"\n"`, uses compact JSON UTF-8, rejects oversize messages. `decode_message(line) -> dict[str, object]` checks the byte bound before decode and validates protocol/type/shape.

- [ ] **Step 4: Write RED parent worker tests with a fake JSON-lines child**

The test module writes a temporary Python worker script that supports modes via environment:

```text
ready       emit ready, echo successful normalized responses
slow        emit ready, sleep past request timeout
crash       emit ready, exit on request
bad_ready   emit malformed/wrong ready
stderr      emit a very large stderr diagnostic then a bounded error
```

Tests assert:

```python
def test_worker_waits_for_matching_ready_project_and_version(self) -> None:
    worker = fake_worker(mode="ready", project_id="alpha")
    self.addCleanup(worker.close)
    self.assertEqual(worker.backend_version, "1.5.3")
    self.assertEqual(worker.project_id, "alpha")

def test_worker_round_trip_returns_result(self) -> None:
    worker = fake_worker(mode="ready", project_id="alpha")
    self.addCleanup(worker.close)
    result = worker.request("list_symbols", {"path": "a.py", "depth": 1, "max_results": 10})
    self.assertEqual(result, {"symbols": [], "truncated": False, "warnings": []})

def test_request_timeout_terminates_worker_and_is_retryable(self) -> None:
    worker = fake_worker(mode="slow", project_id="alpha", request_timeout_seconds=1)
    with self.assertRaises(SemanticBackendError) as raised:
        worker.request("list_symbols", {"path": "a.py", "depth": 1, "max_results": 10})
    self.assertEqual(raised.exception.code, SEMANTIC_TIMEOUT)
    self.assertTrue(raised.exception.retryable)
    self.assertFalse(worker.alive)

def test_worker_crash_is_backend_error_and_next_call_can_restart(self) -> None:
    backend = fake_backend(worker_modes=["crash", "ready"])
    with self.assertRaises(SemanticBackendError):
        backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    result = backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    self.assertEqual(result.symbols, ())

def test_stderr_diagnostics_are_bounded(self) -> None:
    worker = fake_worker(mode="stderr", project_id="alpha")
    self.addCleanup(worker.close)
    with self.assertRaises(SemanticBackendError) as raised:
        worker.request("list_symbols", {"path": "a.py", "depth": 1, "max_results": 10})
    diagnostic = str(raised.exception.details.get("diagnostic", ""))
    self.assertLessEqual(len(diagnostic.encode("utf-8")), 16 * 1024)

def test_close_is_idempotent(self) -> None:
    worker = fake_worker(mode="ready", project_id="alpha")
    worker.close()
    worker.close()
    self.assertFalse(worker.alive)
```

- [ ] **Step 5: Implement `_SerenaWorker` without Serena imports**

Use:

```python
class _SerenaWorker:
    def __init__(
        self,
        *,
        project: RegisteredProject,
        state_dir: Path,
        excluded_roots: tuple[Path, ...],
        start_timeout_seconds: int,
        request_timeout_seconds: int,
        command: Sequence[str] | None = None,  # tests only; production None
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.project = project
        self.state_dir = state_dir
        self.excluded_roots = excluded_roots
        self.start_timeout_seconds = start_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.command = tuple(command) if command is not None else None
        self.environ = dict(environ or {})
```

Production command when `command is None`:

```python
[
    sys.executable,
    "-m",
    "coding_tools_mcp.extensions.semantic.serena_worker",
    "--project-id", project.project_id,
    "--project-root", str(project.root),
    "--state-dir", str(state_dir),
    *[argument for root in excluded_roots for argument in ("--excluded-root", str(root))],
]
```

Use `stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE`, `text=False`, `bufsize=0`.

Start exactly two daemon reader threads:

- stdout reader decodes bounded protocol lines and places messages on `queue.Queue`;
- stderr reader keeps only the newest 16 KiB in a locked bounded byte buffer.

The worker request lock serializes one project's requests. `request()` writes one encoded request, flushes, waits `queue.get(timeout=...)`, verifies matching ID, and maps worker errors to `SemanticBackendError`.

On timeout/crash/protocol corruption, terminate/kill boundedly and mark worker unusable. Never leave a child process behind after `close()`.

Production worker environment is allowlisted rather than copied wholesale. Preserve only process/runtime essentials (`PATH`, `PATHEXT`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`, `TEMP`, `TMP`) from the parent when present, then override:

```python
worker_home = state_dir / "home"
worker_tmp = state_dir / "tmp"
worker_cache = state_dir / "cache"
for path in (worker_home, worker_tmp, worker_cache, state_dir / "serena-home"):
    path.mkdir(parents=True, exist_ok=True)
env.update(
    {
        "HOME": str(worker_home),
        "USERPROFILE": str(worker_home),
        "TMPDIR": str(worker_tmp),
        "TEMP": str(worker_tmp),
        "TMP": str(worker_tmp),
        "XDG_CACHE_HOME": str(worker_cache),
        "SERENA_HOME": str(state_dir / "serena-home"),
        "PYTHONUNBUFFERED": "1",
    }
)
```

Do not propagate API keys, tokens, cloud credentials, Doppler variables, or arbitrary caller environment values. Tests inspect the child environment through the fake worker and assert a sentinel secret is absent.

If `SemanticConfig.allow_dependency_install` is false, force the child offline:

```python
env["UV_OFFLINE"] = "1"
env["NPM_CONFIG_OFFLINE"] = "true"
```

If it is true, omit those two overrides. This startup configuration is the only Phase B switch that permits Serena/SolidLSP to fetch missing language-server runtime dependencies; it is never exposed as a per-request MCP argument.

- [ ] **Step 6: Extend `WorkspaceRuntimeHandle` protocol with runtime state path**

In `extensions/services.py`:

```python
class WorkspaceRuntimeHandle(Protocol):
    @property
    def root(self) -> Path:
        raise NotImplementedError

    @property
    def runtime_dir(self) -> Path:
        raise NotImplementedError
```

Run mypy against `server.py` to prove the actual workspace runtime state satisfies the structural protocol.

- [ ] **Step 7: Implement Serena backend availability detection and one-worker-per-project mapping**

In `serena.py`:

```python
SERENA_DISTRIBUTION = "serena-agent"
SUPPORTED_SERENA_VERSION = "1.5.3"


@dataclass(frozen=True)
class SerenaAvailability:
    available: bool
    version: str | None
    reason: str | None = None


def detect_serena() -> SerenaAvailability:
    try:
        version = importlib.metadata.version(SERENA_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return SerenaAvailability(False, None, "serena-agent is not installed")
    if version != SUPPORTED_SERENA_VERSION:
        return SerenaAvailability(False, version, f"unsupported Serena version: {version}")
    return SerenaAvailability(True, version)
```

`SerenaSemanticBackend` receives `ProjectRegistry`, `ProjectRuntimeManager`, and `SemanticConfig`; it owns `_workers: dict[str, _SerenaWorker]` protected by an `RLock` used only for worker-map/lifecycle bookkeeping.

Its constructor snapshots `detect_serena()` and exposes the protocol fields explicitly:

```python
class SerenaSemanticBackend:
    backend_name = "serena"

    def __init__(self, config: SemanticConfig, registry: ProjectRegistry, runtimes: ProjectRuntimeManager) -> None:
        availability = detect_serena()
        self.available = availability.available
        self.backend_version = availability.version
        self.availability_reason = availability.reason
        self.config = config
        self.registry = registry
        self.runtimes = runtimes
        self._workers: dict[str, _SerenaWorker] = {}
        self._lock = threading.RLock()
```

Task 5 refactors this simple map to `_WorkerRecord` when idle/LRU accounting is introduced. Task 3 must compile and pass independently before that refactor.

When `available` is false, every direct backend operation raises non-retryable `SEMANTIC_BACKEND_UNAVAILABLE`; normal ExtensionHost startup still succeeds because the extension contributes no semantic tools in that state.

Worker state path:

```python
project_runtime = runtimes.require(project.project_id)
state_dir = project_runtime.workspace.runtime_dir / "semantic" / "serena"
```

Excluded roots come from `registry.excluded_roots_for(project_id)`.

- [ ] **Step 8: Run protocol/backend unit tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_semantic_worker_protocol \
  tests.extensions.test_semantic_serena_backend -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_worker_protocol.py tests/extensions/test_semantic_serena_backend.py
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions/semantic coding_tools_mcp/extensions/services.py coding_tools_mcp/server.py
```

- [ ] **Step 9: Commit Task 3**

```bash
git add coding_tools_mcp/extensions/semantic coding_tools_mcp/extensions/services.py tests/extensions
git commit -m "feat: add isolated semantic worker transport"
```

---

### Task 4: Serena 1.5.3 Worker Direct-LSP Adapter

**Files:**
- Create: `coding_tools_mcp/extensions/semantic/serena_worker.py`
- Create: `tests/extensions/test_semantic_serena_integration.py`
- Create: `tests/extensions/fixtures/semantic/python/sample.py`
- Create: `tests/extensions/fixtures/semantic/typescript/sample.ts`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Worker owns all `serena.*` imports.
- Worker transforms Serena 0-based symbols/references into the backend-neutral 1-based JSON protocol.
- Parent sees only protocol/result models.

- [ ] **Step 1: Add exact optional dependency but do not add it to `dev`**

In `pyproject.toml`:

```toml
[project.optional-dependencies]
semantic = [
  "serena-agent==1.5.3",
]
```

Keep the existing `dev`, `desktop`, and `image` groups unchanged.

Refresh the lock immediately after the pyproject edit:

```bash
uv lock
uv run --locked --extra dev --extra semantic python -c "import importlib.metadata as m; assert m.version('serena-agent') == '1.5.3'"
```

Do not continue if the lock resolves a different Serena version.

- [ ] **Step 2: Write real Python and TypeScript fixture files**

Python:

```python
class Greeter:
    def hello(self, name: str) -> str:
        return format_name(name)


def format_name(name: str) -> str:
    return name.title()


def run() -> str:
    return Greeter().hello("world")
```

TypeScript:

```typescript
export class Greeter {
  hello(name: string): string {
    return formatName(name)
  }
}

export function formatName(name: string): string {
  return name.toUpperCase()
}

export function run(): string {
  return new Greeter().hello("world")
}
```

- [ ] **Step 3: Write RED integration tests that skip only when the semantic extra is absent**

At module setup:

```python
SERENA_AVAILABLE = detect_serena().available

@unittest.skipUnless(SERENA_AVAILABLE, "serena-agent==1.5.3 semantic extra is not installed")
class SerenaWorkerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_root = self.root / "runtime-state"
```

Tests execute the actual worker through `SerenaSemanticBackend`, not Serena APIs directly:

```python
def test_python_list_symbols_and_find_symbol_are_normalized(self) -> None:
    project, backend = self.backend_for_fixture("python")
    self.addCleanup(backend.close)
    listed = backend.list_symbols(project, ListSymbolsRequest(path="sample.py", depth=1))
    found = backend.find_symbol(project, FindSymbolRequest(query="Greeter/hello", path="sample.py"))
    self.assertIn("Greeter", [symbol.name_path for symbol in listed.symbols])
    self.assertEqual([symbol.name_path for symbol in found.symbols], ["Greeter/hello"])
    self.assertEqual(found.symbols[0].path, "sample.py")

def test_python_definition_and_references_use_one_based_public_positions(self) -> None:
    project, backend = self.backend_for_fixture("python")
    self.addCleanup(backend.close)
    definition = backend.find_definition(
        project,
        FindDefinitionRequest(path="sample.py", line=10, column=12),
    )
    self.assertTrue(definition.definitions)
    symbol = definition.definitions[0]
    self.assertGreaterEqual(symbol.range.start.line, 1)
    self.assertGreaterEqual(symbol.range.start.column, 1)
    references = backend.find_references(
        project,
        FindReferencesRequest(path="sample.py", line=10, column=12),
    )
    self.assertTrue(references.references)
    self.assertTrue(all(item.range.start.line >= 1 for item in references.references))

def test_typescript_list_symbols_and_find_symbol_are_normalized(self) -> None:
    project, backend = self.backend_for_fixture("typescript")
    self.addCleanup(backend.close)
    listed = backend.list_symbols(project, ListSymbolsRequest(path="sample.ts", depth=1))
    found = backend.find_symbol(project, FindSymbolRequest(query="Greeter/hello", path="sample.ts"))
    self.assertIn("Greeter", [symbol.name_path for symbol in listed.symbols])
    self.assertEqual([symbol.name_path for symbol in found.symbols], ["Greeter/hello"])

def test_parent_project_ignores_registered_nested_child_sources(self) -> None:
    parent, child, backend = self.backend_for_nested_projects()
    self.addCleanup(backend.close)
    found = backend.find_symbol(parent, FindSymbolRequest(query="ChildOnlySymbol"))
    self.assertEqual(found.symbols, ())
    child_found = backend.find_symbol(child, FindSymbolRequest(query="ChildOnlySymbol"))
    self.assertEqual([symbol.name_path for symbol in child_found.symbols], ["ChildOnlySymbol"])

def test_worker_does_not_create_dot_serena_inside_fixture_project(self) -> None:
    project, backend = self.backend_for_fixture("python")
    self.addCleanup(backend.close)
    backend.list_symbols(project, ListSymbolsRequest(path="sample.py"))
    self.assertFalse((project.root / ".serena").exists())
```

Implement `backend_for_fixture()` and `backend_for_nested_projects()` in the test module with real `ProjectRegistry` records plus the smallest project-runtime stub that returns a worker-owned `runtime_dir`. Copy the committed fixture into the temporary root before backend creation; do not point Serena at the repository's tracked fixture directory because the no-`.serena` assertion must exercise a disposable project root.

For the nested-boundary test, create parent/child temporary roots, register both, place a uniquely named symbol only in child, query parent `find_symbol`, and assert no match.

- [ ] **Step 4: Verify RED with the semantic extra**

```bash
uv run --locked --extra dev --extra semantic python -m unittest tests.extensions.test_semantic_serena_integration -v
```

Expected: failures because `serena_worker.py` is missing.

- [ ] **Step 5: Implement worker startup without disk project config**

`serena_worker.py` parses only these CLI args:

```text
--project-id
--project-root
--state-dir
--excluded-root (repeatable)
```

Inside worker startup:

```python
from serena.config.serena_config import (
    LanguageBackend,
    ProjectConfig,
    RegisteredProject as SerenaRegisteredProject,
    SerenaConfig,
)
from serena.symbol import LanguageServerSymbolRetriever

config = SerenaConfig(
    gui_log_window=False,
    web_dashboard=False,
    language_backend=LanguageBackend.LSP,
    project_serena_folder_location=str(state_dir / "project-state"),
)
project_config = ProjectConfig.autogenerate(
    project_root,
    config,
    project_name=project_id,
    save_to_disk=False,
    interactive=False,
)
project_config.ignored_paths.extend(relative_excluded_patterns)
registered = SerenaRegisteredProject(str(project_root), project_config)
project = registered.get_project_instance(config)
project.create_language_server_manager()
retriever = LanguageServerSymbolRetriever(project)
```

Set `SERENA_HOME` in the parent worker environment to `state_dir / "serena-home"` before process start. In addition, the worker's `SerenaConfig` is constructed directly rather than `from_config_file()`, so user-global Serena configuration is never loaded.

Nested exclusions are canonical descendants of project root converted to POSIX-relative pathspecs:

```python
relative = excluded.relative_to(project_root).as_posix()
patterns.extend([relative, f"{relative}/**"])
```

Before Serena creates its LSP manager, walk the project tree once with `os.walk(project_root, followlinks=False)`. For each symlinked file or directory, resolve it strictly. If it resolves outside `project_root` or inside one of the separately registered excluded roots, add that symlink's project-relative path and `<path>/**` to `ignored_paths`. This prevents Serena's own `os.walk(..., followlinks=True)` source discovery from traversing an unsafe symlink before any semantic request is made.

All normalized result paths pass through `_safe_relative_path(project_root, excluded_roots, relative_path)`. Reject/skip `None`, absolute paths, `..` paths, Serena external-source identifiers, symlink escapes, and paths contained by a separately registered child root. Phase B does not expose dependency-source navigation.

- [ ] **Step 6: Protect the JSON protocol from incidental stdout**

Save protocol stdout first:

```python
protocol_stdout = sys.stdout
```

Wrap Serena initialization and every Serena operation in:

```python
with contextlib.redirect_stdout(sys.stderr):
    result = dispatch_semantic_operation(retriever, project, request)
```

Only `_write_protocol_message(protocol_stdout, message)` may write stdout.

- [ ] **Step 7: Implement Serena symbol normalization**

Use Serena objects, not parsing its human-facing tool strings.

Kind normalization:

```python
def _kind_name(value: object) -> str:
    return str(value).strip().lower().replace(" ", "_")
```

Position conversion:

```python
def _position(line0: int, column0: int) -> dict[str, int]:
    return {"line": line0 + 1, "column": column0 + 1}
```

For a `LanguageServerSymbol`, use `get_name_path()`, `.name`, `.location.relative_path`, `get_body_start_position()`, `get_body_end_position()`, and `.body` only when requested. Bound body UTF-8 to `MAX_SYMBOL_BODY_BYTES = 32 * 1024` without splitting an invalid UTF-8 sequence.

`list_symbols` uses `retriever.get_symbol_overview(path)` and recursively normalizes descendants to requested depth with one shared `max_results` node budget.

`find_symbol` uses:

```python
retriever.find(
    request.query,
    within_relative_path=request.path or None,
)
```

Slice to `max_results`; set `truncated=True` when more were found.

- [ ] **Step 8: Implement definition and reference position semantics**

Validate `line >= 1`, `column >= 1` before subtracting one.

Definition:

```python
symbol = retriever.find_declaration(
    request.path,
    request.line - 1,
    request.column - 1,
    include_body=False,
)
```

No symbol -> `SEMANTIC_SYMBOL_NOT_FOUND`.

References first resolve the definition at the public position, then:

```python
references = retriever.find_referencing_symbols_by_location(symbol.location)
```

Each `ReferenceInLanguageServerSymbol` gives the containing symbol plus exact `line` / `character`; normalize the reference start to 1-based and use a conservative one-character end position when Serena does not expose token length. When `include_declaration=true`, prepend the resolved declaration as a reference item only if it is not already represented.

- [ ] **Step 9: Map worker exceptions conservatively**

Before semantic file operations call `retriever.can_analyze_file(path)`. False -> `SEMANTIC_FILE_UNSUPPORTED`.

If no languages were autodetected, return `SEMANTIC_LANGUAGE_UNSUPPORTED`.

Map known input/lookup errors to non-retryable semantic errors. Unexpected Serena/LSP exceptions become `SEMANTIC_BACKEND_ERROR` with a bounded generic message and an internal stderr traceback; do not send traceback text to parent result details.

Always run `project.shutdown(timeout=2.0)` in worker `finally` when startup reached project creation.

- [ ] **Step 10: Run real Serena Python + TypeScript integration**

```bash
uv run --locked --extra dev --extra semantic python -m unittest tests.extensions.test_semantic_serena_integration -v
uv run --locked --extra dev --extra semantic python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_serena_integration.py
uv run --locked --extra dev --extra semantic python -m mypy coding_tools_mcp/extensions/semantic
```

Expected: both language fixtures pass; fixture roots contain no `.serena` after tests.

- [ ] **Step 11: Commit Task 4**

```bash
git add pyproject.toml uv.lock coding_tools_mcp/extensions/semantic tests/extensions/fixtures tests/extensions/test_semantic_serena_integration.py
git commit -m "feat: add Serena semantic worker"
```

---

### Task 5: Bounded Worker Lifecycle, Idle Eviction, Concurrency, and Failure Isolation

**Files:**
- Modify: `coding_tools_mcp/extensions/semantic/serena.py`
- Modify: `coding_tools_mcp/extensions/semantic/extension.py`
- Modify: `tests/extensions/test_semantic_serena_backend.py`
- Create: `tests/extensions/test_semantic_concurrency.py`

**Interfaces:**
- `SerenaSemanticBackend` owns lazy worker state and fulfills `SemanticBackend.close_project/close`.
- One worker lock serializes same-project calls; manager lock never surrounds worker request execution.

- [ ] **Step 1: Write RED bounded-lifecycle tests using fake child workers**

Cover exact semantics:

```python
def test_first_request_lazily_creates_only_selected_project_worker(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    self.assertEqual(self.factory.created_project_ids, ["alpha"])

def test_second_request_reuses_same_project_worker(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    backend.find_symbol(self.alpha, FindSymbolRequest(query="A"))
    self.assertEqual(self.factory.created_project_ids, ["alpha"])

def test_idle_timeout_reaps_expired_worker_on_next_bookkeeping_pass(self) -> None:
    clock = FakeClock(100.0)
    backend = self.backend(clock=clock, semantic_idle_timeout_seconds=10)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    alpha_worker = self.factory.workers["alpha"]
    clock.advance(11.0)
    backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
    self.assertTrue(alpha_worker.closed)

def test_limit_evicts_lru_idle_worker_before_starting_new_project(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
    alpha_worker = self.factory.workers["alpha"]
    backend.list_symbols(self.gamma, ListSymbolsRequest(path="c.py"))
    self.assertTrue(alpha_worker.closed)
    self.assertEqual(set(backend.active_project_ids()), {"beta", "gamma"})

def test_limit_never_evicts_worker_with_in_flight_request(self) -> None:
    backend = self.backend(max_semantic_projects=1)
    self.factory.block_project("alpha")
    thread = threading.Thread(
        target=lambda: backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    )
    thread.start()
    self.factory.wait_until_in_flight("alpha")
    with self.assertRaises(SemanticBackendError) as raised:
        backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
    self.assertEqual(raised.exception.code, SEMANTIC_BACKEND_UNAVAILABLE)
    self.factory.release_project("alpha")
    thread.join(5)
    self.assertFalse(thread.is_alive())

def test_close_project_only_closes_one_worker(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
    backend.close_project("alpha")
    self.assertTrue(self.factory.workers["alpha"].closed)
    self.assertFalse(self.factory.workers["beta"].closed)

def test_close_attempts_every_worker_and_returns_bounded_warnings(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
    backend.list_symbols(self.beta, ListSymbolsRequest(path="b.py"))
    self.factory.workers["alpha"].close_error = RuntimeError("alpha close failed")
    warnings = backend.close()
    self.assertIn("alpha close failed", "\n".join(warnings))
    self.assertTrue(self.factory.workers["beta"].closed)
```

Inject a worker factory into `SerenaSemanticBackend` for these tests; production uses `_SerenaWorker`.

- [ ] **Step 2: Implement worker record and eviction algorithm**

```python
@dataclass
class _WorkerRecord:
    worker: _SerenaWorker
    last_used: float
    in_flight: int = 0
```

`_acquire_worker(project)` under manager lock:

1. reap expired idle records;
2. reuse existing record if present;
3. if at limit, choose `min(idle_records, key=last_used)`; if none, raise retryable `SEMANTIC_BACKEND_UNAVAILABLE` with message `semantic worker capacity is busy`;
4. remove chosen idle record from map and close it outside lock;
5. create/start the new worker;
6. insert it only after successful ready handshake.

For request execution, increment `in_flight` under manager lock, release manager lock, call worker under worker-local lock, then decrement/update `last_used` in `finally`.

- [ ] **Step 3: Write RED cross-project concurrency tests**

Use fake workers whose request blocks on per-project events. Assert:

```python
def test_two_different_projects_can_be_in_flight_simultaneously(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    self.factory.block_project("alpha")
    self.factory.block_project("beta")
    threads = [
        threading.Thread(
            target=lambda project=project, path=path: backend.list_symbols(
                project,
                ListSymbolsRequest(path=path),
            )
        )
        for project, path in ((self.alpha, "a.py"), (self.beta, "b.py"))
    ]
    for thread in threads:
        thread.start()
    self.factory.wait_until_in_flight("alpha")
    self.factory.wait_until_in_flight("beta")
    self.assertEqual(self.factory.max_global_in_flight, 2)
    self.factory.release_project("alpha")
    self.factory.release_project("beta")
    for thread in threads:
        thread.join(5)

def test_two_same_project_requests_do_not_overlap_worker_request_body(self) -> None:
    backend = self.backend(max_semantic_projects=2)
    self.factory.block_first_request("alpha")
    threads = [
        threading.Thread(
            target=lambda: backend.list_symbols(self.alpha, ListSymbolsRequest(path="a.py"))
        )
        for _index in range(2)
    ]
    for thread in threads:
        thread.start()
    self.factory.wait_until_in_flight("alpha")
    time.sleep(0.05)
    self.assertEqual(self.factory.max_in_flight_for("alpha"), 1)
    self.factory.release_project("alpha")
    for thread in threads:
        thread.join(5)
```

The first test fails if any global request lock is introduced.

- [ ] **Step 4: Implement concurrency semantics and crash restart**

If worker request raises a crash/protocol/start error, remove that exact worker record before returning the typed error. A subsequent request for the same project may create a fresh worker.

Do not remove healthy workers for unrelated projects.

- [ ] **Step 5: Verify normal filesystem/Git tools survive semantic worker failure**

Create a Runtime with semantic fake backend configured to fail one project's request. Use a real temporary Git project and assert:

```python
semantic = runtime.call_tool(
    "find_symbol",
    {"project_id": "alpha", "query": "Anything"},
)
self.assertTrue(semantic["isError"])
self.assertEqual(
    semantic["structuredContent"]["error"]["code"],
    SEMANTIC_BACKEND_ERROR,
)
read = runtime.call_tool(
    "read_file",
    {"project_id": "alpha", "path": "sample.py"},
)
status = runtime.call_tool("git_status", {"project_id": "alpha"})
self.assertFalse(read["isError"])
self.assertFalse(status["isError"])
```

- [ ] **Step 6: Run lifecycle/concurrency suites**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_semantic_serena_backend \
  tests.extensions.test_semantic_concurrency \
  tests.extensions.test_project_tool_routing \
  tests.extensions.test_project_command_routing -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions/test_semantic_serena_backend.py tests/extensions/test_semantic_concurrency.py
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions/semantic
```

- [ ] **Step 7: Commit Task 5**

```bash
git add coding_tools_mcp/extensions/semantic tests/extensions
git commit -m "feat: bound semantic worker lifecycle"
```

---

### Task 6: Real MCP Semantic Integration and Optional-Backend Startup Behavior

**Files:**
- Create: `tests/extensions/test_semantic_mcp_integration.py`
- Modify: `tests/extensions/test_project_addressing_integration.py` only for shared fixture helpers if duplication is otherwise unavoidable

**Interfaces:**
- Proves semantic behavior through actual stdio/HTTP MCP transport rather than direct backend calls.
- Proves catalog is frozen from startup availability/configuration.

- [ ] **Step 1: Write real-server integration tests**

With `serena-agent==1.5.3` installed and config:

```toml
config_version = 1

[extensions]
enabled = ["projects", "semantic"]

[extensions.projects.registry.alpha]
root = "/temporary/alpha"

[extensions.projects.registry.beta]
root = "/temporary/beta"

[extensions.semantic]
backend = "serena"
max_semantic_projects = 2
semantic_idle_timeout_seconds = 900
semantic_start_timeout_seconds = 60
semantic_request_timeout_seconds = 60
allow_dependency_install = true
```

Tests:

```python
def test_stdio_semantic_catalog_is_28_tools_when_backend_available_at_startup(self) -> None:
    with semantic_stdio_server(self.config_path) as client:
        tools = client.list_tools()
        self.assertEqual(len(tools), 28)
        self.assertTrue({"list_symbols", "find_symbol", "find_definition", "find_references"} <= set(tools))

def test_semantic_schemas_are_coding_tools_owned_and_require_project_id(self) -> None:
    with semantic_stdio_server(self.config_path) as client:
        tools = client.tool_definitions()
        for name in ("list_symbols", "find_symbol", "find_definition", "find_references"):
            self.assertIn("project_id", tools[name]["inputSchema"]["required"])
            self.assertNotIn("name_path_pattern", tools[name]["inputSchema"].get("properties", {}))

def test_two_project_find_symbol_requests_do_not_cross_contaminate(self) -> None:
    with semantic_http_server(self.config_path) as client:
        alpha = structured(client.call_tool("find_symbol", {"project_id": "alpha", "query": "SharedName"}))
        beta = structured(client.call_tool("find_symbol", {"project_id": "beta", "query": "SharedName"}))
        self.assertEqual({item["path"] for item in alpha["symbols"]}, {"alpha.py"})
        self.assertEqual({item["path"] for item in beta["symbols"]}, {"beta.py"})

def test_definition_and_references_are_stateless_across_requests(self) -> None:
    with semantic_stdio_server(self.config_path) as client:
        definition = structured(client.call_tool("find_definition", self.alpha_position))
        client.call_tool("find_symbol", {"project_id": "beta", "query": "SharedName"})
        references = structured(client.call_tool("find_references", self.alpha_position))
        self.assertTrue(definition["definitions"])
        self.assertTrue(references["references"])
        self.assertTrue(all(item["path"] == "alpha.py" for item in references["references"]))

def test_semantic_error_does_not_remove_tools_from_catalog(self) -> None:
    with semantic_stdio_server(self.config_path) as client:
        before = set(client.list_tools())
        bad = client.call_tool("find_definition", {"project_id": "alpha", "path": "alpha.py", "line": 9999, "column": 1})
        self.assertTrue(bad.get("isError"))
        self.assertEqual(set(client.list_tools()), before)

def test_filesystem_tool_remains_usable_after_semantic_error(self) -> None:
    with semantic_stdio_server(self.config_path) as client:
        client.call_tool("find_symbol", {"project_id": "alpha", "query": "DefinitelyMissing"})
        read = client.call_tool("read_file", {"project_id": "alpha", "path": "alpha.py"})
        self.assertFalse(read.get("isError"))
```

Use same symbol name in alpha/beta with distinct files/bodies to detect contamination.

- [ ] **Step 2: Add unavailable-backend startup test without semantic extra**

Unit-test `SemanticExtension` with an injected unavailable availability probe: semantic extension enabled, no semantic tools contributed, server still starts, metadata reports `available=false`. This test does not depend on uninstalling local packages.

- [ ] **Step 3: Run real MCP integration with semantic extra**

```bash
uv run --locked --extra dev --extra semantic python -m unittest \
  tests.extensions.test_semantic_mcp_integration \
  tests.extensions.test_semantic_serena_integration -v
```

- [ ] **Step 4: Re-run the default no-semantic contract**

```bash
uv run --locked --extra dev python -m unittest \
  tests.compliance.test_mcp_contract \
  tests.compliance.test_schema_drift \
  tests.extensions.test_project_addressing_integration -v
```

Expected: ordinary default composition remains 24 tools and requires no Serena installation.

- [ ] **Step 5: Commit Task 6**

```bash
git add tests/extensions
git commit -m "test: prove semantic MCP isolation"
```

---

### Task 7: CI Gate for the Exact Serena Adapter

**Files:**
- Modify: `.github/workflows/compliance.yml`
- Modify: `tests/compliance/test_docs_required.py`

**Interfaces:**
- Default compliance job remains backend-independent.
- New Ubuntu job installs the exact semantic extra and proves Python + TypeScript adapter behavior.

- [ ] **Step 1: Add a dedicated `semantic-integration` CI job**

Use:

```yaml
  semantic-integration:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Check out repository
        uses: actions/checkout@v6.0.2
      - name: Set up Python
        uses: actions/setup-python@v6.2.0
        with:
          python-version: "3.11"
      - name: Set up Node
        uses: actions/setup-node@v6
        with:
          node-version: "22"
      - name: Install runtime, tests, and pinned semantic backend
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev,semantic]"
      - name: Run Serena semantic adapter integration
        run: |
          python -m unittest \
            tests.extensions.test_semantic_serena_integration \
            tests.extensions.test_semantic_mcp_integration -v
```

Do not add Serena to the Windows smoke or the default package dependencies.

- [ ] **Step 2: Extend required-doc/CI assertions**

`test_docs_required.py` must assert the workflow contains `semantic-integration` and the pinned semantic test modules so accidental CI removal fails locally.

- [ ] **Step 3: Validate the workflow and default gates locally**

```bash
uv run --locked --extra dev python -m unittest tests.compliance.test_docs_required -v
uv run --locked --extra dev python -m ruff check tests/compliance/test_docs_required.py
```

- [ ] **Step 4: Commit Task 7**

```bash
git add .github/workflows/compliance.yml tests/compliance/test_docs_required.py
git commit -m "ci: verify pinned Serena integration"
```

---

### Task 8: Document Optional Semantic Composition and Contract Drift Gates

**Files:**
- Modify: `docs/runtime-contract-v0.4.md`
- Modify: `docs/tools-and-schemas.md`
- Modify: `docs/extensions.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/ci-and-tests.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Create: `tests/extensions/test_semantic_contract_docs.py`
- Modify: `docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md`

**Interfaces:**
- v0.4 continues to define the 24-tool default composition.
- The same contract documents the optional semantic composition: 28 tools only when `semantic` is enabled and Serena 1.5.3 is available at startup.
- Dedicated semantic doc-drift test validates the four optional tool names/properties/annotations without requiring Serena installed.

- [ ] **Step 1: Write RED semantic contract-doc drift test using a fake available backend**

Build the semantic tool contributions through `SemanticExtension` with a fake backend, compose them with the core/project catalog, and assert the contract contains:

```text
list_symbols
find_symbol
find_definition
find_references
project_id
query
include_body
include_declaration
max_results
SEMANTIC_BACKEND_UNAVAILABLE
SEMANTIC_PROJECT_START_FAILED
SEMANTIC_LANGUAGE_UNSUPPORTED
SEMANTIC_FILE_UNSUPPORTED
SEMANTIC_SYMBOL_NOT_FOUND
SEMANTIC_POSITION_INVALID
SEMANTIC_TIMEOUT
SEMANTIC_BACKEND_ERROR
```

Also compare each optional tool's live semantic schema property names and annotation values with the doc.

- [ ] **Step 2: Update v0.4 contract with an explicit optional semantic section**

State exactly:

```text
default projects-only composition: 24 tools
projects + semantic + supported Serena available at startup: 28 tools
semantic enabled but supported backend unavailable at startup: semantic tools absent; process still starts
runtime worker failure after composition: semantic tools remain and return typed failures
```

Document one-worker-per-project and why a shared Serena ProjectServer is rejected for 1.5.3.

- [ ] **Step 3: Update operator docs**

Installation:

```bash
uv sync --extra semantic
```

Enablement:

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

Document that Serena is exact-pinned, state lives outside project roots, semantic operations do not mutate source files, and missing language-server runtime dependencies are offline by default. Operators who intentionally want SolidLSP to bootstrap missing `uvx`/npm dependencies must set `allow_dependency_install = true` in host-local configuration.

- [ ] **Step 4: Mark Phase B implemented pending final acceptance in the design spec**

Do not mark verified yet. Use status equivalent to:

```text
Phase 0 and Phase A implemented/verified; Phase B implemented, final semantic acceptance in progress.
```

- [ ] **Step 5: Run docs/schema/hygiene gates**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_semantic_contract_docs \
  tests.compliance.test_schema_drift \
  tests.compliance.test_docs_required \
  tests.test_public_fork_hygiene -v
git diff --check
```

- [ ] **Step 6: Commit Task 8**

```bash
git add README.md README.zh-CN.md docs tests/extensions/test_semantic_contract_docs.py
git commit -m "docs: define optional semantic runtime contract"
```

---

### Task 9: Phase B Full Acceptance and Final Checkpoint

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-project-addressing-semantic-navigation-design.md`
- Generated compliance reports are not committed if they pick up machine-specific paths; restore them after gates.

**Interfaces:**
- Proves default installation remains Serena-independent.
- Proves exact-pinned semantic installation works for Python + TypeScript and concurrent projects.
- Leaves `main` clean and local; no push.

- [ ] **Step 1: Reconfirm upstream before final acceptance**

```bash
git ls-remote https://github.com/xyTom/coding-tools-mcp.git refs/heads/main
git rev-parse xyTom/main
git rev-list --left-right --count xyTom/main...HEAD
```

If upstream moved, use the established `sync/upstream-main` lane and rerun bridge/semantic tests after resolving conflicts. Do not force-push.

- [ ] **Step 2: Run all semantic unit tests without Serena installed requirement**

```bash
uv run --locked --extra dev python -m unittest discover -s tests/extensions -p 'test_semantic*.py' -v
```

Real Serena tests may skip explicitly when the semantic extra is absent; fake-backend/protocol/lifecycle tests must not skip.

- [ ] **Step 3: Run exact Serena integration**

```bash
uv run --locked --extra dev --extra semantic python -m unittest \
  tests.extensions.test_semantic_serena_integration \
  tests.extensions.test_semantic_mcp_integration -v
```

Expected: Python and TypeScript fixtures green; two-project isolation green; no `.serena` written to fixtures.

- [ ] **Step 4: Run default fork acceptance**

```bash
mise run verify
uv run --locked --extra dev make test-protocol test-schema-drift check-dispatch-inputs test-integration check-npm-launcher
uv run --locked --extra dev make compliance
```

Restore generated reports afterward if changed:

```bash
git restore -- reports/compliance/latest.json reports/compliance/latest.md
```

- [ ] **Step 5: Run semantic/static/privacy gates**

```bash
uv run --locked --extra dev --extra semantic python -m ruff check coding_tools_mcp/extensions/semantic tests/extensions
uv run --locked --extra dev --extra semantic python -m mypy coding_tools_mcp/extensions/semantic
uv run --locked --extra dev python -m unittest tests.test_public_fork_hygiene -v
git diff --check
```

Scan changed tracked files for host-specific markers before staging.

- [ ] **Step 6: Mark Phase B verified with actual gate counts/results**

Only after all commands above are green, update the design status to:

```text
Phase 0, Phase A, and Phase B implemented and verified.
```

Record the exact Serena version and Python/TypeScript integration evidence without writing local paths.

- [ ] **Step 7: Commit final Phase B acceptance checkpoint**

Use the public noreply identity and commit only intentional source/tests/docs:

```bash
git add coding_tools_mcp pyproject.toml coding-tools.toml tests .github docs README.md README.zh-CN.md
git diff --cached --check
git -c user.name=Yuzu02 \
    -c user.email=57969791+Yuzu02@users.noreply.github.com \
    commit -m "feat: complete semantic navigation"
```

- [ ] **Step 8: Fresh post-commit verification**

```bash
git status --short --branch
git log -1 --format='%H%n%s%n%an <%ae>'
uv run --locked --extra dev --extra semantic python -m unittest \
  tests.extensions.test_semantic_mcp_integration \
  tests.extensions.test_semantic_serena_integration \
  tests.extensions.test_semantic_concurrency \
  tests.extensions.test_semantic_contract_docs \
  tests.test_public_fork_hygiene -v
```

Expected: clean `main`, noreply author, all selected acceptance tests green. Do not push.

---

## Spec Coverage Checklist

| Approved semantic requirement | Plan coverage |
| --- | --- |
| Backend-neutral `SemanticBackend` | Task 1 |
| `list_symbols` | Tasks 2, 4, 6 |
| `find_symbol` | Tasks 2, 4, 6 |
| `find_definition` 1-based position | Tasks 1, 2, 4 |
| `find_references` 1-based position | Tasks 1, 2, 4 |
| Read-only semantic tools | Task 2 |
| Stable `project_id` on every semantic request | Tasks 2, 6 |
| No mutable Serena current project | Tasks 3-5 |
| Cross-project isolation | one worker/project in Tasks 3-6 |
| No shared Serena ProjectServer | Global constraint + Tasks 3-4 |
| Exact version pin | Tasks 3-4, 7 |
| Lazy workers | Tasks 3, 5 |
| `max_semantic_projects` | Tasks 2, 5 |
| idle/start/request timeout controls | Tasks 2, 3, 5 |
| Evict only idle worker | Task 5 |
| Same-project serialization / cross-project concurrency | Task 5 |
| Typed semantic errors | Tasks 1-4 |
| No lexical masquerading | Task 2 error mapping/docs Task 8 |
| Worker failure does not affect normal tools | Tasks 5-6 |
| Backend can be absent from default install | Tasks 2, 4, 6 |
| Catalog frozen by startup availability | Tasks 2, 6 |
| No `.serena` mutations in user project | Tasks 4, 6 |
| Nested registered project boundary | Tasks 3-4 |
| Python + second-language real integration | TypeScript in Tasks 4, 7, 9 |
| Public/private hygiene | Tasks 8-9 |
| Upstream-sync revalidation | Task 9 |

Phase B does not add semantic editing, rename, Serena memories, diagnostics, cross-project references in one request, dependency-source navigation contracts, or an owned persistent semantic index format.
