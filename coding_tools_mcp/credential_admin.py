"""Root-only administration for the host credential broker.

This module is intentionally not imported by the MCP runtime.
"""
from __future__ import annotations

import os
import grp
import pwd
import shutil
import stat
import subprocess
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
        self.service_uid = service_uid
        self.service_gid = service_gid

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
        self._validate_fragment(text, request.name)
        if not apply:
            return report
        require_root(operation="provision", euid=euid)
        if self.service_uid is None or self.service_gid is None:
            raise CredentialAdminError("provision requires explicit service UID and GID")
        self._validate_service_account()
        # Validate against the established parser before touching the broker.
        self._validate_fragment(text, request.name)
        self.broker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.registry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.broker_dir, 0o710)
        os.chmod(self.registry_dir, 0o750)
        if os.geteuid() == 0:
            os.chown(self.broker_dir, 0, self.service_gid)
            os.chown(self.registry_dir, 0, self.service_gid)
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
        self._secure_fragment(fragment)
        return report

    def _secure_fragment(self, fragment: Path) -> None:
        os.chmod(fragment, 0o640)
        if os.geteuid() == 0:
            os.chown(fragment, 0, self.service_gid)

    def _validate_fragment(self, text: str, name: str) -> None:
        with tempfile.TemporaryDirectory(prefix=".validate-", dir=self.registry_dir.parent) as validation_root:
            validation_dir = Path(validation_root) / "registry"
            validation_dir.mkdir()
            if self.registry_dir.is_dir():
                for existing in self.registry_dir.glob("*.toml"):
                    if existing.name != f"{name}.toml":
                        shutil.copyfile(existing, validation_dir / existing.name)
            temporary_fragment = validation_dir / f"{name}.toml"
            atomic_write_fragment(temporary_fragment, text)
            snapshot = CredentialProviderRegistry(validation_dir, self.broker_dir).snapshot()
            if snapshot.health != "healthy":
                raise CredentialAdminError("proposed credential provider fragment is invalid")

    def _validate_service_account(self) -> None:
        assert self.service_uid is not None and self.service_gid is not None
        if self.service_uid <= 0 or self.service_gid <= 0:
            raise CredentialAdminError("service UID and GID must identify a non-root account")
        try:
            pwd.getpwuid(self.service_uid)
            grp.getgrgid(self.service_gid)
        except KeyError as exc:
            raise CredentialAdminError("service UID and GID must identify an existing account") from exc

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

    def doctor(self, *, system: bool = False, euid: int | None = None, systemctl_runner: Any = None) -> dict[str, Any]:
        if system:
            require_root(operation="doctor --system", euid=euid)
        report = self.list()
        report["registry_dir"] = str(self.registry_dir)
        report["broker_dir"] = str(self.broker_dir)
        report["system_requested"] = system
        report["checks"] = {"registry": self._audit_registry(), "broker": self._audit_broker()}
        report["ok"] = report["health"] == "healthy" and all(item["safe"] for item in report["checks"].values())
        if system:
            runner = systemctl_runner or subprocess.run
            try:
                result = runner(["systemctl", "show", "--no-pager", "--property=LoadState,ActiveState,SubState", "coding-tools-mcp.service"], capture_output=True, text=True, check=False, timeout=5)
                report["systemctl"] = {"returncode": int(result.returncode), "status": result.stdout.strip()[:512]}
            except subprocess.TimeoutExpired:
                report["systemctl"] = {"returncode": 124, "status": "timeout"}
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

    def _audit_registry(self) -> dict[str, Any]:
        result = self._audit_tree(self.registry_dir, expected_uid=0, expected_gid=self.service_gid, root_mode=0o750, file_mode=0o640)
        return result

    def _audit_broker(self) -> dict[str, Any]:
        result = self._audit_tree(self.broker_dir, expected_uid=0, expected_gid=self.service_gid, root_mode=0o710, file_mode=0o600)
        if result["exists"] and self.service_uid is not None:
            for node in self.broker_dir.rglob("*"):
                try:
                    info = node.lstat()
                except OSError:
                    continue
                expected_mode = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
                if info.st_uid != self.service_uid or info.st_gid != self.service_gid or info.st_mode & 0o777 != expected_mode:
                    result["unsafe"].append(str(node))
            result["safe"] = not result["unsafe"]
        return result

    @staticmethod
    def _audit_tree(path: Path, *, expected_uid: int | None, expected_gid: int | None, root_mode: int, file_mode: int) -> dict[str, Any]:
        result: dict[str, Any] = {"exists": path.exists(), "safe": True, "items": 0, "unsafe": []}
        if not path.exists() or path.is_symlink():
            result["safe"] = False
            return result
        nodes = [path, *path.rglob("*")]
        for node in nodes:
            result["items"] += 1
            try:
                info = node.lstat()
            except OSError:
                result["safe"] = False
                continue
            expected_mode = root_mode if node == path else (0o700 if stat.S_ISDIR(info.st_mode) else file_mode)
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                result["unsafe"].append(str(node))
                continue
            if info.st_uid != expected_uid or info.st_gid != expected_gid or info.st_mode & 0o777 != expected_mode:
                result["unsafe"].append(str(node))
        result["safe"] = not result["unsafe"]
        return result

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
