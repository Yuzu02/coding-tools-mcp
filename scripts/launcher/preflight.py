"""Deterministic deployment preflight checks for the services launcher."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable, Literal

from .config import ServiceConfig


@dataclass(frozen=True)
class PreflightFinding:
    code: str
    severity: Literal["error", "warning"]
    message: str
    details: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.details:
            payload["details"] = {key: value for key, value in self.details}
        return payload


@dataclass(frozen=True)
class PreflightReport:
    fingerprint: str | None
    findings: tuple[PreflightFinding, ...]

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "fingerprint": self.fingerprint,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _writable_root(path: Path) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    if not path.is_dir() or mode & 0o222 == 0:
        return False

    sentinel = path / ".coding-tools-mcp-preflight-write-probe"
    descriptor: int | None = None
    try:
        descriptor = os.open(sentinel, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, b"preflight\n")
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            sentinel.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return False
    return True


def _root_findings(config: ServiceConfig) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    snapshot = config.config_snapshot
    if snapshot is None:
        registered = (("default", config.workspace),)
    else:
        registered = tuple(
            (project.project_id, project.root.resolve(strict=False))
            for project in snapshot.registered_projects
        )

    seen: dict[Path, str] = {}
    for project_id, root in registered:
        previous = seen.get(root)
        if previous is not None:
            findings.append(
                PreflightFinding(
                    code="PROJECT_ROOT_DUPLICATE",
                    severity="error",
                    message="Registered project roots must be canonically unique.",
                    details=(("project_id", project_id), ("conflicts_with", previous)),
                )
            )
            continue
        seen[root] = project_id
        if not root.is_dir():
            findings.append(
                PreflightFinding(
                    code="PROJECT_ROOT_NOT_VISIBLE",
                    severity="error",
                    message="A registered project root is not visible in this namespace.",
                    details=(("project_id", project_id), ("root", str(root))),
                )
            )

    host_config = snapshot.host_config if snapshot is not None else None
    runtime_config = host_config.runtime if host_config is not None else None
    if runtime_config is None:
        return findings

    source_roots = tuple(root for _project_id, root in registered)
    for label, path in (
        ("RUNTIME", runtime_config.runtime_root),
        ("STATE", runtime_config.state_root),
        ("CACHE", runtime_config.cache_root),
    ):
        if path is None:
            continue
        resolved = path.resolve(strict=False)
        if any(_contains(source_root, resolved) for source_root in source_roots):
            findings.append(
                PreflightFinding(
                    code=f"{label}_ROOT_INSIDE_PROJECT",
                    severity="error",
                    message="Runtime state roots must stay outside registered source roots.",
                    details=(("root_kind", label.lower()), ("path", str(resolved))),
                )
            )
            continue
        if not _writable_root(resolved):
            findings.append(
                PreflightFinding(
                    code=f"{label}_ROOT_NOT_WRITABLE",
                    severity="error",
                    message="A configured runtime state root is not writable.",
                    details=(("root_kind", label.lower()), ("path", str(resolved))),
                )
            )
    return findings


def _semantic_findings(config: ServiceConfig) -> list[PreflightFinding]:
    snapshot = config.config_snapshot
    if snapshot is None or "semantic" not in snapshot.runtime_config.enabled_extensions:
        return []
    try:
        installed = metadata.version("serena-agent")
    except metadata.PackageNotFoundError:
        installed = "missing"
    if installed == "1.5.3":
        return []
    return [
        PreflightFinding(
            code="SEMANTIC_BACKEND_VERSION",
            severity="error",
            message="Semantic mode requires serena-agent 1.5.3 exactly.",
            details=(("installed_version", installed), ("required_version", "1.5.3")),
        )
    ]


def _tunnel_findings(config: ServiceConfig) -> list[PreflightFinding]:
    if config.tunnel.mode != "profile-file":
        return []
    profile_file = config.tunnel.profile_file
    if profile_file is not None and profile_file.is_file():
        return []
    return [
        PreflightFinding(
            code="TUNNEL_PROFILE_NOT_VISIBLE",
            severity="error",
            message="The configured tunnel profile file is not visible as a regular file.",
            details=(("profile_file", str(profile_file)),),
        )
    ]


def run_preflight(
    config: ServiceConfig,
    *,
    port_probe: Callable[[str, int], bool],
) -> PreflightReport:
    findings = _root_findings(config)

    snapshot = config.config_snapshot
    host_config = snapshot.host_config if snapshot is not None else None
    transport_kind = host_config.transport.kind if host_config is not None else "http"
    if transport_kind == "http":
        try:
            occupied = port_probe(config.host, config.port)
        except OSError:
            findings.append(
                PreflightFinding(
                    code="LISTENER_PORT_PROBE_FAILED",
                    severity="error",
                    message="The configured listener port could not be probed.",
                )
            )
        else:
            if occupied:
                findings.append(
                    PreflightFinding(
                        code="LISTENER_PORT_IN_USE",
                        severity="error",
                        message="The configured listener port is already in use.",
                        details=(("host", config.host), ("port", str(config.port))),
                    )
                )

    findings.extend(_semantic_findings(config))
    findings.extend(_tunnel_findings(config))
    fingerprint = snapshot.fingerprint if snapshot is not None else None
    return PreflightReport(fingerprint=fingerprint, findings=tuple(findings))
