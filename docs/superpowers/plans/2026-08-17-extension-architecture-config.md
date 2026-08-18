# Extension Architecture + TOML Configuration Implementation Plan

**Status:** HISTORICAL EXECUTION PLAN — Phase 0 is IMPLEMENTED + VERIFIED.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 0 internal extension host and layered TOML configuration foundation that isolates fork-owned capabilities from the `xyTom/coding-tools-mcp` mother core, then prove the boundary by moving the existing project/skill discovery capability behind the first internal `projects` extension.

**Design:** [`../specs/2026-08-17-extension-architecture-config-design.md`](../specs/2026-08-17-extension-architecture-config-design.md). The later [`../specs/2026-08-16-project-addressing-semantic-navigation-design.md`](../specs/2026-08-16-project-addressing-semantic-navigation-design.md) consumes this Phase 0 foundation and is explicitly out of implementation scope here.

**Architecture:** Keep the upstream-facing integration surface small: `Runtime` supplies mother-core tools/services to an `ExtensionHost`, the host composes a frozen tool catalog from static internal extensions, and dispatch/listing use that composed catalog. Configuration is parsed once at startup from built-in defaults → `coding-tools.toml` → `coding-tools.local.toml` → supported environment override → explicit CLI override. The first real extension migrates `ProjectCatalog`, `SkillCatalog`, `list_skills`, and `read_skill` without implementing multi-project routing yet.

**Tech Stack:** Python 3.11+, stdlib `tomllib`, `dataclasses`, `typing.Protocol`, `MappingProxyType`, existing `unittest` suite, existing MCP server/runtime, Ruff, mypy, Mise/uv, Git.

## Global Constraints

- V1 extensions are internal Python modules registered statically by the fork; do not load arbitrary modules, filesystem paths, package entry points, or user-authored code.
- TOML selects/configures registered extensions only; configuration never imports or executes Python.
- `config_version = 1` is mandatory in every present TOML config file.
- Configuration precedence is: built-in fork defaults → `coding-tools.toml` → `coding-tools.local.toml` → supported environment override → explicit CLI override.
- `extensions.enabled` is the only TOML activation source; extension tables configure extensions but do not contain another `enabled` flag.
- Lists replace lower-precedence lists as a whole unless a field explicitly declares another policy; tables merge only through declared schema nodes.
- Unknown root keys, unknown extension names/tables, invalid types, mismatched config versions, dependency cycles, missing enabled dependencies, tool collisions, invalid decorator targets, and duplicate capabilities fail startup.
- Dependencies are explicit and do not auto-enable transitively.
- Registries and the composed tool catalog freeze before extension `start()` and before MCP transport accepts requests.
- `start()` may mutate already-published service objects but may not register new tools, decorators, metadata, services, or schemas after freeze.
- Extensions communicate through public capability protocols/keys; no extension imports another extension's private implementation.
- No monkey-patching, runtime `setattr`, or extension-specific direct mutation of mother-core `TOOL_REGISTRY`.
- Tool decoration is explicit and deterministic; V1 schema decoration is additive only, which is sufficient for future `project_id` injection and avoids ambiguous arbitrary schema rewrites.
- Extension-owned tools also own any specialized model-facing text renderer; extraction must not leave feature-specific renderers behind in mother-core `tool_results.py`.
- `apply_patch` remains the structured direct-edit primitive; canonical `exec_command` workflows may mutate the workspace under existing runtime policy.
- `dangerous` retains its current meaning; this phase does not redefine it as tenant isolation.
- `coding-tools.local.toml`, host paths, credentials, tunnel identifiers, effective units, and runtime state must remain untracked.
- The services launcher remains deployment/composition infrastructure, not an extension, and must not duplicate extension config parsing.
- Default externally visible behavior after Phase 0D remains the current 22-tool fork behavior by enabling `projects` in built-in/public defaults; explicitly disabling extensions is a fork-specific composition choice.
- Do not implement multi-project routing, stable configured `project_id`, Serena, semantic tools, Work Items, hooks, gateway adapters, or external plugin discovery in this plan.
- Commits are local checkpoints only; do not push, merge, tag, or create a PR unless explicitly requested.

---

## File/Responsibility Map

Create the extension foundation as focused modules:

```text
coding_tools_mcp/extensions/
    __init__.py          built-in registry factory + public extension API exports
    config.py            TOML schema nodes, layered merge, path selection, RuntimeConfig
    api.py               ExtensionManifest, Extension Protocol, ExtensionContext
    registry.py          static extension registry + dependency/topological resolution
    services.py          CapabilityKey, ServiceRegistry, core WorkspaceAccess capability
    contributions.py     tool/metadata contributions, decorators, composition/freeze rules
    host.py              configure/register/freeze/start/stop orchestration

    projects/
        __init__.py      public exports for the projects extension
        extension.py     ProjectsExtension + list_skills/read_skill contributions
        project_catalog.py
        skill_catalog.py
```

Existing mother-core/fork bridge surfaces to modify:

```text
coding_tools_mcp/server.py
    ToolSpec / TOOL_REGISTRY                 keep as mother-core source definitions
    Runtime.__init__                         seed core services + construct ExtensionHost
    Runtime.list_tools / call_tool           consume frozen composed tools
    Runtime.server_info_payload              expose bounded extension metadata
    Runtime.close                            stop extensions before core runtime teardown
    validate_arguments / tool_definition     operate on composed tool contracts
    build_runtime / build_parser             load config before runtime/transport startup

coding_tools_mcp/tool_results.py
    make_tool_result / render_tool_text      generic optional renderer override seam only

coding_tools_mcp/project_context.py
    remain mother-core-compatible instruction loading; do not move in Phase 0D
```

Repository/config/docs surfaces:

```text
coding-tools.toml                             committed public composition/defaults
.gitignore                                   ignore coding-tools.local.toml
tests/test_public_fork_hygiene.py             enforce local-config privacy
tests/extensions/*.py                         focused Phase 0 architecture coverage
tests/test_project_catalog.py                 import moved project module
tests/test_project_skills_integration.py      import moved project module
tests/test_project_skills_runtime.py          default/disabled extension behavior
docs/quickstart.md                            operator config usage
docs/tools-and-schemas.md                     composed catalog behavior
docs/services-launcher.md                     launcher/config ownership boundary
```

Do **not** move `coding_tools_mcp/project_context.py` in this phase. It exists upstream and its current fork refinements are generic workspace-instruction behavior. Only `project_catalog.py`, `skill_catalog.py`, and their tool handlers are the first extension extraction.

---

### Task 1: Layered TOML Configuration Foundation

**Files:**
- Create: `coding_tools_mcp/extensions/__init__.py`
- Create: `coding_tools_mcp/extensions/config.py`
- Create: `tests/extensions/__init__.py`
- Create: `tests/extensions/test_config_layers.py`
- Create: `tests/extensions/test_config_validation.py`
- Create: `coding-tools.toml`
- Modify: `.gitignore:1-41`
- Modify: `tests/test_public_fork_hygiene.py:14-68`

**Interfaces:**
- Produces: `ConfigNode`, `scalar()`, `list_of()`, `table()`, `map_of()`, `RuntimeConfig`, `ConfigError`, `parse_extension_list()`, `resolve_config_paths()`, `load_runtime_config()`.
- Consumes later: a mapping `{extension_name: ConfigNode}` supplied by the static extension registry.
- V1 supported override fields: config path, local-config path, and complete `extensions.enabled` replacement through `CODING_TOOLS_MCP_EXTENSIONS` / `--extensions`.

- [ ] **Step 1: Write failing tests for schema-aware layer precedence**

Create `tests/extensions/test_config_layers.py` with fixtures using temporary directories. The important tests are:

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from coding_tools_mcp.extensions.config import (
    load_runtime_config,
    scalar,
    table,
)


class ConfigLayerTests(unittest.TestCase):
    def test_local_overlay_replaces_scalar_without_copying_public_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                """config_version = 1
[extensions]
enabled = ["fake"]
[extensions.fake]
backend = "public"
lazy = true
""",
                encoding="utf-8",
            )
            (root / "coding-tools.local.toml").write_text(
                """config_version = 1
[extensions.fake]
backend = "local"
""",
                encoding="utf-8",
            )
            schema = {"fake": table({"backend": scalar(str), "lazy": scalar(bool)})}

            config = load_runtime_config(
                cwd=root,
                extension_schemas=schema,
                default_enabled=(),
                environ={},
            )

            self.assertEqual(config.enabled_extensions, ("fake",))
            self.assertEqual(config.extension("fake"), {"backend": "local", "lazy": True})

    def test_environment_extensions_replace_toml_enabled_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                "config_version = 1\n[extensions]\nenabled = [\"a\"]\n",
                encoding="utf-8",
            )
            schemas = {"a": table({}), "b": table({})}
            config = load_runtime_config(
                cwd=root,
                extension_schemas=schemas,
                default_enabled=(),
                environ={"CODING_TOOLS_MCP_EXTENSIONS": "b"},
            )
            self.assertEqual(config.enabled_extensions, ("b",))

    def test_cli_extensions_override_environment(self) -> None:
        config = load_runtime_config(
            cwd=Path.cwd(),
            extension_schemas={"a": table({}), "b": table({})},
            default_enabled=("a",),
            environ={"CODING_TOOLS_MCP_EXTENSIONS": "a"},
            cli_extensions=("b",),
            public_path=False,
            local_path=False,
        )
        self.assertEqual(config.enabled_extensions, ("b",))
```

Use the sentinel `False` for `public_path` / `local_path` to mean “explicitly disabled config file”; `None` means “perform default path discovery”. This keeps tests deterministic and avoids conflating “no override supplied” with “do not read a file”.

- [ ] **Step 2: Write failing strict-validation tests**

Create `tests/extensions/test_config_validation.py` covering exact failure classes/messages. Use complete temporary-file fixtures rather than helper calls with implicit state:

```python
from coding_tools_mcp.extensions.config import ConfigError, list_of, load_runtime_config, scalar, table


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class ConfigValidationTests(unittest.TestCase):
    def test_unknown_root_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", "config_version = 1\ntypo = true\n")
            with self.assertRaisesRegex(ConfigError, "unknown configuration key: config.typo"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_unknown_extension_table_fails_even_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                "config_version = 1\n[extensions]\nenabled = []\n[extensions.missing]\n",
            )
            with self.assertRaisesRegex(ConfigError, "unknown extension: missing"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({})},
                    default_enabled=(),
                    environ={},
                )

    def test_public_and_local_versions_must_match_supported_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "coding-tools.toml", "config_version = 1\n[extensions]\nenabled = []\n")
            write(root / "coding-tools.local.toml", "config_version = 2\n[extensions]\nenabled = []\n")
            with self.assertRaisesRegex(ConfigError, "config_version"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={},
                    default_enabled=(),
                    environ={},
                )

    def test_list_replaces_instead_of_concatenating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\npaths = ["public-a", "public-b"]\n',
            )
            write(
                root / "coding-tools.local.toml",
                'config_version = 1\n[extensions.fake]\npaths = ["local-only"]\n',
            )
            config = load_runtime_config(
                cwd=root,
                extension_schemas={"fake": table({"paths": list_of(scalar(str))})},
                default_enabled=(),
                environ={},
            )
            self.assertEqual(config.extension("fake")["paths"], ("local-only",))

    def test_unknown_nested_key_does_not_participate_in_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "coding-tools.toml",
                'config_version = 1\n[extensions]\nenabled = ["fake"]\n[extensions.fake]\nbackend = "ok"\ntypo = true\n',
            )
            with self.assertRaisesRegex(ConfigError, "extensions.fake.typo"):
                load_runtime_config(
                    cwd=root,
                    extension_schemas={"fake": table({"backend": scalar(str)})},
                    default_enabled=(),
                    environ={},
                )
```

Also cover malformed TOML, non-integer `config_version`, duplicate enabled names, invalid extension grammar, and an enabled name with no registered schema.

- [ ] **Step 3: Run the config tests and verify they fail because the module does not exist**

Run:

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_config_layers \
  tests.extensions.test_config_validation -v
```

Expected: FAIL with import/module errors for `coding_tools_mcp.extensions.config`.

- [ ] **Step 4: Implement the configuration schema primitives and immutable normalized result**

Create `coding_tools_mcp/extensions/config.py` with this public shape:

```python
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Sequence, cast

from coding_tools_mcp.envutils import ENV_PREFIX


CONFIG_VERSION = 1
EXTENSION_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ConfigNode:
    kind: Literal["scalar", "list", "table", "map"]
    value_types: tuple[type[object], ...] = ()
    children: Mapping[str, "ConfigNode"] = field(default_factory=dict)
    item: "ConfigNode | None" = None


def scalar(*value_types: type[object]) -> ConfigNode:
    if not value_types:
        raise ValueError("scalar schema requires at least one Python type")
    return ConfigNode(kind="scalar", value_types=value_types)


def list_of(item: ConfigNode) -> ConfigNode:
    return ConfigNode(kind="list", item=item)


def table(children: Mapping[str, ConfigNode]) -> ConfigNode:
    return ConfigNode(kind="table", children=MappingProxyType(dict(children)))


def map_of(item: ConfigNode) -> ConfigNode:
    return ConfigNode(kind="map", item=item)


@dataclass(frozen=True)
class RuntimeConfig:
    config_version: int
    enabled_extensions: tuple[str, ...]
    extension_settings: Mapping[str, Mapping[str, object]]
    sources: tuple[Path, ...]

    @classmethod
    def defaults(
        cls,
        *,
        enabled: Sequence[str],
        settings: Mapping[str, Mapping[str, object]] | None = None,
    ) -> "RuntimeConfig":
        frozen_settings = {
            name: _freeze_mapping(value)
            for name, value in (settings or {}).items()
        }
        return cls(
            config_version=CONFIG_VERSION,
            enabled_extensions=tuple(enabled),
            extension_settings=MappingProxyType(frozen_settings),
            sources=(),
        )

    def extension(self, name: str) -> Mapping[str, object]:
        return self.extension_settings.get(name, MappingProxyType({}))
```

