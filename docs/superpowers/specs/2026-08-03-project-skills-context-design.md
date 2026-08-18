# Project-Scoped Skills Context Design

## Status

HISTORICAL EXECUTION PLAN. The project/skill context work is implemented and
superseded as a standalone delivery by the configured multi-project `projects`
extension and HostConfig v2. The historical branch/stack references below are
not current repository state.

## Problem

The MCP workspace can contain several first-level repositories and nested
repositories. The current project-context loader treats the entire configured
workspace as one project. On a large parent workspace this causes broad scans,
can reach the 20,000-file limit, and fails to expose the instructions that are
actually relevant to a requested path or command work directory.

The workspace also contains reusable agent skills under `.agents/skills` and
compatibility views under `.claude/skills`. Loading all skill bodies into MCP
initialization instructions would waste context and could apply instructions
from unrelated repositories. Requiring the model to rediscover projects and
skills before every operation would add avoidable calls and would not survive
ephemeral HTTP sessions reliably.

## Goals

1. Discover first-level projects and nested subprojects with bounded work.
2. Resolve the applicable project context from an explicit `path` or `workdir`.
3. Expose project-scoped skill metadata without injecting complete skill bodies.
4. Load a selected skill under demand through a read-only MCP tool.
5. Preserve root-project authority while allowing nested subprojects to add
   specialized skills.
6. Deduplicate `.agents` and `.claude` aliases, including Windows junctions.
7. Keep existing file and command tool contracts unchanged.

## Non-goals

- Adding a `project` argument to every existing tool.
- Maintaining a session-local active project as authoritative state.
- Running a separate MCP process or tunnel for each repository.
- Executing skill scripts automatically.
- Implementing hooks, lifecycle events, or Claude Code compatibility beyond
  reading the two approved skill directory formats.
- Treating arbitrary nested directories as projects without recognized markers.

## Terminology

### Workspace

The canonical directory configured for one MCP server instance.

### Main project

If the workspace root contains a recognized project marker, the workspace root
is the sole main project and its stable project ID is `.`. Otherwise, each direct
child of the workspace containing at least one recognized project marker is a
main project. Initial markers are:

- `.git`
- `pyproject.toml`
- `package.json`
- `Cargo.toml`
- `go.mod`
- `pom.xml`
- `build.gradle`
- `build.gradle.kts`

A directory with multiple markers is still one project. Non-root project IDs are
their workspace-relative POSIX paths.

### Subproject

A descendant of a main project containing a recognized project marker. A
subproject contributes context only when the requested `path` or `workdir` is
inside that subproject.

### Skill

A directory containing a UTF-8 `SKILL.md` under one of these locations:

- `.agents/skills/<name>/SKILL.md`
- `.claude/skills/<name>/SKILL.md`

The document must contain YAML frontmatter with non-empty string fields `name`
and `description`.

## Architecture

### Project catalog

Add a focused `project_catalog.py` module. It owns immutable project metadata and
bounded discovery. It must not depend on MCP transport or tool serialization.

The catalog first tests the workspace root for project markers. When the root is
not itself a project, it discovers main projects from workspace direct children.
It discovers nested project boundaries lazily for the main project selected by a
requested path. Discovery ignores known generated or dependency directories, including
`.git`, `.worktrees`, `.venv`, `venv`, `node_modules`, `dist`, `build`, `target`,
cache directories, and directories excluded by the existing workspace policy.

Each project record contains:

- stable project ID;
- workspace-relative root;
- project kind markers;
- main-project or subproject classification;
- parent project ID for subprojects;
- instruction-file summary;
- skill count and catalog warnings.

The server builds the lightweight main-project catalog at startup and caches it
for the runtime lifetime. Nested discovery, project instruction discovery, and
skill parsing are lazy and cached per project root. The existing broad recursive
parent-workspace context scan is replaced by bounded workspace-root instruction
loading plus lazy project-scoped context. No correctness requirement depends on
MCP session-local state.

