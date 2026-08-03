# Project-Scoped Skills Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded project discovery and project-scoped skill metadata/loading so MCP clients can resolve applicable instructions and skills from an explicit work directory without changing existing tool contracts.

**Architecture:** Introduce transport-independent `project_catalog.py` and `skill_catalog.py` modules. `Runtime` owns one immutable/cached catalog per workspace, exposes `list_skills` and `read_skill`, and adds only compact catalog guidance to initialization and `server_info`.

**Tech Stack:** Python 3.11+, standard library dataclasses/pathlib, safe PyYAML parsing already present in the dev dependency set, existing MCP JSON-RPC runtime, `unittest`, Ruff, mypy.

## Global Constraints

- Main-project markers: `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `build.gradle.kts`.
- If the workspace root has a marker, it is the sole main project with ID `.`; otherwise marked direct children are main projects.
- Recognized skills are only `.agents/skills/<name>/SKILL.md` and `.claude/skills/<name>/SKILL.md`.
- Root-project skills win name collisions; applicable nested scopes may only add new names.
- Do not add `project` to existing tool schemas or rely on session-local current-project state.
- Maximums: 256 main projects, 256 subprojects per main project, 128 skills per scope, 512 effective skills, 16 KiB returned skill body, 1 KiB parsed frontmatter, 128 warnings.
- Symlinks and Windows junctions may identify aliases only when the resolved target remains inside the workspace.
- Push only to the user fork; do not open an upstream PR.

---

### Task 1: Bounded project catalog

**Files:**
- Create: `coding_tools_mcp/project_catalog.py`
- Create: `tests/test_project_catalog.py`

**Interfaces:**
- Produces: `ProjectRecord`, `ProjectSelection`, `ProjectCatalog`, `build_project_catalog(workspace: Path) -> ProjectCatalog`.
- `ProjectCatalog.resolve(raw_path: Path) -> ProjectSelection | None` accepts an existing workspace-confined file or directory.
- `ProjectSelection` exposes `main_project`, ordered `subprojects`, and `scope_chain`.

- [ ] **Step 1: Write failing discovery and resolution tests**

```python
class ProjectCatalogTests(unittest.TestCase):
    def test_workspace_root_marker_becomes_dot_main_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
            catalog = build_project_catalog(root)
            self.assertEqual([project.project_id for project in catalog.main_projects], ["."])

    def test_direct_children_are_main_projects_when_workspace_is_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "api").mkdir()
            (root / "api" / "package.json").write_text("{}", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "pyproject.toml").write_text("", encoding="utf-8")
            catalog = build_project_catalog(root)
            self.assertEqual([project.project_id for project in catalog.main_projects], ["api", "data"])

    def test_nested_project_is_only_selected_for_contained_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "sdk"
            nested = main / "repos" / "effect"
            nested.mkdir(parents=True)
            (main / "package.json").write_text("{}", encoding="utf-8")
            (nested / "package.json").write_text("{}", encoding="utf-8")
            catalog = build_project_catalog(root)
            self.assertEqual(catalog.resolve(main).subprojects, ())
            self.assertEqual(catalog.resolve(nested).subprojects[-1].project_id, "sdk/repos/effect")
```

- [ ] **Step 2: Run the focused tests and confirm import failure**

Run: `python -m unittest tests.test_project_catalog -v`

Expected: FAIL because `coding_tools_mcp.project_catalog` does not exist.

- [ ] **Step 3: Implement immutable records and bounded discovery**

```python
@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    root: Path
    display_root: str
    markers: tuple[str, ...]
    kind: Literal["main", "subproject"]
    parent_project_id: str | None

@dataclass(frozen=True)
class ProjectSelection:
    main_project: ProjectRecord
    subprojects: tuple[ProjectRecord, ...]

    @property
    def scope_chain(self) -> tuple[ProjectRecord, ...]:
        return (self.main_project, *self.subprojects)
```

Implement deterministic sorted marker checks, direct-child main discovery, lazy cached subproject discovery, excluded-directory pruning, file-to-parent resolution, and workspace confinement.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_project_catalog -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add coding_tools_mcp/project_catalog.py tests/test_project_catalog.py
git commit -m "feat: add bounded project catalog"
```

### Task 2: Project-scoped skill catalog

**Files:**
- Create: `coding_tools_mcp/skill_catalog.py`
- Create: `tests/test_skill_catalog.py`
- Modify: `coding_tools_mcp/project_catalog.py`

**Interfaces:**
- Consumes: `ProjectCatalog`, `ProjectSelection`, `ProjectRecord`.
- Produces: `SkillRecord`, `EffectiveSkillContext`, `SkillCatalog.list_for(workdir: Path)`, and `SkillCatalog.read(workdir: Path, name: str)`.
- `SkillRecord` fields: `name`, `description`, `owner_project`, `scope_root`, `source`, `source_format`, `truncated`, `warnings`.