Use `MappingProxyType` recursively for normalized mappings returned to runtime code. Do not expose the mutable dictionaries produced by `tomllib`.

Define the freeze/clone helpers in the same module so every referenced helper exists in this task:

```python
def _freeze_value(value: object) -> object:
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _clone(value: object) -> object:
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value
```

Because frozen config lists become tuples, normalized config assertions use tuples rather than mutable lists.

- [ ] **Step 5: Implement deterministic config path discovery and schema-aware merge**

Implement:

```python
def resolve_config_paths(
    *,
    cwd: Path,
    environ: Mapping[str, str],
    public_path: Path | str | bool | None = None,
    local_path: Path | str | bool | None = None,
) -> tuple[Path | None, Path | None]:
    """Resolve config paths without upward-directory searching.

    Precedence for path selection itself:
    explicit argument -> ENV -> cwd default if file exists.
    False explicitly disables that file.
    """
```

Environment names:

```text
CODING_TOOLS_MCP_CONFIG
CODING_TOOLS_MCP_LOCAL_CONFIG
CODING_TOOLS_MCP_EXTENSIONS
```

Default file discovery checks only:

```text
<cwd>/coding-tools.toml
<directory containing selected public config>/coding-tools.local.toml
```

If no public config is selected, default local discovery checks `<cwd>/coding-tools.local.toml`. Do not search parents/home directories.

Implement schema-aware merge using the declared `ConfigNode` tree:

```python
_MISSING = object()


def _merge_node(base: object, overlay: object, schema: ConfigNode, path: str) -> object:
    if schema.kind in {"scalar", "list"}:
        _validate_node(overlay, schema, path)
        return _clone(overlay)

    if schema.kind == "table":
        if not isinstance(overlay, dict):
            raise ConfigError(f"{path} must be a table")
        result = {} if base is _MISSING else dict(cast(Mapping[str, object], base))
        for key, value in overlay.items():
            child = schema.children.get(key)
            if child is None:
                raise ConfigError(f"unknown configuration key: {path}.{key}")
            previous = result.get(key, _MISSING)
            result[key] = _merge_node(previous, value, child, f"{path}.{key}")
        return result

    if schema.kind == "map":
        if not isinstance(overlay, dict) or schema.item is None:
            raise ConfigError(f"{path} must be a table")
        result = {} if base is _MISSING else dict(cast(Mapping[str, object], base))
        for key, value in overlay.items():
            previous = result.get(key, _MISSING)
            result[key] = _merge_node(previous, value, schema.item, f"{path}.{key}")
        return result

    raise AssertionError(f"unsupported config schema kind: {schema.kind}")
```

Implement `_validate_node()` alongside this. For `scalar`, reject `bool` when the declared type is `int`; for `list`, validate each item with `schema.item`; for `table`, reject undeclared keys; for `map`, validate every value with the map item schema. `_clone()` should recursively copy dict/list values so no parsed TOML container is shared across layers.

Build the root schema dynamically from the supplied `extension_schemas`:

```python
root_schema = table(
    {
        "config_version": scalar(int),
        "extensions": table(
            {
                "enabled": list_of(scalar(str)),
                **extension_schemas,
            }
        ),
    }
)
```

Every present TOML file must independently contain `config_version = 1` before merging. This prevents a local file from silently inheriting a version it never declared.

Implement the schema validation/read helpers explicitly:

```python
def _validate_node(value: object, schema: ConfigNode, path: str) -> None:
    if schema.kind == "scalar":
        if type(value) not in schema.value_types:
            expected = ", ".join(item.__name__ for item in schema.value_types)
            raise ConfigError(f"{path} must be one of: {expected}")
        return
    if schema.kind == "list":
        if not isinstance(value, list) or schema.item is None:
            raise ConfigError(f"{path} must be a list")
        for index, item in enumerate(value):
            _validate_node(item, schema.item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a table")
    if schema.kind == "table":
        for key, item in value.items():
            child = schema.children.get(key)
            if child is None:
                raise ConfigError(f"unknown configuration key: {path}.{key}")
            _validate_node(item, child, f"{path}.{key}")
        return
    if schema.kind == "map" and schema.item is not None:
        for key, item in value.items():
            _validate_node(item, schema.item, f"{path}.{key}")
        return
    raise AssertionError(f"invalid config schema node: {schema.kind}")


def _runtime_schema(extension_schemas: Mapping[str, ConfigNode]) -> ConfigNode:
    return table(
        {
            "config_version": scalar(int),
            "extensions": table(
                {
                    "enabled": list_of(scalar(str)),
                    **extension_schemas,
                }
            ),
        }
    )


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc
    return cast(dict[str, object], value)


def _validate_known_extension_tables(
    data: Mapping[str, object],
    extension_schemas: Mapping[str, ConfigNode],
) -> None:
    raw_extensions = data.get("extensions", {})
    if not isinstance(raw_extensions, dict):
        return
    for key in raw_extensions:
        if key != "enabled" and key not in extension_schemas:
            raise ConfigError(f"unknown extension: {key}")


def _validate_enabled(
    values: Sequence[str],
    extension_schemas: Mapping[str, ConfigNode],
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for name in values:
        if EXTENSION_NAME_RE.fullmatch(name) is None:
            raise ConfigError(f"invalid extension name: {name}")
        if name in seen:
            raise ConfigError(f"duplicate enabled extension: {name}")
        if name not in extension_schemas:
            raise ConfigError(f"unknown extension: {name}")
        seen.add(name)
        result.append(name)
    return tuple(result)
```

Call `_validate_known_extension_tables(parsed, extension_schemas)` before `_merge_node()` for each TOML file.

- [ ] **Step 6: Implement `load_runtime_config()` and supported enabled-list overrides**

Use this signature:

```python
def load_runtime_config(
    *,
    cwd: Path,
    extension_schemas: Mapping[str, ConfigNode],
    default_enabled: Sequence[str],
    environ: Mapping[str, str] | None = None,
    public_path: Path | str | bool | None = None,
    local_path: Path | str | bool | None = None,
    cli_extensions: Sequence[str] | None = None,
) -> RuntimeConfig:
    env = os.environ if environ is None else environ
    public, local = resolve_config_paths(
        cwd=cwd,
        environ=env,
        public_path=public_path,
        local_path=local_path,
    )
    root_schema = _runtime_schema(extension_schemas)
    merged: object = {
        "config_version": CONFIG_VERSION,
        "extensions": {"enabled": list(default_enabled)},
    }
    sources: list[Path] = []
    for path in (public, local):
        if path is None:
            continue
        parsed = _read_toml(path)
        if parsed.get("config_version") != CONFIG_VERSION:
            raise ConfigError(f"{path}: config_version must be {CONFIG_VERSION}")
        _validate_known_extension_tables(parsed, extension_schemas)
        merged = _merge_node(merged, parsed, root_schema, "config")
        sources.append(path)

    data = cast(dict[str, object], merged)
    extensions = cast(dict[str, object], data["extensions"])
    env_override = env.get(f"{ENV_PREFIX}_EXTENSIONS")
    if env_override is not None:
        extensions["enabled"] = list(parse_extension_list(env_override))
    if cli_extensions is not None:
        extensions["enabled"] = list(_validate_enabled(cli_extensions, extension_schemas))

    _validate_node(data, root_schema, "config")
    enabled = _validate_enabled(cast(Sequence[str], extensions.get("enabled", ())), extension_schemas)
    settings = {
        name: _freeze_mapping(cast(Mapping[str, object], extensions.get(name, {})))
        for name in extension_schemas
    }
    return RuntimeConfig(
        config_version=CONFIG_VERSION,
        enabled_extensions=enabled,
        extension_settings=MappingProxyType(settings),
        sources=tuple(sources),
    )
```

Parse `CODING_TOOLS_MCP_EXTENSIONS` as a comma-separated full replacement list. Trim whitespace, reject duplicates/invalid names, and allow the empty string to mean “enable no extensions”. Apply `cli_extensions` last when it is not `None`.

Implement the parser explicitly and reuse the same name validator as `_validate_enabled`:

```python
def parse_extension_list(raw: str) -> tuple[str, ...]:
    if raw.strip() == "":
        return ()
    names = tuple(part.strip() for part in raw.split(","))
    if any(not name for name in names):
        raise ConfigError("extension list contains an empty name")
    seen: set[str] = set()
    for name in names:
        if EXTENSION_NAME_RE.fullmatch(name) is None:
            raise ConfigError(f"invalid extension name: {name}")
        if name in seen:
            raise ConfigError(f"duplicate enabled extension: {name}")
        seen.add(name)
    return names
```

- [ ] **Step 7: Add committed public config and protect local config**

Create initial `coding-tools.toml` with no feature extension activated yet; Task 8 will switch the default to `projects` when that extension exists:

```toml
config_version = 1

[extensions]
enabled = []
```

Add to `.gitignore`:

```gitignore
coding-tools.local.toml
```

Extend `test_host_specific_deployment_state_is_not_tracked()`:

```python
self.assertNotIn("coding-tools.local.toml", paths)
self.assertIn("coding-tools.local.toml", ignore_lines)
```

- [ ] **Step 8: Run focused config/hygiene tests**

Run:

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_config_layers \
  tests.extensions.test_config_validation \
  tests.test_public_fork_hygiene -v
```

Expected: PASS.

- [ ] **Step 9: Run Ruff on the new config surface**

Run:

```bash
uv run --locked --extra dev python -m ruff check \
  coding_tools_mcp/extensions \
  tests/extensions \
  tests/test_public_fork_hygiene.py
```

Expected: `All checks passed!`

- [ ] **Step 10: Commit Task 1**

```bash
git add \
  coding_tools_mcp/extensions/__init__.py \
  coding_tools_mcp/extensions/config.py \
  tests/extensions \
  coding-tools.toml \
  .gitignore \
  tests/test_public_fork_hygiene.py
git commit -m "feat: add layered extension config"
```

---

### Task 2: Extension Manifest, Static Registry, and Dependency Graph

**Files:**
- Create: `coding_tools_mcp/extensions/api.py`
- Create: `coding_tools_mcp/extensions/registry.py`
- Create: `tests/extensions/test_extension_registry.py`
- Create: `tests/extensions/test_extension_dependencies.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`

**Interfaces:**
- Consumes: `ConfigNode`, `table()` from Task 1.
- Produces: `ExtensionManifest`, `Extension`, `ExtensionContext` declaration, `ExtensionRegistry`, `resolve_extension_order()`.
- Later tasks will add concrete `services`/`contributions` fields to `ExtensionContext`; define the dataclass only when those types exist in Task 4 to avoid temporary `Any` APIs. In this task define only manifest/protocol/registry.

- [ ] **Step 1: Write failing registry tests**

Create fake extension classes directly inside `tests/extensions/test_extension_registry.py`:

```python
class Alpha:
    manifest = ExtensionManifest(name="alpha", description="alpha")
    def configure(self, config): pass
    def register(self, context): pass
    def start(self): pass
    def stop(self): pass


class ExtensionRegistryTests(unittest.TestCase):
    def test_registry_rejects_duplicate_manifest_names(self) -> None:
        with self.assertRaisesRegex(ExtensionRegistryError, "duplicate extension name: alpha"):
            ExtensionRegistry([Alpha, AnotherAlpha], default_enabled=())

    def test_registry_rejects_invalid_manifest_name(self) -> None:
        class InvalidName(Alpha):
            manifest = ExtensionManifest(name="not valid")

        with self.assertRaisesRegex(ExtensionRegistryError, "invalid extension name"):
            ExtensionRegistry([InvalidName], default_enabled=())

    def test_unknown_enabled_extension_is_configuration_error(self) -> None:
        registry = ExtensionRegistry([Alpha], default_enabled=())
        with self.assertRaisesRegex(ExtensionRegistryError, "unknown extension: missing"):
            registry.resolve_order(("missing",))
```

- [ ] **Step 2: Write failing dependency-order tests**

Use a small factory so every test has a complete Extension implementation without copy/paste:

```python
def fake_extension(name: str, requires: tuple[str, ...] = ()):
    class FakeExtension:
        manifest = ExtensionManifest(name=name, requires=requires)
        def configure(self, config):
            self.config = config
        def register(self, context):
            self.context = context
        def start(self):
            self.started = True
        def stop(self):
            self.started = False
    return FakeExtension


class ExtensionDependencyTests(unittest.TestCase):
    def test_dependency_order_is_stable_for_independent_extensions(self) -> None:
        alpha = fake_extension("alpha")
        beta = fake_extension("beta")
        registry = ExtensionRegistry([alpha, beta], default_enabled=())
        self.assertEqual(registry.resolve_order(("beta", "alpha")), ("beta", "alpha"))

    def test_required_extension_must_be_explicitly_enabled(self) -> None:
        projects = fake_extension("projects")
        semantic = fake_extension("semantic", ("projects",))
        registry = ExtensionRegistry([projects, semantic], default_enabled=())
        with self.assertRaisesRegex(
            ExtensionRegistryError,
            "semantic requires enabled extension projects",
        ):
            registry.resolve_order(("semantic",))

    def test_dependency_cycle_is_rejected_with_cycle_names(self) -> None:
        alpha = fake_extension("alpha", ("beta",))
        beta = fake_extension("beta", ("alpha",))
        registry = ExtensionRegistry([alpha, beta], default_enabled=())
        with self.assertRaisesRegex(ExtensionRegistryError, "dependency cycle"):
            registry.resolve_order(("alpha", "beta"))

    def test_duplicate_enabled_names_are_rejected(self) -> None:
        alpha = fake_extension("alpha")
        registry = ExtensionRegistry([alpha], default_enabled=())
        with self.assertRaisesRegex(ExtensionRegistryError, "duplicate enabled extension: alpha"):
            registry.resolve_order(("alpha", "alpha"))
