from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .project_catalog import ProjectCatalog, ProjectRecord


SKILL_CONTAINERS = ((".agents", "agents"), (".claude", "claude"))
INSTRUCTION_FILE_NAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
MAX_SKILLS_PER_SCOPE = 128
MAX_EFFECTIVE_SKILLS = 512
MAX_SKILL_BODY_BYTES = 16 * 1024
MAX_SKILL_FRONTMATTER_BYTES = 16 * 1024
MAX_SKILL_WARNINGS = 128


class ProjectNotFoundError(Exception):
    pass


class SkillNotFoundError(Exception):
    def __init__(self, name: str, available: tuple[str, ...]) -> None:
        super().__init__(f"Skill is not available for this workdir: {name}")
        self.name = name
        self.available = available


class SkillInvalidError(Exception):
    pass


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    owner_project: str
    scope_root: str
    source: str
    source_format: str
    truncated: bool
    warnings: tuple[str, ...] = ()
    resolved_source: Path = field(repr=False, compare=False, default=Path("."))

    def metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "owner_project": self.owner_project,
            "scope_root": self.scope_root,
            "source": self.source,
            "source_format": self.source_format,
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class EffectiveSkillContext:
    workdir: str
    main_project: str | None
    subprojects: tuple[str, ...]
    instruction_files: tuple[str, ...]
    skills: tuple[SkillRecord, ...]
    warnings: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "workdir": self.workdir,
            "main_project": self.main_project,
            "subprojects": list(self.subprojects),
            "instruction_files": list(self.instruction_files),
            "skills": [skill.metadata() for skill in self.skills],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LoadedSkill:
    skill: SkillRecord
    content: str
    total_bytes: int
    returned_bytes: int
    truncated: bool

    def payload(self) -> dict[str, object]:
        return {
            "skill": self.skill.metadata(),
            "content": self.content,
            "total_bytes": self.total_bytes,
            "returned_bytes": self.returned_bytes,
            "truncated": self.truncated,
        }