### Project resolution

Project resolution accepts a workspace-safe existing directory or file path. For
commands, the caller-provided `workdir` is authoritative. For file tools, the
caller-provided `path` is authoritative. The new skill tools accept an explicit
`workdir` argument and default it to `.` only for compatibility with the current
runtime default cwd.

Resolution selects:

1. the containing main project;
2. all containing subprojects from shallowest to deepest;
3. the effective instruction and skill context for that directory.

Paths at the workspace root or outside every discovered main project return an
empty project-scoped skill catalog rather than guessing.

### Skill catalog

Add a focused `skill_catalog.py` module. It parses only approved `SKILL.md`
locations and returns bounded metadata records:

- `name`;
- `description`;
- owning project ID;
- canonical workspace-relative source path;
- scope root;
- source format (`agents` or `claude`);
- whether the body would be truncated by the read limit;
- warnings associated with that skill.

Physical-path resolution is used to deduplicate symlinks and Windows junctions.
If `.claude/skills/effect-ts` resolves to `.agents/skills/effect-ts`, the catalog
contains one skill whose canonical source is the `.agents` path. A resolved path
outside the workspace is rejected.

The parser accepts simple YAML frontmatter sufficient for scalar `name` and
`description` values. It does not execute YAML tags or construct arbitrary
objects. Invalid UTF-8, missing frontmatter, missing required fields, duplicate
names in the same scope, and unsafe paths produce bounded warnings and exclude
the invalid entry.

### Effective skill precedence

For a resolved work directory:

1. Skills owned by the main-project root are inserted first.
2. Skills from applicable subprojects are considered from shallowest to deepest.
3. A nested skill may add a new name.
4. A nested skill may not replace a name already supplied by the main-project
   root or a shallower applicable scope.

Therefore the root project is authoritative on collisions. This rule is stable
and visible in tool output through each skill's `owner_project` and `scope_root`.

Skills in child repositories that do not contain the requested work directory
are never returned. For example, skills in `project/repos/effect` are not
available while working elsewhere in `project`.

### MCP interface

Add two read-only, idempotent tools.

#### `list_skills`

Input:

```json
{
  "workdir": "seace-minor-sdk/src"
}
```

Output includes:

- resolved workdir;
- selected main project;
- applicable subprojects;
- applicable root and nested instruction-file paths that should be read before
  modifying files in their scopes;
- effective skills with name, description, owner, scope, and source;
- bounded warnings.

The tool returns metadata only. It does not include skill bodies.

#### `read_skill`

Input:

```json
{
  "workdir": "seace-minor-sdk/src",
  "skill": "effect-ts"
}
```

The server first computes the effective skill catalog for `workdir`, then reads
only the selected effective skill. Callers cannot bypass precedence by passing a
raw path. Output includes metadata, complete or bounded content, byte counts,
and a `truncated` flag.

Unknown skill names return a structured validation error containing the bounded
list of available effective names. A skill that exists elsewhere in the
workspace but is out of scope is treated as unavailable and its location is not
leaked through the error.

### Initialization and server information

MCP initialization instructions remain compact. They state that project-scoped
skills may be discovered with `list_skills` and must be loaded with `read_skill`
when their descriptions apply. Initialization does not inject skill bodies.

`server_info` adds a compact `project_catalog` section containing main project
IDs, roots, markers, counts, and warnings. It does not recursively list all
subprojects or skill bodies. Existing `project_context` fields remain compatible,
but their construction must no longer trigger a recursive scan of the entire
parent workspace.

## Limits

The implementation defines explicit constants and tests for them. Initial
limits are:

- 256 main projects;
- 256 discovered subprojects per main project;
- 128 skills per scope;
- 512 effective skills per request;
- 16 KiB maximum `SKILL.md` body returned by `read_skill`;
- 1 KiB maximum frontmatter section parsed for metadata;
- 128 warnings returned per operation;
- existing project-context depth and path-safety limits where applicable.