```

For independent extensions, preserve the order supplied in `enabled`. For dependency edges, dependencies always precede dependents.

- [ ] **Step 3: Run the registry/dependency tests and verify red state**

Run:

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_extension_registry \
  tests.extensions.test_extension_dependencies -v
```

Expected: FAIL because `api.py` / `registry.py` do not exist.

- [ ] **Step 4: Implement `ExtensionManifest` and `Extension` Protocol**

Create `coding_tools_mcp/extensions/api.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .config import ConfigNode, table


@dataclass(frozen=True)
class ExtensionManifest:
    name: str
    requires: tuple[str, ...] = ()
    description: str = ""
    config_schema: ConfigNode = field(default_factory=lambda: table({}))


class Extension(Protocol):
    manifest: ExtensionManifest

    def configure(self, config: Mapping[str, object]) -> None:
        raise NotImplementedError

    def register(self, context: "ExtensionContext") -> None:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
```

Use the same extension-name grammar as config validation. Validate dependency names too.

- [ ] **Step 5: Implement static registry and deterministic topological ordering**

Create `coding_tools_mcp/extensions/registry.py` with:

```python
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from .api import Extension
from .config import ConfigNode, EXTENSION_NAME_RE


class ExtensionRegistryError(ValueError):
    pass


def _validate_extension_name(name: str) -> None:
    if EXTENSION_NAME_RE.fullmatch(name) is None:
        raise ExtensionRegistryError(f"invalid extension name: {name}")


def _validate_enabled_names(names: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        _validate_extension_name(name)
        if name in seen:
            raise ExtensionRegistryError(f"duplicate enabled extension: {name}")
        seen.add(name)
        result.append(name)
    return tuple(result)


class ExtensionRegistry:
    def __init__(
        self,
        extensions: Iterable[type[Extension]],
        *,
        default_enabled: Sequence[str],
    ) -> None:
        by_name: dict[str, type[Extension]] = {}
        for extension_type in extensions:
            manifest = extension_type.manifest
            _validate_extension_name(manifest.name)
            for dependency in manifest.requires:
                _validate_extension_name(dependency)
            if manifest.name in by_name:
                raise ExtensionRegistryError(f"duplicate extension name: {manifest.name}")
            by_name[manifest.name] = extension_type
        self._extensions = MappingProxyType(by_name)
        self._default_enabled = _validate_enabled_names(default_enabled)
        self.resolve_order(self._default_enabled)

    @property
    def default_enabled(self) -> tuple[str, ...]:
        return self._default_enabled

    def schemas(self) -> Mapping[str, ConfigNode]:
        return MappingProxyType(
            {name: extension_type.manifest.config_schema for name, extension_type in self._extensions.items()}
        )

    def extension_type(self, name: str) -> type[Extension]:
        try:
            return self._extensions[name]
        except KeyError as exc:
            raise ExtensionRegistryError(f"unknown extension: {name}") from exc

    def resolve_order(self, enabled: Sequence[str]) -> tuple[str, ...]:
        requested = _validate_enabled_names(enabled)
        requested_set = set(requested)
        indegree = {name: 0 for name in requested}
        dependents = {name: [] for name in requested}
        for name in requested:
            manifest = self.extension_type(name).manifest
            for dependency in manifest.requires:
                if dependency not in self._extensions:
                    raise ExtensionRegistryError(f"{name} requires unknown extension {dependency}")
                if dependency not in requested_set:
                    raise ExtensionRegistryError(f"{name} requires enabled extension {dependency}")
                indegree[name] += 1
                dependents[dependency].append(name)

        position = {name: index for index, name in enumerate(requested)}
        ready = [name for name in requested if indegree[name] == 0]
        ordered: list[str] = []
        while ready:
            ready.sort(key=position.__getitem__)
            name = ready.pop(0)
            ordered.append(name)
            for dependent in dependents[name]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
        if len(ordered) != len(requested):
            blocked = [name for name in requested if indegree[name] > 0]
            raise ExtensionRegistryError(f"dependency cycle among: {', '.join(blocked)}")
        return tuple(ordered)
```

Implement a stable Kahn topological sort. When more than one node has zero indegree, choose according to the caller's `enabled` order, not alphabetically. Reject a required extension that exists in the registry but was not explicitly enabled.

- [ ] **Step 6: Export the stable API from `extensions/__init__.py`**

Export only public foundation symbols; do not export extension-private modules:

```python
from .api import Extension, ExtensionManifest
from .config import ConfigError, RuntimeConfig, load_runtime_config, parse_extension_list
from .registry import ExtensionRegistry, ExtensionRegistryError

__all__ = [
    "ConfigError",
    "Extension",
    "ExtensionManifest",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "RuntimeConfig",
    "load_runtime_config",
    "parse_extension_list",
]
```

- [ ] **Step 7: Run focused tests and lint**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_extension_registry \
  tests.extensions.test_extension_dependencies -v
uv run --locked --extra dev python -m ruff check \
  coding_tools_mcp/extensions/api.py \
  coding_tools_mcp/extensions/registry.py \
  tests/extensions/test_extension_registry.py \
  tests/extensions/test_extension_dependencies.py
```

Expected: PASS / Ruff clean.

- [ ] **Step 8: Commit Task 2**

```bash
git add coding_tools_mcp/extensions tests/extensions
git commit -m "feat: add extension registry and dependency resolution"
```

---

### Task 3: Typed Service Registry and Core Workspace Capability

**Files:**
- Create: `coding_tools_mcp/extensions/services.py`
- Create: `tests/extensions/test_extension_services.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`

**Interfaces:**
- Produces: `CapabilityKey[T]`, `ServiceRegistry`, `ServiceRegistryError`, `ResolvedPathLike`, `WorkspaceAccess`, `CORE_WORKSPACE`.
- Later `Runtime` will seed `CORE_WORKSPACE` with its existing `Workspace` object, which structurally satisfies `WorkspaceAccess`.

- [ ] **Step 1: Write failing service registry tests**

```python
class ExtensionServiceTests(unittest.TestCase):
    def test_provide_then_require_returns_same_object(self) -> None:
        registry = ServiceRegistry()
        key = CapabilityKey[object]("test.value")
        value = object()
        registry.provide(key, value)
        self.assertIs(registry.require(key), value)

    def test_duplicate_provider_is_rejected(self) -> None:
        registry = ServiceRegistry()
        key = CapabilityKey[int]("test.value")
        registry.provide(key, 1)
        with self.assertRaisesRegex(ServiceRegistryError, "duplicate capability provider: test.value"):
            registry.provide(key, 2)

    def test_missing_required_capability_is_rejected(self) -> None:
        registry = ServiceRegistry()
        with self.assertRaisesRegex(ServiceRegistryError, "required capability unavailable: test.missing"):
            registry.require(CapabilityKey[object]("test.missing"))

    def test_registry_is_immutable_after_freeze(self) -> None:
        registry = ServiceRegistry()
        registry.freeze()
        with self.assertRaisesRegex(ServiceRegistryError, "service registry is frozen"):
            registry.provide(CapabilityKey[int]("test.value"), 1)

    def test_capability_keys_with_same_name_are_equal(self) -> None:
        self.assertEqual(CapabilityKey[int]("test.value"), CapabilityKey[str]("test.value"))
```

The last test is intentional: capability identity is the stable string name, not Python object identity, so provider/consumer modules can share a key contract safely.

- [ ] **Step 2: Verify the tests fail**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_extension_services -v
```

Expected: import failure for `extensions.services`.

- [ ] **Step 3: Implement `CapabilityKey` and `ServiceRegistry`**

Create `coding_tools_mcp/extensions/services.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar, cast

T = TypeVar("T")


@dataclass(frozen=True)
class CapabilityKey(Generic[T]):
    name: str


class ServiceRegistryError(RuntimeError):
    pass


class ServiceRegistry:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._frozen = False

    def provide(self, key: CapabilityKey[T], value: T) -> None:
        if self._frozen:
            raise ServiceRegistryError("service registry is frozen")
        if key.name in self._values:
            raise ServiceRegistryError(f"duplicate capability provider: {key.name}")
        self._values[key.name] = value

    def require(self, key: CapabilityKey[T]) -> T:
        try:
            value = self._values[key.name]
        except KeyError as exc:
            raise ServiceRegistryError(f"required capability unavailable: {key.name}") from exc
        return cast(T, value)

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen
```

Do not add optional/multi-provider DI semantics in V1.

- [ ] **Step 4: Define the minimal core workspace protocol**

Use structural typing so `server.Workspace` does not have to inherit an extension class:

```python
class ResolvedPathLike(Protocol):
    display: str
    path: Path


class WorkspaceAccess(Protocol):
    root: Path

    def resolve_existing(self, raw_path: str = ".") -> ResolvedPathLike:
        raise NotImplementedError


CORE_WORKSPACE = CapabilityKey[WorkspaceAccess]("core.workspace")
```

This is the only core capability needed by the first projects extension. Add future core capabilities only when a concrete extension requires them.

- [ ] **Step 5: Run service tests + mypy on this module**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_extension_services -v
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions/services.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add coding_tools_mcp/extensions tests/extensions/test_extension_services.py
git commit -m "feat: add typed extension services"
```

---

### Task 4: Tool Contributions, Additive Decorators, and Frozen Composition

**Files:**
- Create: `coding_tools_mcp/extensions/contributions.py`
- Create: `tests/extensions/test_tool_contributions.py`
- Create: `tests/extensions/test_tool_decorators.py`
- Modify: `coding_tools_mcp/extensions/api.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`

**Interfaces:**
- Produces: `ToolAnnotations`, `ToolHandler`, `ToolTextRenderer`, `ToolContribution`, `SchemaPatch`, `ToolDecorator`, `ServerMetadataContribution`, `ComposedTool`, `ContributionRegistry`, `ContributionError`, `compose_tools()`, `ExtensionContext`.
- V1 decorators may add new input-schema properties/required fields and wrap handlers. They may not delete/replace existing schema properties.

- [ ] **Step 1: Write failing tool-contribution tests**

Use the `CORE` fixture below and write the concrete assertions:

```python
class ToolContributionTests(unittest.TestCase):
    def test_new_tool_is_added_with_origin_metadata(self) -> None:
        registry = ContributionRegistry()
        registry.add_tool(
            "extra",
            ToolContribution(
                name="extra_tool",
                title="Extra",
                description="extra",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=lambda args: {"source": "extra"},
                text_renderer=lambda payload: f"extra:{payload['source']}",
            ),
        )
        tools = compose_tools({"core_tool": CORE}, registry, ("extra",))
        self.assertEqual(tools["extra_tool"].origin, "extra")
        self.assertEqual(tools["extra_tool"].handler({}), {"source": "extra"})
        assert tools["extra_tool"].text_renderer is not None
        self.assertEqual(tools["extra_tool"].text_renderer({"source": "extra"}), "extra:extra")

    def test_extension_cannot_replace_core_tool(self) -> None:
        registry = ContributionRegistry()
        registry.add_tool(
            "extra",
            ToolContribution(
                name="core_tool",
                title="Replacement",
                description="replacement",
                input_schema={"type": "object"},
                handler=lambda args: {},
            ),
        )
        with self.assertRaisesRegex(ContributionError, "tool collision: core_tool"):
            compose_tools({"core_tool": CORE}, registry, ("extra",))

    def test_two_extensions_cannot_contribute_same_tool(self) -> None:
        registry = ContributionRegistry()
        contribution = ToolContribution(
            name="duplicate",
            title="Duplicate",
            description="duplicate",
            input_schema={"type": "object"},
            handler=lambda args: {},
        )
        registry.add_tool("a", contribution)
        with self.assertRaisesRegex(ContributionError, "tool already contributed: duplicate"):
            registry.add_tool("b", contribution)

    def test_registry_rejects_mutation_after_freeze(self) -> None:
        registry = ContributionRegistry()
        registry.freeze()
        with self.assertRaisesRegex(ContributionError, "contribution registry is frozen"):
            registry.add_metadata("a", ServerMetadataContribution(key="status", value="ok"))

    def test_composed_mapping_is_read_only(self) -> None:
        tools = compose_tools({"core_tool": CORE}, ContributionRegistry(), ())
        with self.assertRaises(TypeError):
            tools["other"] = CORE  # type: ignore[index]
```

Use a tiny core tool fixture:

```python
def core_handler(args: dict[str, object]) -> dict[str, object]:
    return {"source": "core", **args}

CORE = ComposedTool(
    name="core_tool",
    title="Core",
    description="core",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=ToolAnnotations(read_only=True, idempotent=True),
    handler=core_handler,
    origin="core",
)
```

- [ ] **Step 2: Write failing decorator tests**

Use this wrapper helper and concrete tests:

```python
def recording_wrapper(label: str, events: list[str]):
    def wrap(next_handler: ToolHandler) -> ToolHandler:
        def handler(args: dict[str, Any]) -> dict[str, Any]:
            events.append(label)
            return next_handler(args)
        return handler
    return wrap