- [ ] **Step 1: Write failing skill parsing, deduplication, precedence, and isolation tests**

```python
def write_skill(scope: Path, container: str, name: str, description: str) -> Path:
    path = scope / container / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return path

class SkillCatalogTests(unittest.TestCase):
    def test_nested_scope_adds_skill_but_cannot_override_root_name(self):
        # main skill: effect-ts, nested skills: effect-ts + jsdocs
        context = SkillCatalog(build_project_catalog(root)).list_for(nested)
        self.assertEqual([skill.name for skill in context.skills], ["effect-ts", "jsdocs"])
        self.assertEqual(context.skills[0].owner_project, "sdk")

    def test_sibling_project_does_not_see_sdk_skills(self):
        context = SkillCatalog(build_project_catalog(root)).list_for(other_project)
        self.assertEqual(context.skills, ())

    def test_claude_alias_to_agents_skill_is_deduplicated(self):
        # create symlink/junction when supported; skip otherwise
        self.assertEqual([skill.name for skill in context.skills], ["effect-ts"])

    def test_outward_symlink_is_rejected_with_warning(self):
        self.assertEqual(context.skills, ())
        self.assertTrue(any("outside workspace" in warning for warning in context.warnings))
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python -m unittest tests.test_skill_catalog -v`

Expected: FAIL because `coding_tools_mcp.skill_catalog` does not exist.

- [ ] **Step 3: Implement safe metadata parsing and effective precedence**

Use `yaml.safe_load` only on the bounded frontmatter bytes. Reject non-mapping YAML and non-string/empty `name` or `description`. Resolve each candidate `SKILL.md` and its parent directory physically, reject targets outside the workspace, deduplicate by resolved source, then order `.agents` before `.claude` and root scopes before nested scopes.

```python
@dataclass(frozen=True)
class EffectiveSkillContext:
    workdir: str
    main_project: str | None
    subprojects: tuple[str, ...]
    instruction_files: tuple[str, ...]
    skills: tuple[SkillRecord, ...]
    warnings: tuple[str, ...]
```

- [ ] **Step 4: Implement bounded body reading by effective name**

`SkillCatalog.read()` must first call `list_for()`, select by effective name, read at most `MAX_SKILL_BODY_BYTES + 1`, decode UTF-8, and return byte counts plus `truncated`. It must never accept a source path.

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest tests.test_project_catalog tests.test_skill_catalog -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add coding_tools_mcp/project_catalog.py coding_tools_mcp/skill_catalog.py tests/test_skill_catalog.py
git commit -m "feat: resolve project-scoped skills"
```

### Task 3: MCP runtime tools and compact initialization

**Files:**
- Modify: `coding_tools_mcp/server.py`
- Modify: `coding_tools_mcp/project_context.py`
- Modify: `tests/compliance/test_mcp_contract.py`
- Modify: `tests/compliance/test_runtime_helpers.py`
- Modify: `tests/compliance/test_schema_drift.py`

**Interfaces:**
- Consumes: `ProjectCatalog`, `SkillCatalog`, `EffectiveSkillContext`.
- Produces MCP tools `list_skills(workdir: str = ".")` and `read_skill(workdir: str = ".", skill: str)`.

- [ ] **Step 1: Write failing registry, schema, and annotation tests**

```python
def test_skill_tools_are_read_only_and_idempotent(self):
    tools = {tool["name"]: tool for tool in Runtime(self.workspace).list_tools()["tools"]}
    self.assertTrue(tools["list_skills"]["annotations"]["readOnlyHint"])
    self.assertTrue(tools["read_skill"]["annotations"]["idempotentHint"])

def test_read_skill_requires_skill_name(self):
    schema = input_schemas()["read_skill"]
    self.assertEqual(schema["required"], ["skill"])
    self.assertNotIn("path", schema["properties"])
