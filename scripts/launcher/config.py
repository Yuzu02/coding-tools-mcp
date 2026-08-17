"""Configuration resolution for the multiplatform services launcher."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ENV_PREFIX = "CODING_TOOLS_SERVICES_"
DOTENV_ENTRY_RE = re.compile(
    r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)
ENV_REFERENCE_RE = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")
TUNNEL_MODES = ("disabled", "profile", "profile-file", "generated")
TUNNEL_SELECTION_FLAGS = {
    "--no-tunnel",
    "--tunnel-profile",
    "--tunnel-profile-file",
    "--tunnel-id",
    "--write-tunnel-profile",
}
TUNNEL_ONLY_ENV_PREFIXES = ("CONTROL_PLANE_", "TUNNEL_CLIENT_")


class ConfigError(ValueError):
    """Raised when launcher configuration is invalid."""


class LauncherArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ConfigError(message)


@dataclass(frozen=True)
class TunnelSelection:
    mode: Literal["disabled", "profile", "profile-file", "generated"]
    profile: str | None = None
    profile_dir: Path | None = None
    profile_file: Path | None = None
    tunnel_id: str | None = None
    api_key_ref: str | None = None
    control_plane_base_url: str = "https://api.openai.com"
    control_plane_url_path: str | None = None
    mcp_server_url: str | None = None
    generated_profile_name: str | None = None
    write_profile: Path | None = None
    force_profile_write: bool = False
    open_web_ui: bool = False


@dataclass(frozen=True)
class ServiceConfig:
    repository_root: Path
    workspace: Path
    mcp_repository: Path
    host: str
    port: int
    permission_mode: str
    shell_env_inherit: str
    enable_view_image: bool
    mcp_args: tuple[str, ...]
    uv: str
    tunnel_client: str
    tunnel: TunnelSelection
    sync: bool
    sync_extras: tuple[str, ...]
    sync_only: bool
    doctor_only: bool
    dry_run: bool
    startup_timeout: float
    shutdown_timeout: float
    poll_interval: float
    logs_root: Path
    tunnel_health_listen_addr: str
    tunnel_health_url_file: Path | None
    tunnel_log_minutes: int
    keep_generated_profile: bool
    allow_remote_tunnel_ui: bool
    env_file: Path | None
    env_file_loaded: bool
    process_environment: dict[str, str]

    def sync_argv(self) -> list[str]:
        argv = [
            self.uv,
            "sync",
            "--project",
            str(self.mcp_repository),
            "--locked",
        ]
        for extra in self.sync_extras:
            argv.extend(("--extra", extra))
        return argv

    def mcp_argv(self) -> list[str]:
        argv = [
            self.uv,
            "run",
            "--project",
            str(self.mcp_repository),
            "--locked",
            "python",
            "-m",
            "coding_tools_mcp",
            "--workspace",
            str(self.workspace),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--permission-mode",
            self.permission_mode,
            "--shell-env-inherit",
            self.shell_env_inherit,
        ]
        if self.enable_view_image:
            argv.append("--enable-view-image")
        argv.extend(self.mcp_args)
        return argv

    def redacted_summary(self) -> dict[str, object]:
        tunnel: dict[str, object] = {"mode": self.tunnel.mode}
        if self.tunnel.mode == "profile":
            tunnel["profile"] = self.tunnel.profile
            tunnel["profile_dir"] = (
                str(self.tunnel.profile_dir) if self.tunnel.profile_dir else None
            )
        elif self.tunnel.mode == "profile-file":
            tunnel["profile_file"] = str(self.tunnel.profile_file)
        elif self.tunnel.mode == "generated":
            identifier = self.tunnel.tunnel_id or ""
            tunnel["tunnel_id_hint"] = (
                f"{identifier[:10]}…" if len(identifier) > 10 else identifier
            )
            tunnel["api_key_source"] = (
                self.tunnel.api_key_ref.split(":", 1)[0]
                if self.tunnel.api_key_ref
                else None
            )
            tunnel["persistent_profile"] = (
                str(self.tunnel.write_profile) if self.tunnel.write_profile else None
            )
        return {
            "workspace": str(self.workspace),
            "mcp_repository": str(self.mcp_repository),
            "endpoint": f"http://{self.host}:{self.port}/mcp",
            "permission_mode": self.permission_mode,
            "shell_env_inherit": self.shell_env_inherit,
            "sync": self.sync,
            "sync_extras": list(self.sync_extras),
            "logs_root": str(self.logs_root),
            "tunnel": tunnel,
        }


def _env_name(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


def _env_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    value = environment.get(_env_name(name))
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{_env_name(name)} must be a boolean value")


def _env_int(environment: Mapping[str, str], name: str, default: int) -> int:
    value = environment.get(_env_name(name))
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{_env_name(name)} must be an integer") from exc


def _env_float(environment: Mapping[str, str], name: str, default: float) -> float:
    value = environment.get(_env_name(name))
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{_env_name(name)} must be a number") from exc


def _split_env_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _bool_option(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    default: bool,
    help_text: str,
) -> None:
    parser.add_argument(
        f"--{name}",
        action=argparse.BooleanOptionalAction,
        default=default,
        help=help_text,
    )


def build_parser(
    repo_root: Path,
    environment: Mapping[str, str] | None = None,
) -> argparse.ArgumentParser:
    env = environment or {}
    parser = LauncherArgumentParser(
        description="Start and supervise coding-tools-mcp and OpenAI tunnel-client.",
    )
    parser.add_argument(
        "--workspace",
        default=(
            env.get(_env_name("WORKSPACE"))
            or env.get("CODING_TOOLS_MCP_WORKSPACE")
        ),
    )
    parser.add_argument(
        "--mcp-repository",
        default=env.get(_env_name("MCP_REPOSITORY")) or str(repo_root),
    )
    parser.add_argument("--host", default=env.get(_env_name("HOST"), "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_int(env, "PORT", 8000))
    parser.add_argument(
        "--permission-mode",
        choices=("safe", "trusted", "dangerous"),
        default=env.get(_env_name("PERMISSION_MODE"), "trusted"),
    )
    parser.add_argument(
        "--shell-env-inherit",
        default=env.get(_env_name("SHELL_ENV_INHERIT"), "all"),
    )
    _bool_option(
        parser,
        "enable-view-image",
        default=_env_bool(env, "ENABLE_VIEW_IMAGE", True),
        help_text="Enable the optional view_image MCP tool.",
    )
    parser.add_argument(
        "--mcp-arg",
        action="append",
        default=list(_split_env_list(env.get(_env_name("MCP_ARGS")))),
    )

    _bool_option(
        parser,
        "sync",
        default=_env_bool(env, "SYNC", True),
        help_text="Run uv sync --locked before startup.",
    )
    parser.add_argument(
        "--sync-extra",
        action="append",
        default=list(_split_env_list(env.get(_env_name("SYNC_EXTRAS")))),
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        default=_env_bool(env, "SYNC_ONLY", False),
    )
    parser.add_argument("--uv", default=env.get(_env_name("UV"), "uv"))

    parser.add_argument(
        "--no-tunnel",
        action="store_true",
        default=_env_bool(env, "NO_TUNNEL", False),
    )
    parser.add_argument(
        "--tunnel-client",
        default=env.get(_env_name("TUNNEL_CLIENT"), "tunnel-client"),
    )
    parser.add_argument(
        "--tunnel-profile",
        default=env.get(_env_name("TUNNEL_PROFILE")),
    )
    parser.add_argument(
        "--tunnel-profile-dir",
        default=env.get(_env_name("TUNNEL_PROFILE_DIR")),
    )
    parser.add_argument(
        "--tunnel-profile-file",
        default=env.get(_env_name("TUNNEL_PROFILE_FILE")),
    )
    parser.add_argument("--tunnel-id", default=env.get(_env_name("TUNNEL_ID")))
    parser.add_argument(
        "--control-plane-base-url",
        default=env.get(
            _env_name("CONTROL_PLANE_BASE_URL"),
            "https://api.openai.com",
        ),
    )
    parser.add_argument(
        "--control-plane-url-path",
        default=env.get(_env_name("CONTROL_PLANE_URL_PATH")),
    )
    parser.add_argument(
        "--control-plane-api-key-ref",
        default=env.get(
            _env_name("CONTROL_PLANE_API_KEY_REF"),
            "env:CONTROL_PLANE_API_KEY",
        ),
    )
    parser.add_argument(
        "--tunnel-mcp-server-url",
        default=env.get(_env_name("TUNNEL_MCP_SERVER_URL")),
    )
    parser.add_argument(
        "--generated-profile-name",
        default=env.get(_env_name("GENERATED_PROFILE_NAME")),
    )
    parser.add_argument(
        "--write-tunnel-profile",
        default=env.get(_env_name("WRITE_TUNNEL_PROFILE")),
    )
    parser.add_argument(
        "--force-profile-write",
        action="store_true",
        default=_env_bool(env, "FORCE_PROFILE_WRITE", False),
    )
    parser.add_argument(
        "--open-tunnel-web-ui",
        action="store_true",
        default=_env_bool(env, "OPEN_TUNNEL_WEB_UI", False),
    )

    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=_env_float(env, "STARTUP_TIMEOUT", 60.0),
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        default=_env_float(env, "SHUTDOWN_TIMEOUT", 10.0),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=_env_float(env, "POLL_INTERVAL", 0.25),
    )
    parser.add_argument(
        "--logs-root",
        default=env.get(
            _env_name("LOGS_ROOT"),
            str(repo_root / ".runtime" / "services"),
        ),
    )
    parser.add_argument(
        "--tunnel-health-listen-addr",
        default=env.get(_env_name("TUNNEL_HEALTH_LISTEN_ADDR"), "127.0.0.1:8080"),
    )
    parser.add_argument(
        "--tunnel-health-url-file",
        default=env.get(_env_name("TUNNEL_HEALTH_URL_FILE")),
    )
    parser.add_argument(
        "--tunnel-log-minutes",
        type=int,
        default=_env_int(env, "TUNNEL_LOG_MINUTES", 120),
    )
    parser.add_argument(
        "--keep-generated-profile",
        action="store_true",
        default=_env_bool(env, "KEEP_GENERATED_PROFILE", False),
    )
    parser.add_argument(
        "--allow-remote-tunnel-ui",
        action="store_true",
        default=_env_bool(env, "ALLOW_REMOTE_TUNNEL_UI", False),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=_env_bool(env, "DRY_RUN", False),
    )
    parser.add_argument(
        "--doctor-only",
        action="store_true",
        default=_env_bool(env, "DOCTOR_ONLY", False),
    )
    parser.add_argument("--env-file", default=env.get(_env_name("ENV_FILE")))
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        default=_env_bool(env, "NO_ENV_FILE", False),
    )
    return parser


def load_dotenv(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise ConfigError(f".env file is not valid UTF-8: {path}") from exc
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = DOTENV_ENTRY_RE.fullmatch(stripped)
        if match is None:
            raise ConfigError(f"invalid .env entry at line {line_number}")
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group("name")] = value
    return values


def scrub_mcp_environment(
    environment: Mapping[str, str],
    api_key_ref: str | None,
) -> dict[str, str]:
    scrubbed: dict[str, str] = {}
    referenced_variable = (
        api_key_ref.split(":", 1)[1]
        if api_key_ref and api_key_ref.startswith("env:")
        else None
    )
    for name, value in environment.items():
        if name == "OPENAI_API_KEY" or name == referenced_variable:
            continue
        if name.startswith(TUNNEL_ONLY_ENV_PREFIXES):
            continue
        scrubbed[name] = value
    return scrubbed


def _preparse_environment_inputs(
    argv: Sequence[str],
    environment: Mapping[str, str],
) -> tuple[Path | None, Path | None, bool]:
    parser = LauncherArgumentParser(add_help=False)
    parser.add_argument("--workspace")
    parser.add_argument("--env-file")
    parser.add_argument("--no-env-file", action="store_true")
    args, _ = parser.parse_known_args(argv)
    workspace_raw = (
        args.workspace
        or environment.get(_env_name("WORKSPACE"))
        or environment.get("CODING_TOOLS_MCP_WORKSPACE")
    )
    workspace = _optional_path(workspace_raw)
    no_env_file = args.no_env_file or _env_bool(environment, "NO_ENV_FILE", False)
    explicit_env_file = args.env_file or environment.get(_env_name("ENV_FILE"))
    env_file = _optional_path(explicit_env_file)
    if env_file is None and workspace is not None and not no_env_file:
        env_file = workspace / ".env"
    return workspace, env_file, no_env_file


def _selection_flag_present(argv: Sequence[str]) -> bool:
    return any(
        token in TUNNEL_SELECTION_FLAGS
        or any(token.startswith(f"{flag}=") for flag in TUNNEL_SELECTION_FLAGS)
        for token in argv
    )


def _validate_secret_reference(reference: str) -> None:
    if ENV_REFERENCE_RE.fullmatch(reference):
        return
    if reference.startswith("file:") and len(reference) > len("file:"):
        return
    raise ConfigError(
        "--control-plane-api-key-ref must use env:NAME or file:/path; literal keys are rejected"
    )


def _is_loopback_listener(address: str) -> bool:
    if ":" not in address:
        return False
    host, _port = address.rsplit(":", 1)
    normalized = host.strip().strip("[]").lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _resolve_tunnel_selection(
    args: argparse.Namespace,
    argv: Sequence[str],
) -> TunnelSelection:
    cli_selects_mode = _selection_flag_present(argv)
    no_tunnel = args.no_tunnel
    profile = args.tunnel_profile
    profile_file = args.tunnel_profile_file
    tunnel_id = args.tunnel_id
    write_profile = args.write_tunnel_profile

    if cli_selects_mode:
        tokens = set(argv)
        no_tunnel = "--no-tunnel" in tokens
        profile = args.tunnel_profile if "--tunnel-profile" in tokens else None
        profile_file = (
            args.tunnel_profile_file if "--tunnel-profile-file" in tokens else None
        )
        generated_selected = any(
            flag in tokens for flag in ("--tunnel-id", "--write-tunnel-profile")
        )
        if not generated_selected:
            tunnel_id = None
            write_profile = None

    selected = [
        no_tunnel,
        bool(profile),
        bool(profile_file),
        bool(tunnel_id or write_profile),
    ]
    if sum(bool(value) for value in selected) > 1:
        raise ConfigError("tunnel selection modes are mutually exclusive")

    profile_dir = _optional_path(args.tunnel_profile_dir)
    if no_tunnel:
        return TunnelSelection(mode="disabled")
    if profile_file:
        path = _optional_path(profile_file)
        if path is None or not path.is_file():
            raise ConfigError(f"tunnel profile file does not exist: {path}")
        return TunnelSelection(mode="profile-file", profile_file=path)
    if tunnel_id or write_profile:
        if not tunnel_id:
            raise ConfigError("generated tunnel profile requires --tunnel-id")
        _validate_secret_reference(args.control_plane_api_key_ref)
        destination = _optional_path(write_profile)
        if destination and destination.exists() and not args.force_profile_write:
            raise ConfigError(
                f"generated tunnel profile already exists: {destination}; use --force-profile-write to replace it"
            )
        return TunnelSelection(
            mode="generated",
            tunnel_id=tunnel_id,
            api_key_ref=args.control_plane_api_key_ref,
            control_plane_base_url=args.control_plane_base_url,
            control_plane_url_path=args.control_plane_url_path,
            mcp_server_url=args.tunnel_mcp_server_url,
            generated_profile_name=args.generated_profile_name,
            write_profile=destination,
            force_profile_write=args.force_profile_write,
            open_web_ui=args.open_tunnel_web_ui,
        )
    return TunnelSelection(
        mode="profile",
        profile=profile or "coding-tools-dev",
        profile_dir=profile_dir,
    )


def resolve_config(
    argv: list[str] | None,
    *,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> ServiceConfig:
    tokens = list(argv or [])
    existing_environment = dict(os.environ if environ is None else environ)
    root = (repo_root or Path(__file__).resolve().parents[2]).expanduser().resolve()
    _workspace_hint, env_file_hint, no_env_file = _preparse_environment_inputs(
        tokens,
        existing_environment,
    )
    dotenv_values: dict[str, str] = {}
    env_file_loaded = False
    if not no_env_file and env_file_hint is not None:
        if env_file_hint.exists():
            if not env_file_hint.is_file():
                raise ConfigError(f".env path is not a file: {env_file_hint}")
            dotenv_values = load_dotenv(env_file_hint)
            env_file_loaded = True
        elif "--env-file" in tokens or existing_environment.get(_env_name("ENV_FILE")):
            raise ConfigError(f".env file does not exist: {env_file_hint}")

    merged_environment = dict(dotenv_values)
    merged_environment.update(existing_environment)
    parser = build_parser(root, merged_environment)
    args = parser.parse_args(tokens)

    if not args.workspace:
        raise ConfigError(
            "--workspace is required unless CODING_TOOLS_SERVICES_WORKSPACE or CODING_TOOLS_MCP_WORKSPACE is set"
        )
    workspace = Path(args.workspace).expanduser().resolve()
    repository = Path(args.mcp_repository).expanduser().resolve()
    if not workspace.is_dir():
        raise ConfigError(f"workspace directory does not exist: {workspace}")
    if not repository.is_dir():
        raise ConfigError(f"MCP repository directory does not exist: {repository}")
    for marker in ("pyproject.toml", "uv.lock"):
        if not (repository / marker).is_file():
            raise ConfigError(f"MCP repository is missing {marker}: {repository}")
    if not 1 <= args.port <= 65535:
        raise ConfigError("--port must be between 1 and 65535")
    if args.startup_timeout <= 0 or args.shutdown_timeout <= 0 or args.poll_interval <= 0:
        raise ConfigError("startup, shutdown, and poll durations must be positive")
    if not 1 <= args.tunnel_log_minutes <= 1440:
        raise ConfigError("--tunnel-log-minutes must be between 1 and 1440")
    if (
        not args.allow_remote_tunnel_ui
        and not _is_loopback_listener(args.tunnel_health_listen_addr)
    ):
        raise ConfigError(
            "remote tunnel admin listeners require --allow-remote-tunnel-ui"
        )

    tunnel = _resolve_tunnel_selection(args, tokens)
    if args.doctor_only and tunnel.mode == "disabled":
        raise ConfigError("--doctor-only requires tunnel mode")
    process_environment = dict(merged_environment)
    logs_root = Path(args.logs_root).expanduser().resolve()
    env_file = None if args.no_env_file else _optional_path(args.env_file) or env_file_hint

    return ServiceConfig(
        repository_root=root,
        workspace=workspace,
        mcp_repository=repository,
        host=args.host,
        port=args.port,
        permission_mode=args.permission_mode,
        shell_env_inherit=args.shell_env_inherit,
        enable_view_image=args.enable_view_image,
        mcp_args=tuple(args.mcp_arg),
        uv=args.uv,
        tunnel_client=args.tunnel_client,
        tunnel=tunnel,
        sync=args.sync,
        sync_extras=tuple(args.sync_extra),
        sync_only=args.sync_only,
        doctor_only=args.doctor_only,
        dry_run=args.dry_run,
        startup_timeout=args.startup_timeout,
        shutdown_timeout=args.shutdown_timeout,
        poll_interval=args.poll_interval,
        logs_root=logs_root,
        tunnel_health_listen_addr=args.tunnel_health_listen_addr,
        tunnel_health_url_file=_optional_path(args.tunnel_health_url_file),
        tunnel_log_minutes=args.tunnel_log_minutes,
        keep_generated_profile=args.keep_generated_profile,
        allow_remote_tunnel_ui=args.allow_remote_tunnel_ui,
        env_file=env_file,
        env_file_loaded=env_file_loaded,
        process_environment=process_environment,
    )
