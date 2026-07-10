from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


def _new_secret() -> str:
    return uuid4().hex + uuid4().hex


def _new_client_id() -> str:
    return f"chatgpt-client-{uuid4().hex[:12]}"


@dataclass
class TunnelConfig:
    type: str = "frp"
    public_url: str = ""
    frp_server: str = ""
    frp_subdomain: str = ""
    cloudflare_mode: str = "quick"
    cloudflare_token: str = ""

    def computed_public_url(self) -> str:
        if self.type == "frp" and self.frp_server and self.frp_subdomain:
            return f"https://{self.frp_subdomain}.{self.frp_server}"
        return self.public_url


@dataclass
class AuthConfig:
    type: str = "oauth"
    oauth_client_id: str = field(default_factory=_new_client_id)
    oauth_client_secret: str = field(default_factory=_new_secret)
    oauth_password: str = field(default_factory=_new_secret)
    oauth_token_secret: str = field(default_factory=_new_secret)
    bearer_token: str = field(default_factory=_new_secret)


@dataclass
class RuntimeConfig:
    local_port: int = 28766
    tool_profile: str = "full"
    permission_mode: str = "trusted"
    runtime_command: str = ""


@dataclass
class WorkspaceProfile:
    id: str
    name: str
    path: str
    tunnel: TunnelConfig = field(default_factory=TunnelConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @property
    def endpoint(self) -> str:
        return f"{self.effective_public_url.rstrip('/')}/mcp"

    @property
    def local_endpoint(self) -> str:
        return f"http://127.0.0.1:{self.runtime.local_port}/mcp"

    @property
    def effective_public_url(self) -> str:
        return self.tunnel.computed_public_url().rstrip("/")

    def frp_proxy_snippet(self) -> str:
        return "\n".join(
            [
                "[[proxies]]",
                f'name = "{self.name.lower().replace(" ", "-") or "workspace"}-mcp"',
                'type = "http"',
                'localIP = "host.docker.internal"',
                f"localPort = {self.runtime.local_port}",
                f'subdomain = "{self.tunnel.frp_subdomain}"',
            ]
        )

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "WorkspaceProfile":
        return cls(
            id=record["id"],
            name=record["name"],
            path=record["path"],
            tunnel=TunnelConfig(**record.get("tunnel", {})),
            auth=AuthConfig(**record.get("auth", {})),
            runtime=RuntimeConfig(**record.get("runtime", {})),
        )


@dataclass
class RuntimeStatus:
    state: str = "stopped"
    pid: int | None = None
    local_message: str = "未启动"
    public_message: str = "未知"


def build_profile(path: str, name: str | None = None) -> WorkspaceProfile:
    cleaned = path.rstrip("\\/")
    label = name or cleaned.replace("\\", "/").split("/")[-1]
    return WorkspaceProfile(id=uuid4().hex, name=label or "工作区", path=cleaned)