class ToolDecoratorTests(unittest.TestCase):
    def test_decorator_adds_required_schema_property_and_wraps_handler(self) -> None:
        events: list[str] = []
        registry = ContributionRegistry()
        registry.add_decorator(
            "projects",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(
                    properties={"project_id": {"type": "string", "minLength": 1}},
                    required=("project_id",),
                ),
                wrap_handler=recording_wrapper("projects", events),
            ),
        )
        tool = compose_tools({"core_tool": CORE}, registry, ("projects",))["core_tool"]
        self.assertIn("project_id", tool.input_schema["properties"])
        self.assertIn("project_id", tool.input_schema["required"])
        tool.handler({"project_id": "app"})
        self.assertEqual(events, ["projects"])

    def test_unknown_decorator_target_fails_composition(self) -> None:
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(targets=("missing",), schema_patch=SchemaPatch(), wrap_handler=lambda handler: handler),
        )
        with self.assertRaisesRegex(ContributionError, "unknown decorator target: missing"):
            compose_tools({"core_tool": CORE}, registry, ("a",))

    def test_decorator_cannot_replace_existing_schema_property(self) -> None:
        core = replace(
            CORE,
            input_schema={
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(
                targets=("core_tool",),
                schema_patch=SchemaPatch(properties={"project_id": {"type": "integer"}}),
                wrap_handler=lambda handler: handler,
            ),
        )
        with self.assertRaisesRegex(ContributionError, "schema property collision: core_tool.project_id"):
            compose_tools({"core_tool": core}, registry, ("a",))

    def test_two_decorators_cannot_add_same_property(self) -> None:
        registry = ContributionRegistry()
        for extension in ("a", "b"):
            registry.add_decorator(
                extension,
                ToolDecorator(
                    targets=("core_tool",),
                    schema_patch=SchemaPatch(properties={"project_id": {"type": "string"}}),
                    wrap_handler=lambda handler: handler,
                ),
            )
        with self.assertRaisesRegex(ContributionError, "schema property collision: core_tool.project_id"):
            compose_tools({"core_tool": CORE}, registry, ("a", "b"))

    def test_decorator_execution_order_matches_extension_order(self) -> None:
        events: list[str] = []
        registry = ContributionRegistry()
        registry.add_decorator(
            "a",
            ToolDecorator(targets=("core_tool",), schema_patch=SchemaPatch(), wrap_handler=recording_wrapper("a", events)),
        )
        registry.add_decorator(
            "b",
            ToolDecorator(targets=("core_tool",), schema_patch=SchemaPatch(), wrap_handler=recording_wrapper("b", events)),
        )
        core = replace(CORE, handler=lambda args: events.append("core") or {"ok": True})
        compose_tools({"core_tool": core}, registry, ("a", "b"))["core_tool"].handler({})
        self.assertEqual(events, ["a", "b", "core"])
```

For ordering, if extensions are ordered `("a", "b")`, handler execution must be `a -> b -> core`. Compose wrappers in reverse registration order to make the first extension the outermost wrapper.

- [ ] **Step 3: Verify red state**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_tool_contributions \
  tests.extensions.test_tool_decorators -v
```

Expected: import failures.

- [ ] **Step 4: Implement immutable tool contract dataclasses**

Create `coding_tools_mcp/extensions/contributions.py` with:

```python
from copy import deepcopy
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
ContentBuilder = Callable[[dict[str, Any]], list[dict[str, Any]]]
HandlerWrapper = Callable[[ToolHandler], ToolHandler]
ToolTextRenderer = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ToolAnnotations:
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False


@dataclass(frozen=True)
class ToolContribution:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    annotations: ToolAnnotations = ToolAnnotations()
    error_status: str | None = None
    content_builder: ContentBuilder | None = None
    text_renderer: ToolTextRenderer | None = None


@dataclass(frozen=True)
class SchemaPatch:
    properties: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDecorator:
    targets: tuple[str, ...]
    schema_patch: SchemaPatch
    wrap_handler: HandlerWrapper


@dataclass(frozen=True)
class ServerMetadataContribution:
    key: str
    value: object


@dataclass(frozen=True)
class ComposedTool:
    name: str
    title: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    annotations: ToolAnnotations
    origin: str
    error_status: str | None = None
    content_builder: ContentBuilder | None = None
    text_renderer: ToolTextRenderer | None = None
    decorators: tuple[str, ...] = ()
```

Deep-copy schema dictionaries during composition and expose the final **tool catalog mapping** through `MappingProxyType`. The JSON schema payload itself may remain an internal `dict` so existing JSON encoding continues to work; never share the mother-core schema object by reference, and `tool_definition()` in Task 6 must return a fresh/copy-safe schema payload.

- [ ] **Step 5: Implement contribution registration and namespaced metadata**

`ContributionRegistry` should record extension ownership explicitly:

```python
class ContributionError(RuntimeError):
    pass


class ContributionRegistry:
    def __init__(self) -> None:
        self._tools: list[tuple[str, ToolContribution]] = []
        self._tool_names: set[str] = set()
        self._decorators: list[tuple[str, ToolDecorator]] = []
        self._metadata: dict[str, dict[str, object]] = {}
        self._frozen = False

    def _require_mutable(self) -> None:
        if self._frozen:
            raise ContributionError("contribution registry is frozen")

    def add_tool(self, extension: str, tool: ToolContribution) -> None:
        self._require_mutable()
        if tool.name in self._tool_names:
            raise ContributionError(f"tool already contributed: {tool.name}")
        self._tool_names.add(tool.name)
        self._tools.append((extension, tool))

    def add_decorator(self, extension: str, decorator: ToolDecorator) -> None:
        self._require_mutable()
        self._decorators.append((extension, decorator))

    def add_metadata(self, extension: str, contribution: ServerMetadataContribution) -> None:
        self._require_mutable()
        namespace = self._metadata.setdefault(extension, {})
        if contribution.key in namespace:
            raise ContributionError(f"duplicate metadata contribution: {extension}.{contribution.key}")
        namespace[contribution.key] = contribution.value

    def freeze(self) -> None:
        self._frozen = True
```

Add read-only iterator/snapshot methods that return tuples/copies rather than exposing the private lists/dicts:

```python
def tool_entries(self) -> tuple[tuple[str, ToolContribution], ...]:
    return tuple(self._tools)


def decorator_entries(self) -> tuple[tuple[str, ToolDecorator], ...]:
    return tuple(self._decorators)


def metadata_snapshot(self) -> dict[str, dict[str, object]]:
    return {
        extension: dict(values)
        for extension, values in self._metadata.items()
    }
```

Metadata keys are namespaced by extension in the host output; an extension cannot write into another extension's namespace.

- [ ] **Step 6: Implement `compose_tools()` with strict additive schema patches**

Use:

```python
def compose_tools(
    core_tools: Mapping[str, ComposedTool],
    contributions: ContributionRegistry,
    extension_order: Sequence[str],
) -> Mapping[str, ComposedTool]:
    tools = {name: replace(tool, input_schema=deepcopy(dict(tool.input_schema))) for name, tool in core_tools.items()}
    for extension, contribution in contributions.tool_entries():
        if contribution.name in tools:
            raise ContributionError(f"tool collision: {contribution.name}")
        tools[contribution.name] = ComposedTool(
            name=contribution.name,
            title=contribution.title,
            description=contribution.description,
            input_schema=deepcopy(dict(contribution.input_schema)),
            handler=contribution.handler,
            annotations=contribution.annotations,
            error_status=contribution.error_status,
            content_builder=contribution.content_builder,
            text_renderer=contribution.text_renderer,
            origin=extension,
        )

    rank = {name: index for index, name in enumerate(extension_order)}
    decorators = sorted(
        contributions.decorator_entries(),
        key=lambda item: rank[item[0]],
    )
    by_target: dict[str, list[tuple[str, ToolDecorator]]] = {}
    for extension, decorator in decorators:
        for target in decorator.targets:
            if target not in tools:
                raise ContributionError(f"unknown decorator target: {target}")
            by_target.setdefault(target, []).append((extension, decorator))

    for target, entries in by_target.items():
        tool = tools[target]
        schema = deepcopy(dict(tool.input_schema))
        properties = schema.setdefault("properties", {})
        required = list(schema.get("required", []))
        for extension, decorator in entries:
            for name, property_schema in decorator.schema_patch.properties.items():
                if name in properties:
                    raise ContributionError(f"schema property collision: {target}.{name}")
                properties[name] = deepcopy(dict(property_schema))
            for name in decorator.schema_patch.required:
                if name not in properties:
                    raise ContributionError(f"required schema property missing: {target}.{name}")
                if name not in required:
                    required.append(name)
        if required:
            schema["required"] = required

        handler = tool.handler
        for _extension, decorator in reversed(entries):
            handler = decorator.wrap_handler(handler)
        tools[target] = replace(
            tool,
            input_schema=schema,
            handler=handler,
            decorators=tool.decorators + tuple(extension for extension, _decorator in entries),
        )
    for tool in tools.values():
        _validate_composed_tool(tool)
    return MappingProxyType(tools)
```

Add this minimum contract validator in the same module:

```python
def _validate_composed_tool(tool: ComposedTool) -> None:
    schema = tool.input_schema
    if schema.get("type") != "object":
        raise ContributionError(f"tool input schema must be an object: {tool.name}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ContributionError(f"tool schema properties must be an object: {tool.name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise ContributionError(f"tool schema required must be a string list: {tool.name}")
    missing = [name for name in required if name not in properties]
    if missing:
        raise ContributionError(f"tool schema requires unknown properties: {tool.name}: {missing}")
```

Do not implement a full JSON Schema meta-validator; existing protocol/schema tests remain responsible for the broader MCP schema contract.

Rules:

1. clone core tools;
2. add contributed tools, rejecting duplicate names;
3. collect decorators in extension order and per-extension registration order;
4. ensure every target exists after contributions are added;
5. for each additive property, reject if already present or previously added;
6. add each `required` name only if the property exists after the patch;
7. build handler wrapper chain so execution follows extension order;
8. return a `MappingProxyType` of frozen `ComposedTool` instances.

- [ ] **Step 7: Complete `ExtensionContext` in `api.py`**

Now that concrete registries exist:

```python
from .contributions import (
    ContributionRegistry,
    ServerMetadataContribution,
    ToolContribution,
    ToolDecorator,
)
from .services import ServiceRegistry


@dataclass(frozen=True)
class ExtensionContext:
    services: ServiceRegistry
    contributions: ContributionRegistry
    extension_name: str

    def add_tool(self, tool: ToolContribution) -> None:
        self.contributions.add_tool(self.extension_name, tool)

    def add_decorator(self, decorator: ToolDecorator) -> None:
        self.contributions.add_decorator(self.extension_name, decorator)

    def add_metadata(self, key: str, value: object) -> None:
        self.contributions.add_metadata(
            self.extension_name,
            ServerMetadataContribution(key=key, value=value),
        )
```

Keep `services` public because capability provide/require is itself a constrained API; do not expose the host or core runtime object.

- [ ] **Step 8: Run focused contribution/decorator tests and typecheck**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_tool_contributions \
  tests.extensions.test_tool_decorators -v
uv run --locked --extra dev python -m mypy \
  coding_tools_mcp/extensions/api.py \
  coding_tools_mcp/extensions/contributions.py \
  coding_tools_mcp/extensions/services.py
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

```bash
git add coding_tools_mcp/extensions tests/extensions
git commit -m "feat: add extension tool composition"
```

---

### Task 5: ExtensionHost Lifecycle, Freeze Boundary, and Failure Cleanup

**Files:**
- Create: `coding_tools_mcp/extensions/host.py`
- Create: `tests/extensions/test_extension_lifecycle.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`

**Interfaces:**
- Consumes: `RuntimeConfig`, `ExtensionRegistry`, `ServiceRegistry`, `ContributionRegistry`, `compose_tools()`.
- Produces: `ExtensionHost.build()`, `.tools`, `.metadata()`, `.stop()`.
- `build()` performs configure → register → compose/validate → freeze → start. A successfully returned host is fully started and immutable at the registry level.

- [ ] **Step 1: Write failing lifecycle tests with traceable fake extensions**

Create extensions that append lifecycle events to a shared list:

```python
def lifecycle_extension(
    name: str,
    events: list[str],
    *,
    requires: tuple[str, ...] = (),
    fail_register: bool = False,
    fail_start: bool = False,
    fail_stop: bool = False,
):
    class FakeExtension:
        manifest = ExtensionManifest(name=name, requires=requires)

        def configure(self, config):
            events.append(f"{name}.configure")

        def register(self, context):
            events.append(f"{name}.register")
            if fail_register:
                raise RuntimeError(f"{name} register failed")

        def start(self):
            events.append(f"{name}.start")
            if fail_start:
                raise RuntimeError(f"{name} start failed")

        def stop(self):
            events.append(f"{name}.stop")
            if fail_stop:
                raise RuntimeError(f"{name} stop failed")

    return FakeExtension
```

Assert exact sequence:

```text
base.configure
child.configure
base.register
child.register
base.start
child.start
child.stop
base.stop
```

Implement the lifecycle assertions concretely:

```python
class ExtensionLifecycleTests(unittest.TestCase):
    def build_host(self, extension_types, enabled):
        registry = ExtensionRegistry(extension_types, default_enabled=())
        return ExtensionHost.build(
            registry=registry,
            config=RuntimeConfig.defaults(enabled=enabled),
            core_tools={},
        )

    def test_dependency_lifecycle_order_and_reverse_shutdown(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",))
        host = self.build_host([base, child], ("child", "base"))
        host.stop()
        self.assertEqual(
            events,
            [
                "base.configure", "child.configure",
                "base.register", "child.register",
                "base.start", "child.start",
                "child.stop", "base.stop",
            ],
        )

    def test_registries_are_frozen_before_first_start_call(self) -> None:
        test_case = self

        class FreezeProbe:
            manifest = ExtensionManifest(name="probe")

            def configure(self, config):
                self.context = None

            def register(self, context):
                self.context = context

            def start(self):
                assert self.context is not None
                with test_case.assertRaisesRegex(ServiceRegistryError, "service registry is frozen"):
                    self.context.services.provide(CapabilityKey[int]("late.service"), 1)
                with test_case.assertRaisesRegex(ContributionError, "contribution registry is frozen"):
                    self.context.add_metadata("late", True)

            def stop(self):
                pass

        host = self.build_host([FreezeProbe], ("probe",))
        host.stop()

    def test_registration_failure_starts_nothing(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_register=True)
        with self.assertRaisesRegex(RuntimeError, "child register failed"):
            self.build_host([base, child], ("base", "child"))
        self.assertNotIn("base.start", events)
        self.assertNotIn("child.start", events)

    def test_start_failure_stops_failing_extension_then_previously_started_extensions(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_start=True)
        with self.assertRaisesRegex(RuntimeError, "child start failed"):
            self.build_host([base, child], ("base", "child"))
        self.assertEqual(events[-2:], ["child.stop", "base.stop"])

    def test_stop_is_idempotent(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        host = self.build_host([base], ("base",))
        host.stop()
        host.stop()
        self.assertEqual(events.count("base.stop"), 1)

    def test_one_stop_failure_does_not_skip_remaining_extensions(self) -> None:
        events: list[str] = []
        base = lifecycle_extension("base", events)
        child = lifecycle_extension("child", events, requires=("base",), fail_stop=True)
        host = self.build_host([base, child], ("base", "child"))
        warnings = host.stop()
        self.assertIn("child stop failed", "\n".join(warnings))
        self.assertIn("base.stop", events)
```

- [ ] **Step 2: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_extension_lifecycle -v
```

Expected: import failure for `extensions.host`.

- [ ] **Step 3: Implement `ExtensionHost.build()` orchestration**

Create `coding_tools_mcp/extensions/host.py` with the public constructor:

```python
from typing import Any, Iterable, Mapping

from .api import Extension, ExtensionContext
from .config import RuntimeConfig
from .contributions import ComposedTool, ContributionRegistry, compose_tools
from .registry import ExtensionRegistry
from .services import CapabilityKey, ServiceRegistry


class ExtensionHost:
    def __init__(
        self,
        *,
        order: tuple[str, ...],
        instances: Mapping[str, Extension],
        services: ServiceRegistry,
        contributions: ContributionRegistry,
        tools: Mapping[str, ComposedTool],
    ) -> None:
        self._order = order
        self._instances = dict(instances)
        self._services = services
        self._contributions = contributions
        self._tools = tools
        self._stopped = False

    @classmethod
    def build(
        cls,
        *,
        registry: ExtensionRegistry,
        config: RuntimeConfig,
        core_tools: Mapping[str, ComposedTool],
        seed_services: Iterable[tuple[CapabilityKey[Any], Any]] = (),
    ) -> "ExtensionHost":
        order = registry.resolve_order(config.enabled_extensions)
        instances = {name: registry.extension_type(name)() for name in order}
        services = ServiceRegistry()
        contributions = ContributionRegistry()
        for key, value in seed_services:
            services.provide(key, value)
        for name in order:
            instances[name].configure(config.extension(name))
        for name in order:
            instances[name].register(
                ExtensionContext(
                    services=services,
                    contributions=contributions,
                    extension_name=name,
                )
            )
        tools = compose_tools(core_tools, contributions, order)
        contributions.freeze()
        services.freeze()
        host = cls(
            order=order,
            instances=instances,
            services=services,
            contributions=contributions,
            tools=tools,
        )
        started: list[str] = []
        for name in order:
            try:
                instances[name].start()
            except Exception as exc:
                warnings = host._stop_names((name, *reversed(started)))
                for warning in warnings:
                    exc.add_note(f"extension cleanup: {warning}")
                host._stopped = True
                raise
            started.append(name)
        return host

    @property
    def tools(self) -> Mapping[str, ComposedTool]:
        return self._tools

    def metadata(self) -> dict[str, object]:
        return {
            "enabled": list(self._order),
            "order": list(self._order),
            "contributions": {
                "tools": [tool.name for _extension, tool in self._contributions.tool_entries()],
                "decorated_tools": sorted(
                    {target for _extension, decorator in self._contributions.decorator_entries() for target in decorator.targets}
                ),
            },
            "metadata": self._contributions.metadata_snapshot(),
        }

    def _stop_names(self, names: Iterable[str]) -> tuple[str, ...]:
        warnings: list[str] = []
        for name in names:
            try:
                self._instances[name].stop()
            except Exception as exc:
                if len(warnings) < 32:
                    warnings.append(f"{name}: {exc}")
        return tuple(warnings)

    def stop(self) -> tuple[str, ...]:
        if self._stopped:
            return ()
        self._stopped = True
        return self._stop_names(reversed(self._order))
```

Internal build sequence must be literal and test-visible:

```python
order = registry.resolve_order(config.enabled_extensions)
instances = {name: registry.extension_type(name)() for name in order}
for name in order:
    instances[name].configure(config.extension(name))
for key, value in seed_services:
    services.provide(key, value)
for name in order:
    instances[name].register(
        ExtensionContext(
            services=services,
            contributions=contributions,
            extension_name=name,
        )
    )
tools = compose_tools(core_tools, contributions, order)
contributions.freeze()
services.freeze()
for name in order:
    instances[name].start()
```

If `start()` for extension X raises, call `X.stop()` first, then stop already-started extensions in reverse order, collect cleanup failures, and re-raise the original startup error with cleanup diagnostics chained/bounded.

- [ ] **Step 4: Implement bounded metadata**

`metadata()` returns no config values or service objects:

```python
{
    "enabled": ["base", "child"],
    "order": ["base", "child"],
    "contributions": {
        "tools": ["extension_echo"],
        "decorated_tools": ["server_info"],
    },
    "metadata": {"child": {"health": "ready"}},
}
```

Extension-specific metadata contributions live under:

```python
"metadata": {"semantic": {"backend": "serena", "status": "ready"}}
```

Never include raw TOML contents, environment values, paths from arbitrary config keys, or service reprs.

- [ ] **Step 5: Run lifecycle tests + lint/typecheck**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_extension_lifecycle -v
uv run --locked --extra dev python -m ruff check coding_tools_mcp/extensions/host.py tests/extensions/test_extension_lifecycle.py
uv run --locked --extra dev python -m mypy coding_tools_mcp/extensions
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add coding_tools_mcp/extensions tests/extensions/test_extension_lifecycle.py
git commit -m "feat: add extension host lifecycle"
```

---

### Task 6: Mother-Core Tool Bridge Through the Composed Catalog

**Files:**
- Modify: `coding_tools_mcp/server.py:621-761` (`ToolSpec`, `TOOL_REGISTRY` remains core source)
- Modify: `coding_tools_mcp/server.py:1458-1540` (`Runtime.__init__`)
- Modify: `coding_tools_mcp/server.py:1708-1825` (`list_tools`, `server_info_payload`, `call_tool`)
- Modify: `coding_tools_mcp/server.py:4952-5070` (`validate_arguments`, `tool_definition`, `tool_annotations`)
- Modify: `coding_tools_mcp/tool_results.py:10-42` (`make_tool_result`, `render_tool_text` optional renderer seam)
- Create: `tests/extensions/test_core_bridge.py`
- Modify: `tests/compliance/test_schema_drift.py`
- Modify: `tests/compliance/test_mcp_contract.py` only where helper assumptions directly index the global core registry instead of runtime-composed definitions; keep the default 22-tool assertion unchanged through Task 8, where `projects` is extracted and immediately re-contributes the same two tools.

**Interfaces:**
- Consumes: `ExtensionHost`, `ComposedTool`, `ToolAnnotations`, `CORE_WORKSPACE`.
- Produces: runtime-owned immutable `self._tools: Mapping[str, ComposedTool]`; all tool listing, schema validation, annotations, handlers, content builders, optional text renderers, and error-status lookup use this mapping.
- This is the principal upstream bridge seam.

- [ ] **Step 1: Write failing bridge tests using a synthetic internal extension registry**

`tests/extensions/test_core_bridge.py` must construct a `Runtime` with an explicit synthetic registry/config, not modify global built-ins:

```python
class EchoExtension:
    manifest = ExtensionManifest(name="echo")

    def configure(self, config): pass
    def register(self, context):
        context.add_tool(
            ToolContribution(
                name="extension_echo",
                title="Extension echo",
                description="Echo extension arguments.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                handler=lambda args: {"value": args["value"]},
                annotations=ToolAnnotations(read_only=True, idempotent=True),
            )
        )
    def start(self): pass
    def stop(self): pass
```

Tests:

```python
class CoreBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='fixture'\nversion='0'\n",
            encoding="utf-8",
        )
        self.registry = ExtensionRegistry([EchoExtension], default_enabled=())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runtime(self, enabled: tuple[str, ...]) -> Runtime:
        return Runtime(
            self.root,
            extension_registry=self.registry,
            extension_config=RuntimeConfig.defaults(enabled=enabled),
        )

    def test_tools_list_contains_extension_contribution(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            names = {tool["name"] for tool in runtime.list_tools()["tools"]}
            self.assertIn("extension_echo", names)
        finally:
            runtime.close()

    def test_call_tool_dispatches_extension_handler(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            result = runtime.call_tool("extension_echo", {"value": "hello"})
            self.assertEqual(result["structuredContent"]["value"], "hello")
            self.assertFalse(result["isError"])
        finally:
            runtime.close()

    def test_extension_schema_is_enforced_by_existing_jsonrpc_validation_path(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            with self.assertRaisesRegex(JsonRpcError, "arguments.value is required"):
                runtime.call_tool("extension_echo", {})
        finally:
            runtime.close()

    def test_server_info_reports_bounded_extension_metadata(self) -> None:
        runtime = self.runtime(("echo",))
        try:
            metadata = runtime.server_info_payload()["extensions"]
            self.assertEqual(metadata["enabled"], ["echo"])
            self.assertIn("extension_echo", metadata["contributions"]["tools"])
            self.assertNotIn("extension_settings", metadata)
        finally:
            runtime.close()

    def test_disabled_extension_contributes_nothing(self) -> None:
        runtime = self.runtime(())
        try:
            self.assertNotIn("extension_echo", runtime.exposed_tool_names())
        finally:
            runtime.close()
```

- [ ] **Step 2: Verify bridge tests fail against the current global-only registry**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_core_bridge -v
```

Expected: FAIL because `Runtime` cannot accept/compose an extension registry/config.

- [ ] **Step 3: Add a core-to-extension tool adapter without moving `ToolSpec`**

Keep upstream's `ToolSpec` and `TOOL_REGISTRY` as the mother-core definition source. Add a helper near tool-definition code:

```python
def core_tool_contracts(runtime: Runtime) -> dict[str, ComposedTool]:
    schemas = input_schemas()
    contracts: dict[str, ComposedTool] = {}
    for name, spec in TOOL_REGISTRY.items():
        if spec.gated_by is not None and not getattr(runtime, spec.gated_by):
            continue
        contracts[name] = ComposedTool(
            name=name,
            title=spec.title,
            description=spec.description,
            input_schema=schemas[name],
            handler=getattr(runtime, name),
            annotations=ToolAnnotations(
                read_only=spec.read_only,
                destructive=spec.destructive,
                idempotent=spec.idempotent,
                open_world=spec.open_world,
            ),
            error_status=spec.error_status,
            content_builder=spec.content_builder,
            origin="core",
        )
    return contracts
```

This is an adapter, not a duplicate registry: core metadata remains authored once in `TOOL_REGISTRY`/`input_schemas()` until an item is deliberately extracted into an extension.

- [ ] **Step 4: Extend `Runtime.__init__` with injectable extension config/registry**

Add keyword-only parameters:

```python
extension_config: RuntimeConfig | None = None,
extension_registry: ExtensionRegistry | None = None,
```

Add and export a `builtin_extension_registry()` factory in `extensions.__init__`; until Task 8 introduces the first real built-in extension it is deliberately empty:

```python
def builtin_extension_registry() -> ExtensionRegistry:
    return ExtensionRegistry([], default_enabled=())
```

Task 8 replaces only this factory body/default set; `server.py` never imports `extensions.projects` directly.

After core workspace/project-context/telemetry state exists, build the host:

```python
self.extension_registry = extension_registry or builtin_extension_registry()
self.extension_config = extension_config or RuntimeConfig.defaults(
    enabled=self.extension_registry.default_enabled
)
self.extension_host = ExtensionHost.build(
    registry=self.extension_registry,
    config=self.extension_config,
    core_tools=core_tool_contracts(self),
    seed_services=((CORE_WORKSPACE, self.workspace),),
)
self._tools = self.extension_host.tools
self._exposed_tool_names = tuple(self._tools)
self._exposed_tool_name_set = frozenset(self._tools)
```

Delete the old `_tool_handlers = {name: getattr(self, name) for name in TOOL_REGISTRY}` bridge once `call_tool` uses `self._tools[name].handler`.

- [ ] **Step 5: Route listing/validation/dispatch through `ComposedTool`**

Refactor helpers to take the actual contract:

```python
def validate_arguments(tool: ComposedTool, args: dict[str, Any]) -> None:
    validate_schema_value(args, dict(tool.input_schema), path="arguments")


def tool_annotations(tool: ComposedTool, *, fake_readonly: bool = False) -> dict[str, Any]:
    annotations = tool.annotations
    if fake_readonly:
        return {
            "title": tool.title,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": annotations.idempotent,
            "openWorldHint": False,
        }
    return {
        "title": tool.title,
        "readOnlyHint": annotations.read_only,
        "destructiveHint": annotations.destructive,
        "idempotentHint": annotations.idempotent,
        "openWorldHint": annotations.open_world,
    }


def tool_definition(tool: ComposedTool, *, fake_readonly: bool = False) -> dict[str, Any]:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": deepcopy(dict(tool.input_schema)),
        "outputSchema": tool_output_schema(),
        "annotations": tool_annotations(tool, fake_readonly=fake_readonly),
    }