class SkillCatalog:
    def __init__(self, project_catalog: ProjectCatalog) -> None:
        self.project_catalog = project_catalog
        self.workspace = project_catalog.workspace
        self._scope_cache: dict[Path, tuple[tuple[SkillRecord, ...], tuple[str, ...]]] = {}
        self._cache_lock = threading.RLock()

    def list_for(self, workdir: Path | str = ".") -> EffectiveSkillContext:
        resolved = self._resolve_workdir(workdir)
        selection = self.project_catalog.resolve(resolved)
        workdir_display = _display_path(resolved, self.workspace)
        if selection is None:
            return EffectiveSkillContext(
                workdir=workdir_display,
                main_project=None,
                subprojects=(),
                instruction_files=(),
                skills=(),
                warnings=self.project_catalog.warnings[:MAX_SKILL_WARNINGS],
            )

        warnings = list(self.project_catalog.warnings[:MAX_SKILL_WARNINGS])
        instruction_files: list[str] = []
        effective: dict[str, SkillRecord] = {}
        for scope in selection.scope_chain:
            for instruction in self._instruction_files(scope, warnings):
                if instruction not in instruction_files:
                    instruction_files.append(instruction)
            records, scope_warnings = self._skills_for_scope(scope)
            for warning in scope_warnings:
                _append_warning(warnings, warning)
            for record in records:
                if record.name in effective:
                    continue
                if len(effective) >= MAX_EFFECTIVE_SKILLS:
                    _append_warning(
                        warnings,
                        f"Effective skill list truncated to {MAX_EFFECTIVE_SKILLS} entries.",
                    )
                    break
                effective[record.name] = record

        return EffectiveSkillContext(
            workdir=workdir_display,
            # Historical response field names are preserved for compatibility;
            # these values identify structural scopes, not configured project IDs.
            main_project=selection.main_project.scope_id,
            subprojects=tuple(project.scope_id for project in selection.subprojects),
            instruction_files=tuple(instruction_files),
            skills=tuple(effective.values()),
            warnings=tuple(warnings),
        )

    def read(self, workdir: Path | str, name: str) -> LoadedSkill:
        context = self.list_for(workdir)
        if context.main_project is None:
            raise ProjectNotFoundError(f"No project contains workdir: {context.workdir}")
        by_name = {skill.name: skill for skill in context.skills}
        record = by_name.get(name)
        if record is None:
            raise SkillNotFoundError(name, tuple(by_name))
        try:
            total_bytes = record.resolved_source.stat().st_size
            with record.resolved_source.open("rb") as handle:
                data = handle.read(MAX_SKILL_BODY_BYTES + 1)
        except OSError as exc:
            raise SkillInvalidError(f"Skill could not be read: {name}: {exc}") from exc
        returned = data[:MAX_SKILL_BODY_BYTES]
        try:
            content = returned.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillInvalidError(f"Skill is not valid UTF-8: {name}") from exc
        truncated = total_bytes > MAX_SKILL_BODY_BYTES or len(data) > MAX_SKILL_BODY_BYTES
        return LoadedSkill(
            skill=record,
            content=content,
            total_bytes=total_bytes,
            returned_bytes=len(returned),
            truncated=truncated,
        )

    def _resolve_workdir(self, raw_workdir: Path | str) -> Path:
        candidate = Path(raw_workdir).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve(strict=True)
        if resolved.is_file():
            resolved = resolved.parent
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Skill workdir must remain inside the configured workspace.") from exc
        return resolved

    def _instruction_files(self, scope: ProjectRecord, warnings: list[str]) -> tuple[str, ...]:
        found: list[str] = []
        physical_paths: set[Path] = set()
        for name in INSTRUCTION_FILE_NAMES:
            candidate = scope.root / name
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.workspace)
            except (OSError, ValueError):
                _append_warning(warnings, f"Skipped unsafe instruction file: {_safe_display(candidate, self.workspace)}")
                continue
            if resolved in physical_paths:
                continue
            physical_paths.add(resolved)
            found.append(_display_path(candidate, self.workspace))
        return tuple(found)

    def _skills_for_scope(self, scope: ProjectRecord) -> tuple[tuple[SkillRecord, ...], tuple[str, ...]]:
        with self._cache_lock:
            cached = self._scope_cache.get(scope.root)
            if cached is not None:
                return cached

        warnings: list[str] = []
        records: list[SkillRecord] = []
        physical_sources: set[Path] = set()
        names: set[str] = set()
        candidates_seen = 0
        for container, source_format in SKILL_CONTAINERS:
            skills_root = scope.root / container / "skills"
            if not skills_root.is_dir():
                continue
            try:
                physical_root = skills_root.resolve(strict=True)
                physical_root.relative_to(self.workspace)
            except (OSError, ValueError):
                _append_warning(
                    warnings,
                    f"Skipped skill directory outside workspace: {_safe_display(skills_root, self.workspace)}",
                )
                continue
            try:
                children = sorted(skills_root.iterdir(), key=lambda path: path.name.casefold())
            except OSError as exc:
                _append_warning(
                    warnings,
                    f"Could not list skill directory {_safe_display(skills_root, self.workspace)}: {exc}",
                )
                continue
            for child in children:
                if candidates_seen >= MAX_SKILLS_PER_SCOPE:
                    _append_warning(
                        warnings,
                        f"Skill list for {scope.display_root} truncated to {MAX_SKILLS_PER_SCOPE} entries.",
                    )
                    break
                skill_file = child / "SKILL.md"
                if not skill_file.is_file():
                    continue
                candidates_seen += 1
                try:
                    physical_source = skill_file.resolve(strict=True)
                    physical_source.relative_to(self.workspace)
                except (OSError, ValueError):
                    _append_warning(
                        warnings,
                        f"Skipped skill outside workspace: {_safe_display(skill_file, self.workspace)}",
                    )
                    continue
                if physical_source in physical_sources:
                    continue
                try:
                    name, description = _parse_frontmatter(physical_source)
                    total_bytes = physical_source.stat().st_size
                except (OSError, ValueError, UnicodeDecodeError) as exc:
                    _append_warning(
                        warnings,
                        f"Skipped invalid skill {_safe_display(skill_file, self.workspace)}: {exc}",
                    )
                    continue
                physical_sources.add(physical_source)
                if name in names:
                    _append_warning(
                        warnings,
                        f"Skipped duplicate skill name {name!r} in scope {scope.display_root}.",
                    )
                    continue
                names.add(name)
                records.append(
                    SkillRecord(
                        name=name,
                        description=description,
                        # Historical field name: this is the structural scope owner.
                        owner_project=scope.scope_id,
                        scope_root=scope.display_root,
                        source=_display_path(skill_file, self.workspace),
                        source_format=source_format,
                        truncated=total_bytes > MAX_SKILL_BODY_BYTES,
                        resolved_source=physical_source,
                    )
                )
        result = (tuple(records), tuple(warnings))
        with self._cache_lock:
            self._scope_cache[scope.root] = result
        return result