```

- [ ] **Step 2: Run focused contract tests and confirm failure**

Run: `python -m unittest tests.compliance.test_mcp_contract tests.compliance.test_schema_drift -v`

Expected: FAIL because the tools and schemas are absent.

- [ ] **Step 3: Register tools and schemas**

Add `ToolSpec` entries with read-only/idempotent annotations. Add schemas:

```python
"list_skills": object_schema({"workdir": {**string, "default": "."}}),
"read_skill": object_schema(
    {"workdir": {**string, "default": "."}, "skill": {**string, "minLength": 1}},
    ["skill"],
),
```

- [ ] **Step 4: Wire one shared catalog into Runtime**

Construct `ProjectCatalog` and `SkillCatalog` once per server/runtime-owned workspace tree and reuse them across ephemeral HTTP runtimes in the same way `ProjectContext` is shared today. Keep constructor injection available for tests.

- [ ] **Step 5: Implement tool payloads and structured errors**

`list_skills` returns resolved workdir, main project, subprojects, instruction paths, effective skill metadata, and warnings. `read_skill` returns the selected metadata and bounded content. Convert missing project/skill and invalid cached content into `PROJECT_NOT_FOUND`, `SKILL_NOT_FOUND`, or `SKILL_INVALID` `ToolFailure` instances; `SKILL_NOT_FOUND.details.available` contains only effective names.

- [ ] **Step 6: Replace broad parent-workspace initialization scan**

Keep workspace-root `AGENTS.md`/`CLAUDE.md` loading, but do not recursively scan the entire parent workspace during initialization. Append compact instructions explaining that clients should call `list_skills` for an explicit workdir and `read_skill` when a listed description applies.

- [ ] **Step 7: Add compact `server_info.project_catalog` tests and implementation**

Assert main project IDs/roots/markers/counts are present while bodies and recursively enumerated subprojects are absent. Preserve existing `project_context` keys.

- [ ] **Step 8: Run focused runtime tests**

Run: `python -m unittest tests.compliance.test_mcp_contract tests.compliance.test_runtime_helpers tests.compliance.test_schema_drift -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add coding_tools_mcp/server.py coding_tools_mcp/project_context.py tests/compliance/test_mcp_contract.py tests/compliance/test_runtime_helpers.py tests/compliance/test_schema_drift.py
git commit -m "feat: expose project-scoped skill tools"
```

### Task 4: Integration fixture and documentation

**Files:**
- Create: `tests/test_project_skills_integration.py`
- Modify: `README.md`
- Modify: `SPEC.md`
- Modify: `docs/runtime-contract-v0.2.md`

**Interfaces:**
- Validates the complete public contract through `Runtime.call_tool` and explicit `workdir` values.

- [ ] **Step 1: Write the end-to-end temporary-workspace fixture**

Create unrelated and SDK main projects, a nested Effect repository, root/nested collisions, a distinct nested skill, a `.claude` alias, and an outward unsafe link. Use platform-aware skip handling only for link creation; the non-link assertions must run everywhere.

- [ ] **Step 2: Verify the integration test fails before final wiring/docs**

Run: `python -m unittest tests.test_project_skills_integration -v`

Expected: FAIL on one or more missing public payload fields or initialization guidance.

- [ ] **Step 3: Complete public docs**

Document:

```json
{"workdir":"seace-minor-sdk/src"}
```

for `list_skills`, and:

```json
{"workdir":"seace-minor-sdk/src","skill":"effect-ts"}
```

for `read_skill`. State root collision precedence, explicit-workdir reliability across reconnects, metadata-only listing, bounded content, and that referenced scripts are never executed automatically.

- [ ] **Step 4: Run integration and documentation/schema checks**

Run: `python -m unittest tests.test_project_skills_integration tests.compliance.test_docs_required tests.compliance.test_schema_drift -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_project_skills_integration.py README.md SPEC.md docs/runtime-contract-v0.2.md
git commit -m "docs: define project-scoped skills contract"
```

### Task 5: Full verification and live workspace validation

**Files:**
- Modify only if verification exposes a feature-specific defect.

**Interfaces:**
- Validates acceptance criteria and the real `seace-minor-sdk` junction layout.

- [ ] **Step 1: Run static checks**

Run: `.venv/Scripts/python.exe -m ruff check coding_tools_mcp tests`

Run: `.venv/Scripts/python.exe -m mypy coding_tools_mcp`

Expected: both exit 0.

- [ ] **Step 2: Run the complete suite**

Run: `.venv/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'`

Expected: exit 0 with no failures.

- [ ] **Step 3: Run diff and schema verification**

Run: `git diff --check c1c094e...HEAD`

Run: `.venv/Scripts/python.exe -m unittest tests.compliance.test_schema_drift -v`

Expected: exit 0.

- [ ] **Step 4: Validate against the real workspace**

Start or invoke the feature runtime against the parent workspace and verify:

- `list_skills(workdir="seace-minor-sdk")` returns one root `effect-ts` despite the `.claude` junction;
- `list_skills(workdir="seace-minor-sdk/repos/effect")` adds `grill-me`, `jsdocs`, and `scratchpad`;
- a sibling project does not receive SDK skills;
- `read_skill` returns the selected `SKILL.md` body and no arbitrary path input is available.

- [ ] **Step 5: Review branch state and push only to fork**

Run: `git status --short --branch`

Run: `git log --oneline --decorate -6`

Run: `git push -u fork feat/project-catalog-context`

Expected: clean tree, branch stacked on `c1c094e`, push succeeds only to `fork`.