```

`Runtime.call_tool()` becomes conceptually:

```python
tool = self._tools.get(name)
if tool is None:
    raise JsonRpcError(-32602, f"Unknown tool: {name}", {"reason": "unknown_tool"})
validate_arguments(tool, args)
try:
    payload = tool.handler(args)
    payload.setdefault("ok", True)
    self.emit_tool_trace(name, args, payload, started_at, context=context)
    content = tool.content_builder(payload) if tool.content_builder else None
    return make_tool_result(
        name,
        payload,
        is_error=payload.get("ok") is False,
        content=content,
        text_renderer=tool.text_renderer,
    )
except ToolFailure as exc:
    payload = {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "category": exc.category,
            "retryable": exc.retryable,
            "details": exc.details,
        },
    }
    if tool.error_status:
        payload["status"] = tool.error_status
    self.emit_tool_trace(name, args, payload, started_at, context=context)
    return make_tool_result(name, payload, is_error=True, text_renderer=tool.text_renderer)
```

Preserve the existing `except Exception` structured `INTERNAL_ERROR` branch after this code, changing only its lookup from the old `spec` variable to `tool.error_status` and passing `text_renderer=tool.text_renderer` to `make_tool_result()` there as well. Error rendering still takes precedence over the success renderer inside `render_tool_text()`.

No extension-specific branch is allowed in `call_tool()`.

- [ ] **Step 6: Add the generic optional text-renderer seam to `tool_results.py`**

Do not teach `tool_results.py` about extensions. It only accepts an optional renderer supplied by the composed tool contract:

```python
ToolTextRenderer = Callable[[dict[str, Any]], str]


