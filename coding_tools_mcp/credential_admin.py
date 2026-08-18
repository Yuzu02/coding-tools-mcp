"""Root-only administration for the host credential broker.

This module is intentionally not imported by the MCP runtime.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .credential_providers import CredentialProviderRegistry, atomic_write_fragment


class CredentialAdminError(ValueError):
    pass


@dataclass(frozen=True)
class ProvisionRequest:
    name: str
    commands: tuple[str, ...]
    source: Path
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ("state",)
    env_passthrough: tuple[str, ...] = ()
    env_paths: tuple[tuple[str, str], ...] = ()


def require_root(*, operation: str, euid: int | None = None) -> None:
    effective_uid = os.geteuid() if euid is None else euid
    if effective_uid != 0:
        raise CredentialAdminError(f"{operation} requires explicit root execution")


def _safe_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name or "/" in name or "\\" in name:
        raise CredentialAdminError("provider broker subtree requires a safe provider name")


def validated_provider_subtree(broker_dir: Path, name: str) -> Path:
    _safe_name(name)
    broker_input = Path(broker_dir).expanduser()
    broker = broker_input.resolve(strict=False)
    raw_target = broker_input / name
    for part in (raw_target, *raw_target.parents):
        if part == Path("."):
            break
        if part.is_symlink():
            raise CredentialAdminError("provider broker subtree contains symlink")
        if part == broker_input:
            break
    target = raw_target.resolve(strict=False)
    try:
        target.relative_to(broker)
    except ValueError as exc:
        raise CredentialAdminError("provider broker subtree escapes broker root") from exc
    if target == broker:
        raise CredentialAdminError("provider broker subtree must be a descendant")
    return target


def _check_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise CredentialAdminError("credential source must be a regular directory")
    for item in path.rglob("*"):
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            raise CredentialAdminError("credential source contains unsafe filesystem node")
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise CredentialAdminError("credential source contains unsafe filesystem node")


class CredentialAdmin:
    def __init__(self, registry_dir: Path, broker_dir: Path, *, service_uid: int | None = None, service_gid: int | None = None) -> None:
        self.registry_dir = Path(registry_dir).expanduser().resolve(strict=False)
        self.broker_dir = Path(broker_dir).expanduser().resolve(strict=False)
        self.service_uid = os.getuid() if service_uid is None else service_uid
        self.service_gid = os.getgid() if service_gid is None else service_gid

    def _fragment(self, request: ProvisionRequest, target: Path) -> str:
        _safe_name(request.name)
        roots: dict[str, tuple[str, ...]] = {}
        for field, values in (("read_roots", request.read_roots), ("write_roots", request.write_roots)):
            checked: list[str] = []
            for value in values:
                candidate = (target / value).resolve(strict=False)
                try:
                    candidate.relative_to(target)
                except ValueError as exc:
                    raise CredentialAdminError("declared root escapes provider broker subtree") from exc
                if candidate == target or Path(value).is_absolute() or any(p == ".." for p in Path(value).parts):
                    raise CredentialAdminError("declared root escapes provider broker subtree")
                checked.append(str(candidate))
            roots[field] = tuple(checked)
        env_paths: list[str] = []
        for key, value in request.env_paths:
            candidate = (target / value).resolve(strict=False)
            try:
                candidate.relative_to(target)
            except ValueError as exc:
                raise CredentialAdminError("declared environment path escapes provider broker subtree") from exc
            env_paths.append(f"{key}={candidate}")
        import json
        lines = [f"name = {json.dumps(request.name)}", "commands = [" + ", ".join(json.dumps(x) for x in request.commands) + "]"]
        lines += [f"{key} = [" + ", ".join(json.dumps(x) for x in values) + "]" for key, values in roots.items() if values]
        if request.env_passthrough:
            lines.append("env_passthrough = [" + ", ".join(json.dumps(x) for x in request.env_passthrough) + "]")
        if env_paths:
            lines.append("env_paths = [" + ", ".join(json.dumps(x) for x in env_paths) + "]")
        return "\n".join(lines) + "\n"

    def provision(self, request: ProvisionRequest, *, apply: bool = False, euid: int | None = None) -> dict[str, Any]:
        target = validated_provider_subtree(self.broker_dir, request.name)
        source_path = Path(request.source).expanduser()
        _check_tree(source_path)
        fragment = self.registry_dir / f"{request.name}.toml"
        text = self._fragment(request, target)
        report: dict[str, Any] = {"action": "provision", "provider": request.name, "fragment": str(fragment), "broker": str(target), "apply": apply}
        if not apply:
            return report
        require_root(operation="provision", euid=euid)
        # Validate against the established parser before touching the broker.
        with tempfile.TemporaryDirectory(prefix=".validate-", dir=self.registry_dir.parent) as validation_root:
            validation_dir = Path(validation_root) / "registry"
            validation_dir.mkdir()
            temporary_fragment = validation_dir / f"{request.name}.toml"
            atomic_write_fragment(temporary_fragment, text)
            snapshot = CredentialProviderRegistry(validation_dir, self.broker_dir).snapshot()
            if snapshot.health != "healthy":
                raise CredentialAdminError("proposed credential provider fragment is invalid")
        self.broker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.registry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix=f".{request.name}.", dir=self.broker_dir) as temporary:
            stage = Path(temporary) / request.name
            stage.mkdir(mode=0o700)
            for root_name in (*request.read_roots, *request.write_roots):
                (stage / root_name).mkdir(parents=True, mode=0o700, exist_ok=True)
            destination = stage / (request.write_roots[0] if request.write_roots else "state")
            for item in source_path.iterdir():
                self._copy_node(item, destination / item.name)
            self._chown_tree(stage)
            if target.exists() or target.is_symlink():
                self._remove_tree(target)
            os.replace(stage, target)
        atomic_write_fragment(fragment, text)
        return report

    def _copy_node(self, source: Path, destination: Path) -> None:
        mode = source.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise CredentialAdminError("credential source contains unsafe filesystem node")
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)

    def _chown_tree(self, root: Path) -> None:
        for item in (root, *root.rglob("*")):
            os.chown(item, self.service_uid, self.service_gid)
            if item.is_dir():
                os.chmod(item, 0o700)
            else:
                os.chmod(item, 0o600)

    def _remove_tree(self, path: Path) -> None:
        if path.is_symlink():
            raise CredentialAdminError("provider broker subtree contains symlink")
        if path.exists():
            shutil.rmtree(path)

    def list(self) -> dict[str, Any]:
        snapshot = CredentialProviderRegistry(self.registry_dir, self.broker_dir).snapshot()
        return {"health": snapshot.health, "generation": snapshot.generation, "fingerprint": snapshot.fingerprint, "providers": [{"name": p.name, "commands": p.commands, "read_roots": tuple(map(str, p.read_roots)), "write_roots": tuple(map(str, p.write_roots)), "env_paths": tuple(key for key, _ in p.env_paths)} for p in snapshot.providers]}

    def doctor(self, *, system: bool = False, euid: int | None = None) -> dict[str, Any]:
        if system:
            require_root(operation="doctor --system", euid=euid)
        report = self.list()
        report["registry_dir"] = str(self.registry_dir)
        report["broker_dir"] = str(self.broker_dir)
        report["system_requested"] = system
        report["checks"] = {
            "registry_directory": self._directory_check(self.registry_dir),
            "broker_directory": self._directory_check(self.broker_dir),
        }
        return report

    @staticmethod
    def _directory_check(path: Path) -> dict[str, Any]:
        if path.is_symlink():
            return {"exists": True, "safe": False, "mode": None}
        try:
            mode = path.stat().st_mode
        except OSError:
            return {"exists": False, "safe": True, "mode": None}
        return {"exists": True, "safe": stat.S_ISDIR(mode), "mode": oct(mode & 0o777)}

    def remove(self, name: str, *, apply: bool = False, euid: int | None = None) -> dict[str, Any]:
        target = validated_provider_subtree(self.broker_dir, name)
        fragment = self.registry_dir / f"{name}.toml"
        plan = {"action": "remove", "provider": name, "fragment": str(fragment), "broker": str(target), "apply": apply}
        if not apply:
            return plan
        require_root(operation="remove", euid=euid)
        if fragment.exists():
            withdrawn = self.registry_dir / f".{name}.withdrawn"
            os.replace(fragment, withdrawn)
            withdrawn.unlink(missing_ok=True)
        self._remove_tree(target)
        return plan