def _parse_frontmatter(path: Path) -> tuple[str, str]:
    with path.open("rb") as handle:
        first_line = handle.readline()
        if not first_line or first_line.strip() != b"---":
            raise ValueError("missing YAML frontmatter")

        consumed = len(first_line)
        frontmatter_lines: list[str] = []
        while True:
            line = handle.readline()
            if not line:
                raise ValueError("unterminated YAML frontmatter")
            consumed += len(line)
            if consumed > MAX_SKILL_FRONTMATTER_BYTES:
                raise ValueError(f"frontmatter exceeds {MAX_SKILL_FRONTMATTER_BYTES} bytes")
            if line.strip() == b"---":
                break
            frontmatter_lines.append(line.decode("utf-8").rstrip("\r\n"))

    values: dict[str, str] = {}
    index = 0
    while index < len(frontmatter_lines):
        raw_line = frontmatter_lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent:
            raise ValueError("frontmatter must use scalar key: value fields")
        if ":" not in raw_line:
            raise ValueError("frontmatter must use scalar key: value fields")

        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if key not in {"name", "description"}:
            index += 1
            if not value:
                while index < len(frontmatter_lines):
                    nested = frontmatter_lines[index]
                    if nested.strip() and not nested.startswith(" "):
                        break
                    index += 1
            continue

        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            block_lines: list[str] = []
            index += 1
            while index < len(frontmatter_lines):
                nested = frontmatter_lines[index]
                if nested.strip() and not nested.startswith(" "):
                    break
                block_lines.append(nested)
                index += 1
            values[key] = _parse_block_scalar(value, block_lines)
            continue

        values[key] = _parse_scalar(value)
        index += 1
    for required in ("name", "description"):
        if not values.get(required, "").strip():
            raise ValueError(f"frontmatter requires non-empty {required}")
    return values["name"].strip(), values["description"].strip()


def _parse_block_scalar(marker: str, raw_lines: list[str]) -> str:
    non_empty = [line for line in raw_lines if line.strip()]
    if not non_empty:
        return ""
    indentation = min(len(line) - len(line.lstrip(" ")) for line in non_empty)
    if indentation <= 0:
        raise ValueError("block scalar content must be indented")
    lines = [line[indentation:] if line.strip() else "" for line in raw_lines]

    if marker.startswith("|"):
        value = "\n".join(lines)
    else:
        paragraphs: list[str] = []
        current: list[str] = []
        for line in lines:
            if line == "":
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                paragraphs.append("")
            else:
                current.append(line)
        if current:
            paragraphs.append(" ".join(current))
        value = "\n".join(paragraphs)

    if marker.endswith("-"):
        return value.rstrip("\n")
    if marker.endswith("+"):
        return value + "\n"
    return value.rstrip("\n") + "\n"


def _parse_scalar(raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith('"'):
        if not raw.endswith('"'):
            raise ValueError("unterminated quoted scalar")
        value = json.loads(raw)
        if not isinstance(value, str):
            raise ValueError("frontmatter values must be strings")
        return value
    if raw.startswith("'"):
        if not raw.endswith("'"):
            raise ValueError("unterminated quoted scalar")
        return raw[1:-1].replace("''", "'")
    if raw in {"|", ">"} or raw.startswith(("!", "&", "*", "[", "{")):
        raise ValueError("only scalar frontmatter values are supported")
    return raw


def _display_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."


def _safe_display(path: Path, root: Path) -> str:
    try:
        return _display_path(path, root)
    except ValueError:
        return path.name


def _append_warning(warnings: list[str], message: str) -> None:
    if len(warnings) < MAX_SKILL_WARNINGS:
        warnings.append(message)