def make_tool_result(
    tool_name: str,
    payload: dict[str, Any],
    *,
    is_error: bool,
    content: list[dict[str, Any]] | None = None,
    text_renderer: ToolTextRenderer | None = None,
) -> dict[str, Any]:
    result_content = list(content or [])
    text = render_tool_text(
        tool_name,
        payload,
        is_error=is_error,
        text_renderer=text_renderer,
    )
    if text:
        result_content.append({"type": "text", "text": _bounded_model_text(text, tool_name)})
    return {"content": result_content, "structuredContent": payload, "isError": is_error}


def render_tool_text(
    tool_name: str,
    payload: dict[str, Any],
    *,
    is_error: bool,
    text_renderer: ToolTextRenderer | None = None,
) -> str:
    if is_error or payload.get("ok") is False:
        return _render_error(payload)
    renderer = text_renderer or _RENDERERS.get(tool_name)
    if renderer is not None:
        return renderer(payload)
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    status = payload.get("status")
    return f"{tool_name}: {status or 'completed'}."
```

Keep the existing core `_RENDERERS` map for mother-core tools. Task 8 removes only the two project/skill renderer entries/functions after moving equivalent renderers into `ProjectsExtension`.

- [ ] **Step 7: Stop ExtensionHost before core-owned runtime resources**

At the beginning of `Runtime.close()` after the idempotency guard:

```python
extension_warnings = self.extension_host.stop()
```

Then close `WorkspaceCommandManager`/telemetry as today. Feed bounded extension shutdown warning counts into telemetry/server logs if an existing safe channel is available; do not make `close()` fail to clean up core resources because an extension `stop()` failed.

- [ ] **Step 8: Expose bounded extension metadata in `server_info`**

Add:

```python
"extensions": self.extension_host.metadata(),
```

Do not place raw `extension_settings` there.

- [ ] **Step 9: Run bridge + schema drift + current MCP contract tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_core_bridge \
  tests.compliance.test_schema_drift \
  tests.compliance.test_mcp_contract -v
```

Expected: PASS with the existing default tool catalog unchanged at this point.

- [ ] **Step 10: Run Ruff/mypy for the bridge**

```bash
uv run --locked --extra dev python -m ruff check coding_tools_mcp/server.py coding_tools_mcp/extensions tests/extensions
uv run --locked --extra dev python -m mypy coding_tools_mcp/server.py coding_tools_mcp/extensions
```

Expected: PASS.

- [ ] **Step 11: Commit Task 6**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/tool_results.py coding_tools_mcp/extensions tests/extensions tests/compliance
git commit -m "refactor: route tools through extension bridge"
```

---

### Task 7: Startup Config Integration and Fail-Fast CLI/Environment Behavior

**Files:**
- Modify: `coding_tools_mcp/server.py:6051-6342` (`build_runtime`, `run_http`, `run_stdio`, `build_parser`, `main`)
- Modify: `coding_tools_mcp/extensions/__init__.py`
- Create: `tests/extensions/test_config_startup.py`
- Modify: `scripts/launcher/config.py:57-151` only if a launcher test proves it does not start the MCP process with `cwd=config.mcp_repository`; otherwise make no launcher code change.
- Modify: `tests/test_launcher_config.py` only if the launcher behavior above needs a regression assertion.

**Interfaces:**
- Produces CLI flags `--config`, `--local-config`, `--extensions` and their environment counterparts.
- `build_runtime()` loads configuration before creating `Runtime`.
- Invalid config returns startup exit code `2` with bounded `ERROR:` stderr before transport binds/listens.

- [ ] **Step 1: Write failing startup config tests**

Create `tests/extensions/test_config_startup.py` covering:

```python
class ConfigStartupTests(unittest.TestCase):
    def test_build_parser_accepts_config_local_config_and_extensions(self) -> None:
        args = build_parser().parse_args(
            [
                "--config", "public.toml",
                "--local-config", "local.toml",
                "--extensions", "projects,semantic",
            ]
        )
        self.assertEqual(args.config, "public.toml")
        self.assertEqual(args.local_config, "local.toml")
        self.assertEqual(args.extensions, "projects,semantic")

    def test_build_runtime_loads_public_config_from_cwd_when_no_override_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch("os.getcwd", return_value=tmp):
            root = Path(tmp)
            (root / "coding-tools.toml").write_text(
                "config_version = 1\n[extensions]\nenabled = []\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--workspace", tmp, "--stdio"])
            policy = runtime_policy_from_args(args)
            with mock.patch("pathlib.Path.cwd", return_value=root):
                runtime = build_runtime(args, policy, emit_warning=False)
            try:
                self.assertEqual(runtime.extension_config.sources, (root / "coding-tools.toml",))
            finally:
                runtime.close()

    def test_explicit_config_path_beats_environment_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit.toml"
            env_file = root / "env.toml"
            for path in (explicit, env_file):
                path.write_text("config_version = 1\n[extensions]\nenabled = []\n", encoding="utf-8")
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(explicit), "--stdio"])
            policy = runtime_policy_from_args(args)
            with mock.patch.dict(os.environ, {"CODING_TOOLS_MCP_CONFIG": str(env_file)}):
                runtime = build_runtime(args, policy, emit_warning=False)
            try:
                self.assertEqual(runtime.extension_config.sources, (explicit,))
            finally:
                runtime.close()

    def test_invalid_config_fails_before_http_server_construction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.toml"
            bad.write_text("config_version = 999\n[extensions]\nenabled = []\n", encoding="utf-8")
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(bad)])
            with mock.patch("coding_tools_mcp.server.RuntimeHTTPServer") as server_type:
                self.assertEqual(run_http(args), 2)
            server_type.assert_not_called()

    def test_invalid_config_fails_stdio_startup_with_exit_code_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.toml"
            bad.write_text("config_version = 999\n[extensions]\nenabled = []\n", encoding="utf-8")
            args = build_parser().parse_args(["--workspace", tmp, "--config", str(bad), "--stdio"])
            self.assertEqual(run_stdio(args), 2)
```

For “before HTTP server construction”, patch `RuntimeHTTPServer` and assert it was never called.

- [ ] **Step 2: Verify red state**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_config_startup -v
```

Expected: FAIL because parser/runtime do not consume extension config.

- [ ] **Step 3: Add CLI flags with `None` defaults so precedence remains observable**

Add:

```python
parser.add_argument(
    "--config",
    default=None,
    help=f"public extension config TOML; defaults to {ENV_PREFIX}_CONFIG or ./coding-tools.toml when present",
)
parser.add_argument(
    "--local-config",
    default=None,
    help=f"private extension overlay TOML; defaults to {ENV_PREFIX}_LOCAL_CONFIG or coding-tools.local.toml when present",
)
parser.add_argument(
    "--extensions",
    default=None,
    help=f"comma-separated full enabled-extension override; defaults to {ENV_PREFIX}_EXTENSIONS when set",
)
```

Do not add a generic `--set key=value` escape hatch.

- [ ] **Step 4: Load config once in `build_runtime()`**

Use the registry as the schema source:

```python
registry = builtin_extension_registry()
config = load_runtime_config(
    cwd=Path.cwd(),
    extension_schemas=registry.schemas(),
    default_enabled=registry.default_enabled,
    environ=os.environ,
    public_path=args.config,
    local_path=args.local_config,
    cli_extensions=parse_extension_list(args.extensions) if args.extensions is not None else None,
)
runtime = Runtime(
    workspace,
    enable_view_image=args.enable_view_image,
    permission_mode=runtime_policy.permission_mode,
    shell_env_policy=runtime_policy.shell_env_policy,
    allow_network=runtime_policy.allow_network,
    auth_token=auth_token,
    oauth_config=oauth_config,
    project_context=project_context,
    fake_readonly_annotations=runtime_policy.fake_readonly_annotations,
    transport=transport,
    command_manager=command_manager,
    extension_config=config,
    extension_registry=registry,
)
```

If an embedder passes an explicit registry/config directly to `Runtime`, it bypasses file discovery by design; tests and future embedding code can remain deterministic.

- [ ] **Step 5: Make `run_http` / `run_stdio` catch startup config errors before transport**

Catch `ConfigError`, `ExtensionRegistryError`, `ContributionError`, and service/host startup validation errors at the same startup boundary that already handles policy `ValueError`s. Print:

```text
ERROR: <bounded diagnostic>
```

and return `2`. Do not convert startup config errors into MCP tool results because no valid runtime contract exists yet.

- [ ] **Step 6: Verify launcher ownership boundary instead of duplicating parsing**

Inspect/retain the existing launcher behavior where the MCP `start_process` call receives `cwd=config.mcp_repository`. Add a regression assertion if absent:

```python
self.assertEqual(mcp_start.cwd, config.mcp_repository)
```

Because runtime default discovery checks `cwd/coding-tools.toml`, this is sufficient. Do **not** add TOML parsing to `scripts/launcher/config.py`.

- [ ] **Step 7: Run startup/launcher focused tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_config_startup \
  tests.test_launcher_config \
  tests.test_launcher_integration -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 7**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/extensions tests/extensions tests/test_launcher_config.py tests/test_launcher_integration.py