Limit exhaustion produces deterministic warnings and partial bounded results,
not unbounded scanning.

## Security

- Every discovered and read path is resolved against the canonical workspace.
- Directory links may be followed only to determine physical identity and only
  when the result remains inside the workspace.
- Skill lookup accepts names, not raw source paths.
- Skill documents are data; the MCP never executes scripts referenced by them.
- Skill content is subject to byte limits and UTF-8 validation.
- Tool errors do not reveal out-of-scope skill locations.
- Discovery does not traverse ignored dependency, build, cache, or worktree
  directories.

## Error model

New structured error codes:

- `PROJECT_NOT_FOUND` when a requested path claims a project-scoped operation but
  is not within a discovered project;
- `SKILL_NOT_FOUND` when the effective catalog does not contain the requested
  name;
- `SKILL_INVALID` when a selected cached record becomes unreadable or invalid;
- existing path validation and workspace-escape errors for unsafe workdirs.

Catalog-level malformed entries produce warnings rather than failing unrelated
valid skills.

## Testing

### Unit tests

- main-project discovery from each marker;
- workspace-root project discovery and stable `.` ID;
- deterministic IDs and ordering;
- bounded nested discovery;
- no traversal through excluded directories;
- path-to-project resolution for files and directories;
- `.agents` discovery;
- `.claude` discovery;
- junction and symlink deduplication;
- workspace escape rejection;
- frontmatter parsing and invalid metadata warnings;
- root-skill precedence over same-name nested skills;
- nested skill addition for workdirs inside the subproject;
- isolation of sibling and child repositories;
- content truncation and UTF-8 handling;
- deterministic warning and skill limits.

### Runtime tests

- tool registry and schema coverage for `list_skills` and `read_skill`;
- read-only and idempotent annotations;
- `server_info` compact project summary;
- initialization instructions advertise on-demand loading without embedding
  bodies;
- structured `SKILL_NOT_FOUND` behavior;
- session reconnection does not change explicit-workdir resolution.

### Integration fixture

Create a temporary workspace containing:

- one unrelated project;
- one main project with a root skill;
- one nested repository with an additional skill and a same-name override;
- `.claude` aliasing the root `.agents` skill;
- one unsafe outward link.

Verify that work in the main project sees only the root skill, work in the nested
repository sees the root skill plus the additional nested skill, the attempted
override loses to the root, the alias is deduplicated, and the unsafe link is
excluded.

## Compatibility and rollout

The change is additive: existing tools retain their schemas and semantics. The
new tools use explicit `workdir`, so ephemeral HTTP sessions do not affect
correctness. No persisted catalog is introduced; restarting the MCP rebuilds
bounded caches from the workspace.

The feature ships first on `feat/project-catalog-context` for local validation.
After tests and a live MCP restart confirm discovery against the real workspace,
the branch may be pushed to the fork. A later feature may add hooks that consume
the same project and skill resolution interfaces, but hooks are explicitly out
of scope here.

## Acceptance criteria

1. `server_info` no longer relies on a single recursive parent-workspace scan to
   communicate useful project context.
2. A workspace configured directly at a repository root resolves that root as
   main project `.` and exposes its root instructions and skills.
3. `list_skills(workdir="seace-minor-sdk")` returns the root `effect-ts` skill
   once despite the `.claude` junction.
4. A workdir under `seace-minor-sdk/repos/effect` adds its distinct nested skills
   without overriding any same-name root skill.
5. A workdir in another main project exposes none of the SDK skills.
6. `read_skill` returns only an effective skill selected by name and never reads
   an arbitrary caller-provided path.
7. All discovery and read operations remain bounded and workspace-confined.
8. The full existing test suite, Ruff, mypy, schema drift tests, and new
   integration tests pass.