git commit -m "feat: load extension config at startup"
```

---

### Task 8: Extract Existing Project/Skill Discovery Into the First `projects` Extension

**Files:**
- Create: `coding_tools_mcp/extensions/projects/__init__.py`
- Create: `coding_tools_mcp/extensions/projects/extension.py`
- Move: `coding_tools_mcp/project_catalog.py` → `coding_tools_mcp/extensions/projects/project_catalog.py`
- Move: `coding_tools_mcp/skill_catalog.py` → `coding_tools_mcp/extensions/projects/skill_catalog.py`
- Modify: `coding_tools_mcp/extensions/__init__.py`
- Modify: `coding_tools_mcp/server.py:80-95` imports
- Modify: `coding_tools_mcp/server.py:680-706` remove `list_skills`/`read_skill` mother-core `ToolSpec`s
- Modify: `coding_tools_mcp/server.py:1528-1540` remove direct `SkillCatalog` construction
- Modify: `coding_tools_mcp/server.py:2306-2340` remove direct handlers/_resolve helper
- Modify: `coding_tools_mcp/server.py:5129-5133` remove mother-core skill schemas
- Modify: `coding_tools_mcp/tool_results.py:172-205,437-438` remove project/skill-specific renderers after equivalent extension renderers exist
- Modify: `coding-tools.toml`
- Modify: `tests/test_project_catalog.py`
- Modify: `tests/test_project_skills_integration.py`
- Modify: `tests/test_project_skills_runtime.py`
- Create: `tests/extensions/test_projects_extension.py`

**Interfaces:**
- Produces: `ProjectsExtension`, `PROJECT_CATALOG` capability.
- Consumes: `CORE_WORKSPACE` (`WorkspaceAccess`) from Task 3.
- Default registry becomes `projects` enabled, so the normal fork remains at 22 tools after extraction; `--extensions ""` produces 20 tools because the two skill tools are absent.
- This remains single-workspace compatibility behavior. Do not add configured project IDs or `list_projects`/`resolve_project` yet.

- [ ] **Step 1: Write failing extension-specific migration tests before moving code**

Create `tests/extensions/test_projects_extension.py`:

```python
@contextlib.contextmanager
def runtime_fixture(*, extension_config: RuntimeConfig | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text("{}", encoding="utf-8")
        registry = builtin_extension_registry()
        runtime = Runtime(
            root,
            extension_registry=registry,
            extension_config=extension_config,
        )
        try:
            yield runtime
        finally:
            runtime.close()


class ProjectsExtensionTests(unittest.TestCase):
    def test_builtin_registry_enables_projects_by_default(self) -> None:
        registry = builtin_extension_registry()
        self.assertEqual(registry.default_enabled, ("projects",))

    def test_default_runtime_still_exposes_list_and_read_skill(self) -> None:
        with runtime_fixture() as runtime:
            self.assertIn("list_skills", runtime.exposed_tool_names())
            self.assertIn("read_skill", runtime.exposed_tool_names())

    def test_disabled_projects_extension_contributes_neither_skill_tool(self) -> None:
        config = RuntimeConfig.defaults(enabled=())
        with runtime_fixture(extension_config=config) as runtime:
            self.assertNotIn("list_skills", runtime.exposed_tool_names())
            self.assertNotIn("read_skill", runtime.exposed_tool_names())

    def test_projects_extension_publishes_structural_project_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            services = ServiceRegistry()
            contributions = ContributionRegistry()
            workspace = Workspace(root)
            services.provide(CORE_WORKSPACE, workspace)
            extension = ProjectsExtension()
            extension.configure({})
            extension.register(
                ExtensionContext(
                    services=services,
                    contributions=contributions,
                    extension_name="projects",
                )
            )
            catalog = services.require(PROJECT_CATALOG)
            self.assertEqual(catalog.workspace, root.resolve())
            self.assertEqual(catalog.main_projects[0].project_id, ".")
```

Also preserve behavioral tests for:

- file workdir rejected as `NOT_A_DIRECTORY`;
- unknown skill returns `SKILL_NOT_FOUND` with `available` list;
- invalid skill returns `SKILL_INVALID`;
- nested instruction/skill precedence remains unchanged.

- [ ] **Step 2: Run new tests and verify they fail because built-in registry is empty**

```bash
uv run --locked --extra dev python -m unittest tests.extensions.test_projects_extension -v
```

Expected: FAIL on expected default `projects` registration.

- [ ] **Step 3: Move project catalog and skill catalog under the extension package without semantic edits**

Preserve the implementations first; only update imports:

```text
coding_tools_mcp/extensions/projects/project_catalog.py
coding_tools_mcp/extensions/projects/skill_catalog.py
```

Update the existing unit tests to import from the new package. Run those tests immediately after the move:

```bash
uv run --locked --extra dev python -m unittest \
  tests.test_project_catalog \
  tests.test_project_skills_integration -v
```

Expected: PASS before wiring the extension.

- [ ] **Step 4: Define `PROJECT_CATALOG` capability and `ProjectsExtension`**

In `extensions/projects/__init__.py`:

```python
from .extension import PROJECT_CATALOG, ProjectsExtension

__all__ = ["PROJECT_CATALOG", "ProjectsExtension"]
```

In `extension.py`:

```python
PROJECT_CATALOG = CapabilityKey[ProjectCatalog]("projects.catalog")


class ProjectsExtension:
    manifest = ExtensionManifest(
        name="projects",
        description="Single-workspace project scope, instructions, and skills discovery.",
        config_schema=table({}),
    )

    def __init__(self) -> None:
        self._workspace: WorkspaceAccess | None = None
        self._skills: SkillCatalog | None = None

    def configure(self, config: Mapping[str, object]) -> None:
        if config:
            raise ConfigError("extensions.projects has no Phase 0 settings")

    def register(self, context: ExtensionContext) -> None:
        workspace = context.services.require(CORE_WORKSPACE)
        catalog = build_project_catalog(workspace.root)
        self._workspace = workspace
        self._skills = SkillCatalog(catalog)
        context.services.provide(PROJECT_CATALOG, catalog)
        context.add_tool(self._list_skills_tool())
        context.add_tool(self._read_skill_tool())

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def _list_skills_tool(self) -> ToolContribution:
        return ToolContribution(
            name="list_skills",
            title="List skills",
            description="List project-scoped skills and instruction files for the explicit workspace-relative workdir.",
            input_schema={
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "default": "."},
                },
                "additionalProperties": False,
            },
            handler=self.list_skills,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            text_renderer=self._render_list_skills,
        )

    def _read_skill_tool(self) -> ToolContribution:
        return ToolContribution(
            name="read_skill",
            title="Read skill",
            description="Read the effective named skill for the explicit workspace-relative workdir.",
            input_schema={
                "type": "object",
                "properties": {
                    "workdir": {"type": "string", "default": "."},
                    "skill": {"type": "string", "minLength": 1},
                },
                "required": ["skill"],
                "additionalProperties": False,
            },
            handler=self.read_skill,
            annotations=ToolAnnotations(read_only=True, idempotent=True),
            error_status="failed",
            text_renderer=self._render_read_skill,
        )

    @staticmethod
    def _render_list_skills(payload: dict[str, Any]) -> str:
        skills = payload.get("skills")
        if not isinstance(skills, list) or not skills:
            return "No skills found."
        lines: list[str] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            description = item.get("description", "")
            source = item.get("source", "")
            if description:
                lines.append(f"{name}: {description} ({source})")
            else:
                lines.append(f"{name} ({source})")
        return "\n".join(lines)

    @staticmethod
    def _render_read_skill(payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if not isinstance(content, str):
            return ""
        if not payload.get("truncated"):
            return content
        total_bytes = payload.get("total_bytes", "?")
        returned_bytes = payload.get("returned_bytes", "?")
        return (
            f"[showing {returned_bytes} of {total_bytes} bytes; skill body truncated]\n"
            f"{content}"
        )
```

Use current tool descriptions, annotations, schemas, error status, and response payloads verbatim so migration does not change external semantics.

- [ ] **Step 5: Preserve current workdir/error behavior in extension handlers**

The extension handler must use `CORE_WORKSPACE.resolve_existing()` before `SkillCatalog`, preserving the existing directory-only requirement:

```python
def _require_workspace(self) -> WorkspaceAccess:
    if self._workspace is None:
        raise RuntimeError("projects extension is not registered")
    return self._workspace


def _require_skills(self) -> SkillCatalog:
    if self._skills is None:
        raise RuntimeError("projects extension is not registered")
    return self._skills


def _resolve_skill_workdir(self, raw_workdir: str) -> Path:
    workspace = self._require_workspace()
    resolved = workspace.resolve_existing(raw_workdir or ".")
    if not resolved.path.is_dir():
        raise ToolFailure("NOT_A_DIRECTORY", "workdir must be a directory.", category="validation")
    return resolved.path


def list_skills(self, args: dict[str, Any]) -> dict[str, Any]:
    workdir = self._resolve_skill_workdir(str(args.get("workdir", ".")))
    catalog = self._require_skills()
    try:
        context = catalog.list_for(workdir)
    except ValueError as exc:
        raise ToolFailure("INVALID_ARGUMENT", str(exc), category="validation") from exc
    return {"ok": True, **context.payload()}


def read_skill(self, args: dict[str, Any]) -> dict[str, Any]:
    workdir = self._resolve_skill_workdir(str(args.get("workdir", ".")))
    skill = str(args.get("skill", ""))
    if not skill:
        raise ToolFailure("INVALID_ARGUMENT", "skill is required.", category="validation")
    catalog = self._require_skills()
    try:
        loaded = catalog.read(workdir, skill)
    except ValueError as exc:
        raise ToolFailure("INVALID_ARGUMENT", str(exc), category="validation") from exc
    except ProjectNotFoundError as exc:
        raise ToolFailure("PROJECT_NOT_FOUND", str(exc), category="not_found") from exc
    except SkillNotFoundError as exc:
        raise ToolFailure(
            "SKILL_NOT_FOUND",
            str(exc),
            category="not_found",
            details={"available": list(exc.available)},
        ) from exc
    except SkillInvalidError as exc:
        raise ToolFailure("SKILL_INVALID", str(exc), category="invalid_state") from exc
    return {"ok": True, **loaded.payload()}
```

Copy the existing exception-to-`ToolFailure` mappings for `ProjectNotFoundError`, `SkillNotFoundError`, and `SkillInvalidError`. Do not invent new error codes.

- [ ] **Step 6: Remove direct projects/skills ownership from `server.py`**

After the extension is ready:

- remove direct imports of `build_project_catalog`, `SkillCatalog`, and skill exceptions;
- remove the two `TOOL_REGISTRY` entries;
- remove their two `input_schemas()` entries;
- remove `self.skill_catalog` construction;
- remove `Runtime.list_skills`, `Runtime.read_skill`, `_resolve_skill_workdir`.
- remove `_render_list_skills`, `_render_read_skill`, and their `_RENDERERS` entries from `tool_results.py`; the generic optional renderer seam from Task 6 remains.

Do **not** remove `ProjectContext` / `load_project_context` from the mother core.

Update `tests/test_project_skills_runtime.py::test_skill_tool_schemas_use_explicit_workdir_and_no_source_path` because extension schemas no longer live in the mother-core `input_schemas()` mapping. Read the two schemas from `Runtime.list_tools()` instead:

```python
runtime = Runtime(root)
try:
    tools = {item["name"]: item for item in runtime.list_tools()["tools"]}
    list_schema = tools["list_skills"]["inputSchema"]
    read_schema = tools["read_skill"]["inputSchema"]
    self.assertEqual(list_schema.get("required", []), [])
    self.assertEqual(list_schema["properties"]["workdir"]["default"], ".")
    self.assertEqual(read_schema["required"], ["skill"])
    self.assertNotIn("path", read_schema["properties"])
    self.assertNotIn("source", read_schema["properties"])
finally:
    runtime.close()
```

Also update `test_list_and_read_skill_return_project_scoped_payloads`, which currently calls removed `Runtime.list_skills()` / `Runtime.read_skill()` methods directly. Route it through the public dispatch path and inspect `structuredContent`:

```python
listed_result = runtime.call_tool("list_skills", {"workdir": "sdk/repos/effect"})
loaded_result = runtime.call_tool(
    "read_skill",
    {"workdir": "sdk/repos/effect", "skill": "effect-ts"},
)
listed = listed_result["structuredContent"]
loaded = loaded_result["structuredContent"]
self.assertFalse(listed_result["isError"])
self.assertFalse(loaded_result["isError"])
```

Remove `input_schemas` from that test module's import once no test in the file uses it.

Keep `test_skill_tools_render_agent_readable_metadata_and_body` unchanged semantically. Its successful text assertions become the regression proving that moving the specialized renderers into `ProjectsExtension` did not degrade model-facing output.

- [ ] **Step 7: Register the built-in extension and switch public/default composition**

In `extensions/__init__.py`, avoid import-time arbitrary discovery:

```python
def builtin_extension_registry() -> ExtensionRegistry:
    from .projects import ProjectsExtension

    return ExtensionRegistry([ProjectsExtension], default_enabled=("projects",))
```

Update committed `coding-tools.toml`:

```toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

- [ ] **Step 8: Run project/skills regression tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_projects_extension \
  tests.test_project_catalog \
  tests.test_project_skills_integration \
  tests.test_project_skills_runtime -v
```

Expected: PASS.

- [ ] **Step 9: Verify the default and disabled tool counts explicitly**

Add assertions to `test_projects_extension.py`:

```python
self.assertEqual(len(default_runtime.exposed_tool_names()), 22)
self.assertEqual(len(disabled_runtime.exposed_tool_names()), 20)
```

Then run:

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_projects_extension \
  tests.compliance.test_mcp_contract -v
```

Expected: PASS; default compatibility remains 22 tools.

- [ ] **Step 10: Run grep guard proving server has no projects-extension private import**

Run:

```bash
if rg -n 'extensions\.projects|project_catalog|skill_catalog|SkillCatalog' coding_tools_mcp/server.py; then
  echo 'server.py still owns projects extension internals' >&2
  exit 1
fi
```

Expected: no matches, exit 0.

- [ ] **Step 11: Commit Task 8**

```bash
git add \
  coding_tools_mcp/server.py \
  coding_tools_mcp/tool_results.py \
  coding_tools_mcp/extensions \
  coding-tools.toml \
  tests
git commit -m "refactor: move project skills into extension"
```

---

### Task 9: Harden the Upstream Bridge Contract With Focused Compatibility Tests

**Files:**
- Create: `tests/extensions/test_upstream_compatibility.py`
- Modify: `tests/extensions/test_core_bridge.py`
- Modify: `.github/workflows/compliance.yml` only if test discovery does not already include `tests/extensions/` (current `unittest discover -s tests -p "test_*.py"` should include them; verify before editing).

**Interfaces:**
- Produces: regression gates that fail when future upstream syncs bypass or invalidate the intended bridge.
- Does not depend on a local `xyTom/main` Git ref; CI clones may not have that remote/ref.

- [ ] **Step 1: Write bridge-structure tests that are portable in CI**

Create `test_upstream_compatibility.py` with source-level and runtime-level assertions:

```python
ROOT = Path(__file__).resolve().parents[2]
SERVER = (ROOT / "coding_tools_mcp" / "server.py").read_text(encoding="utf-8")
TOOL_RESULTS = (ROOT / "coding_tools_mcp" / "tool_results.py").read_text(encoding="utf-8")


class UpstreamBridgeCompatibilityTests(unittest.TestCase):
    def test_mother_core_does_not_import_extension_private_packages(self) -> None:
        self.assertNotIn("extensions.projects", SERVER)
        self.assertNotIn("extensions.semantic", SERVER)

    def test_projects_skill_tools_are_not_authored_in_core_tool_registry(self) -> None:
        self.assertNotRegex(SERVER, r'\n\s+"list_skills": ToolSpec\(')
        self.assertNotRegex(SERVER, r'\n\s+"read_skill": ToolSpec\(')

    def test_projects_skill_renderers_are_not_authored_in_core_tool_results(self) -> None:
        self.assertNotIn("def _render_list_skills", TOOL_RESULTS)
        self.assertNotIn("def _render_read_skill", TOOL_RESULTS)
        self.assertNotRegex(TOOL_RESULTS, r'"list_skills"\s*:\s*_render_list_skills')
        self.assertNotRegex(TOOL_RESULTS, r'"read_skill"\s*:\s*_render_read_skill')

    def test_core_registry_and_core_schema_names_match_before_composition(self) -> None:
        from coding_tools_mcp.server import TOOL_REGISTRY, input_schemas
        self.assertEqual(set(TOOL_REGISTRY), set(input_schemas()))

    def test_default_and_disabled_catalogs_have_expected_bridge_delta(self) -> None:
        from coding_tools_mcp.extensions import RuntimeConfig, builtin_extension_registry
        from coding_tools_mcp.server import Runtime, TOOL_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            registry = builtin_extension_registry()

            default_runtime = Runtime(root, extension_registry=registry)
            try:
                self.assertEqual(
                    set(default_runtime.exposed_tool_names()),
                    set(TOOL_REGISTRY) | {"list_skills", "read_skill"},
                )
            finally:
                default_runtime.close()

            disabled_runtime = Runtime(
                root,
                extension_registry=registry,
                extension_config=RuntimeConfig.defaults(enabled=()),
            )
            try:
                self.assertEqual(set(disabled_runtime.exposed_tool_names()), set(TOOL_REGISTRY))
            finally:
                disabled_runtime.close()
```

The fixture uses the default `enable_view_image=True`, so the runtime-visible core set matches `TOOL_REGISTRY` including the gated image tool.

- [ ] **Step 2: Add a decorator bridge test even though no production decorator exists yet**

Extend `test_core_bridge.py` with a fake decorator extension that adds a required `bridge_token` argument to `server_info` and strips it before calling the original handler. Assert both `tools/list` schema and dispatch use the decorated version. This prevents a future refactor from preserving contributed tools while accidentally bypassing decorators.

Use this concrete extension/test:

```python
class DecoratorExtension:
    manifest = ExtensionManifest(name="decorator")

    def configure(self, config):
        pass

    def register(self, context):
        def wrap(next_handler: ToolHandler) -> ToolHandler:
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                forwarded = dict(args)
                forwarded.pop("bridge_token", None)
                return next_handler(forwarded)
            return handler

        context.add_decorator(
            ToolDecorator(
                targets=("server_info",),
                schema_patch=SchemaPatch(
                    properties={"bridge_token": {"type": "string", "minLength": 1}},
                    required=("bridge_token",),
                ),
                wrap_handler=wrap,
            )
        )

    def start(self):
        pass

    def stop(self):
        pass


def test_decorator_bridge_changes_both_schema_and_dispatch(self) -> None:
    registry = ExtensionRegistry([DecoratorExtension], default_enabled=())
    runtime = Runtime(
        self.root,
        extension_registry=registry,
        extension_config=RuntimeConfig.defaults(enabled=("decorator",)),
    )
    try:
        tools = {tool["name"]: tool for tool in runtime.list_tools()["tools"]}
        schema = tools["server_info"]["inputSchema"]
        self.assertIn("bridge_token", schema["properties"])
        self.assertIn("bridge_token", schema["required"])

        missing = None
        try:
            runtime.call_tool("server_info", {})
        except JsonRpcError as exc:
            missing = exc
        self.assertIsNotNone(missing)

        result = runtime.call_tool("server_info", {"bridge_token": "ok"})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["server"], "coding-tools-mcp")
    finally:
        runtime.close()
```

- [ ] **Step 3: Run compatibility tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.extensions.test_upstream_compatibility \
  tests.extensions.test_core_bridge -v
```

Expected: PASS.

- [ ] **Step 4: Verify normal unittest discovery includes the extension test package**

Run:

```bash
uv run --locked --extra dev python -m unittest discover -s tests -p 'test_*.py' -v > /tmp/coding-tools-tests.log 2>&1
rg 'test_upstream_compatibility|test_extension_lifecycle|test_projects_extension' /tmp/coding-tools-tests.log
```

Expected: each module appears. If discovery already includes them, do not edit CI workflow.

- [ ] **Step 5: Commit Task 9**

```bash
git add tests/extensions .github/workflows/compliance.yml
git commit -m "test: harden extension bridge compatibility"
```

If `.github/workflows/compliance.yml` was not changed, omit it from `git add`.

---

### Task 10: Document the Extension Runtime and Operator Configuration Contract

**Files:**
- Create: `docs/extensions.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/tools-and-schemas.md`
- Modify: `docs/services-launcher.md`
- Modify: `README.md` with one short link/overview only; do not duplicate the full extension document.
- Modify: `SPEC.md` only if it currently claims the fork tool catalog can never be startup-composed; preserve upstream protocol claims that remain true.

**Interfaces:**
- Produces operator/developer documentation matching the actual implementation.
- Documents the default 22-tool composition and explicit disabled-extension behavior without pretending the fork is behavior-identical to upstream.

- [ ] **Step 1: Write `docs/extensions.md` from the implemented contract, not the design-only API**

Include these exact sections:

```markdown
# Internal Extensions

## Why the fork uses extensions
## Built-in extensions
## Configuration files
## Precedence
## Enabling and disabling extensions
## Local/private configuration
## Extension lifecycle
## Tool contributions and decorators
## Service capabilities
## Upstream synchronization bridge
## Adding a new internal extension
## Non-goals
```

The config example must match production names exactly:

```toml
config_version = 1

[extensions]
enabled = ["projects"]

[extensions.projects]
```

Document:

```bash
coding-tools-mcp --workspace /path/to/workspace --extensions projects
coding-tools-mcp --workspace /path/to/workspace --extensions ''
```

and the environment equivalent:

```bash
CODING_TOOLS_MCP_EXTENSIONS=projects
```

- [ ] **Step 2: Document privacy and precedence exactly once in quickstart**

`docs/quickstart.md` should say:

```text
coding-tools.toml is safe to commit and defines public/default composition.
coding-tools.local.toml is host-specific, ignored by Git, and overrides only declared fields.
CLI > environment > local TOML > public TOML > built-in defaults.
```

Do not put real host paths in examples.

- [ ] **Step 3: Update tool/schema documentation to explain startup composition**

Document that:

- mother-core definitions plus enabled extension contributions are composed once at startup;
- `tools/list` and `tools/call` use the same frozen catalog;
- `listChanged=false` remains correct during a process lifetime;
- disabling `projects` removes `list_skills` and `read_skill` for that process;
- tool decorators are deterministic and additive in V1.

- [ ] **Step 4: Document launcher ownership boundary**

In `docs/services-launcher.md`, explicitly state the launcher starts the MCP checkout and may select environment/CLI startup inputs, but it does not parse extension TOML. The runtime package is the sole parser/source of truth.

- [ ] **Step 5: Run required docs/schema tests**

Use existing Make targets from CI:

```bash
make test-docs-required
make test-schema-drift
```

Expected: PASS.

- [ ] **Step 6: Run public-fork hygiene after documentation changes**

```bash
uv run --locked --extra dev python -m unittest tests.test_public_fork_hygiene -v
```

Expected: PASS, ensuring no real local paths/config leaked into documentation.

- [ ] **Step 7: Commit Task 10**

```bash
git add README.md SPEC.md docs coding-tools.toml
git commit -m "docs: document internal extension runtime"
```

Only stage `SPEC.md` if it actually required a consistency edit.

---

### Task 11: Phase 0 Full Verification and Acceptance Gate

**Files:**
- No implementation file should be intentionally changed in this task.
- Generated compliance evidence may change only if the repository's existing commands produce tracked reports; inspect status before committing anything and do not commit generated local evidence unless repository policy explicitly requires it.

**Interfaces:**
- Consumes the completed Phase 0A–0D implementation.
- Produces verification evidence for the acceptance criteria; no push/merge.

- [ ] **Step 1: Verify Git state and review the complete implementation delta**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
git diff --check HEAD~10..HEAD
```

Expected: no unstaged/staged source changes before verification; `git diff --check` exits 0. Adjust the diff range to the first Phase 0 commit if more than 10 commits were created.

- [ ] **Step 2: Run the complete extension architecture suite**

```bash
uv run --locked --extra dev python -m unittest discover -s tests/extensions -p 'test_*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run all migrated project/skill tests**

```bash
uv run --locked --extra dev python -m unittest \
  tests.test_project_catalog \
  tests.test_project_skills_integration \
  tests.test_project_skills_runtime -v
```

Expected: PASS.

- [ ] **Step 4: Run upstream-sensitive protocol/schema/dispatch gates**

```bash
make test-protocol
make test-schema-drift
make check-dispatch-inputs
```

Expected: PASS.

- [ ] **Step 5: Run lint and typecheck**

```bash
make lint
make typecheck
```

Expected: PASS.

- [ ] **Step 6: Run full unit/integration/npm gates**

```bash
make test
make test-integration
make check-npm-launcher
```

Expected: PASS.

- [ ] **Step 7: Run the repository's complete local Mise verification**

```bash
mise run verify
```

Expected: Ruff clean, unittest discovery green, npm launcher test/pack gate green.

- [ ] **Step 8: Run the full compliance command if runtime budget permits**

```bash
make compliance
```

Expected: PASS. If this command produces environment-specific failures unrelated to Phase 0, preserve the exact command/output and diagnose rather than weakening tests.

- [ ] **Step 9: Verify Phase 0 acceptance behavior directly**

Run a small Python smoke against temporary workspaces:

```bash
uv run --locked --extra dev python - <<'PY'
import tempfile
from pathlib import Path

from coding_tools_mcp.extensions import RuntimeConfig, builtin_extension_registry
from coding_tools_mcp.server import Runtime

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n", encoding="utf-8")

    registry = builtin_extension_registry()
    default = Runtime(root, extension_registry=registry)
    try:
        assert len(default.exposed_tool_names()) == 22
        assert {"list_skills", "read_skill"} <= set(default.exposed_tool_names())
    finally:
        default.close()

    disabled = Runtime(
        root,
        extension_registry=registry,
        extension_config=RuntimeConfig.defaults(enabled=()),
    )
    try:
        assert len(disabled.exposed_tool_names()) == 20
        assert "list_skills" not in disabled.exposed_tool_names()
        assert "read_skill" not in disabled.exposed_tool_names()
    finally:
        disabled.close()
PY
```

Expected: exit 0.

- [ ] **Step 10: Verify the public/private config boundary**

Run:

```bash
git check-ignore -v coding-tools.local.toml
git ls-files --error-unmatch coding-tools.toml
if git ls-files --error-unmatch coding-tools.local.toml >/dev/null 2>&1; then
  echo 'coding-tools.local.toml must never be tracked' >&2
  exit 1
fi
```

Expected: local config is ignored, public config is tracked, local config is not tracked.

- [ ] **Step 11: Verify mother-core bridge isolation**

Run:

```bash
if rg -n 'extensions\.(projects|semantic|work_items|hooks|gateway)' coding_tools_mcp/server.py; then
  echo 'mother core imports extension-private modules' >&2
  exit 1
fi

uv run --locked --extra dev python -m unittest tests.extensions.test_upstream_compatibility -v
```

Expected: no private extension imports; test PASS.

- [ ] **Step 12: Inspect final status; do not push**

```bash
git status --short --branch
```

Expected: clean working tree, local Phase 0 commits ahead of `origin/main`. Stop here for review. Do not push.

---

## Spec Coverage Checklist

The implementer must confirm these mappings during execution:

| Approved spec requirement | Plan coverage |
| --- | --- |
| Static internal modules only | Tasks 2, 8 |
| No arbitrary TOML imports/code | Task 1 |
| Declarative acyclic dependencies | Task 2 |
| Services/capabilities | Task 3 |
| ToolContribution | Task 4 |
| ToolDecorator | Task 4 + bridge proof Task 9 |
| Server metadata contribution | Tasks 4, 5, 6 |
| Deterministic lifecycle | Task 5 |
| Freeze before start/transport | Tasks 5, 7 |
| Public + local TOML | Task 1 |
| Strict versioned validation | Task 1 |
| Layer precedence | Tasks 1, 7 |
| Local config Git hygiene | Tasks 1, 10, 11 |
| Launcher does not own parser | Task 7 + docs Task 10 |
| Minimal mother-core bridge | Tasks 6, 9 |
| Runtime degradation leaves catalog stable | Host API in Task 5; concrete backend degradation remains for future semantic extension |
| First real extension extraction | Task 8 |
| Project/semantic designs consume foundation | Task 8 establishes `projects`; semantic remains explicitly out of scope until Phase B |
| Upstream-sync compatibility gates | Task 9 + final Task 11 |

No Phase 0 task implements the later multi-project `ProjectRegistry` identity model or Serena. The `PROJECT_CATALOG` capability created in Task 8 is the current structural single-workspace catalog; Phase A may introduce the stable configured `ProjectRegistry` as a separate capability and migrate consumers deliberately rather than overloading the two concepts.
