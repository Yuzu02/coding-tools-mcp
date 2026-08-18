# Multiplatform Services Launcher Implementation Plan

**Status:** HISTORICAL EXECUTION PLAN — IMPLEMENTED + VERIFIED; deployment authority is now HostConfig v2.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-owned Python launcher that provisions the declared toolchain with `mise`, synchronizes the checkout with `uv`, and safely supervises `coding-tools-mcp` plus OpenAI `tunnel-client` on Windows, Linux, and macOS.

**Architecture:** Keep `scripts/start_services.py` as a thin CLI and split configuration, diagnostics, tunnel-profile handling, and process supervision into focused modules under `scripts/launcher/`. Use immutable configuration records, argv arrays with `shell=False`, official `tunnel-client init` profile generation, separate child environments, run-scoped artifacts, and standard-library tests with fake child processes.

**Tech Stack:** Python 3.11+ standard library, `argparse`, `dataclasses`, `pathlib`, `subprocess`, `socket`, `signal`, `urllib`, `unittest`, Ruff, `uv 0.12.1`, `mise`, OpenAI `tunnel-client 0.0.10`, Node 24.15.0, Rust 1.97.1.

## Global Constraints

- Base commit: `9893bc88ab0fbfc0b410e4835915abfe9f7387e7`; implementation branch: `feat/multiplatform-services-launcher`.
- Toolchain pins: Python `3.13.12`, `uv 0.12.1`, Node `24.15.0`, Rust `1.97.1`, `github:openai/tunnel-client 0.0.10`.
- The launcher runs the MCP checkout only through `uv run --project <repo> --locked python -m coding_tools_mcp`.
- Generated tunnel profiles are created through `tunnel-client init`; repository code does not serialize the tunnel YAML schema.
- Accept only `env:NAME` and `file:/path` control-plane key references; reject literal keys.
- Never expose control-plane credentials, private key paths, raw extra headers, `.env` values, or complete process environments in manifests or terminal output.
- Do not pass tunnel-only credential variables to the MCP child.
- Launch every child with an argv list and `shell=False`.
- Default MCP permission mode is `trusted`; default HTTP session mode is `ephemeral`.
- Configuration precedence is CLI > existing environment > selected `.env` > defaults.
- Diagnostics failures must never prevent process cleanup.
- Runtime artifacts live below `.runtime/services/` and remain ignored by Git.
- Push only to the user's `fork` remote; do not open an upstream PR.

---

### Task 1: Toolchain and repository contract

**Files:**
- Create: `mise.toml`
- Modify: `.gitignore`
- Create: `tests/test_launcher_toolchain.py`

**Interfaces:**
- Produces a root `mise.toml` with the exact tool pins and tasks `setup`, `setup-dev`, `start`, `check-npm`, `test-launcher`, and `verify`.
- Produces `.runtime/` ignore coverage.

- [ ] **Step 1: Write the failing toolchain contract tests**

```python
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherToolchainTests(unittest.TestCase):
    def test_mise_pins_required_tools_and_uv_environment(self) -> None:
        config = tomllib.loads((ROOT / "mise.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["tools"]["python"], "3.13.12")
        self.assertEqual(config["tools"]["uv"], "0.12.1")
        self.assertEqual(config["tools"]["node"], "24.15.0")
        self.assertEqual(config["tools"]["rust"], "1.97.1")
        self.assertEqual(config["tools"]["github:openai/tunnel-client"], "0.0.10")
        self.assertEqual(config["env"]["UV_PYTHON"], "3.13.12")

    def test_mise_tasks_use_uv_locked_execution(self) -> None:
        config = tomllib.loads((ROOT / "mise.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["tasks"]["setup"]["run"], "uv sync --locked")
        self.assertIn("uv run --locked python scripts/start_services.py", config["tasks"]["start"]["run"])
        self.assertIn("tests.test_launcher_config", config["tasks"]["test-launcher"]["run"])

    def test_runtime_directory_is_ignored(self) -> None:
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".runtime/", lines)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --locked python -m unittest tests.test_launcher_toolchain -v`

Expected: FAIL because `mise.toml` does not exist and `.runtime/` is not ignored.

- [ ] **Step 3: Add the toolchain file and ignore rule**

```toml
[tools]
python = "3.13.12"
uv = "0.12.1"
node = "24.15.0"
rust = "1.97.1"
"github:openai/tunnel-client" = "0.0.10"

[env]
PYTHONUTF8 = "1"
PYTHONDONTWRITEBYTECODE = "1"
UV_MANAGED_PYTHON = "1"
UV_PYTHON = "3.13.12"

[tasks.setup]
run = "uv sync --locked"

[tasks.setup-dev]
run = "uv sync --locked --extra dev"

[tasks.start]
run = "uv run --locked python scripts/start_services.py"

[tasks.check-npm]
run = "cd npm/coding-tools-mcp && npm test && npm pack --dry-run --json"

[tasks.test-launcher]
run = "uv run --locked python -m unittest tests.test_launcher_toolchain tests.test_launcher_config tests.test_launcher_diagnostics tests.test_launcher_tunnel tests.test_launcher_processes tests.test_launcher_integration -v"

[tasks.verify]
run = "uv run --locked python -m ruff check scripts tests && uv run --locked python -m unittest discover -s tests -p 'test_*.py' && cd npm/coding-tools-mcp && npm test && npm pack --dry-run --json"
```

- [ ] **Step 4: Run the contract tests**

Run: `uv run --locked python -m unittest tests.test_launcher_toolchain -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mise.toml .gitignore tests/test_launcher_toolchain.py
git commit -m "build: pin launcher toolchain with mise"
```

---

### Task 2: Configuration parsing and environment isolation

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/launcher/__init__.py`
- Create: `scripts/launcher/config.py`
- Create: `tests/test_launcher_config.py`

**Interfaces:**
- Produces `ConfigError(ValueError)`.
- Produces immutable `TunnelMode`, `TunnelSelection`, and `ServiceConfig` dataclasses.
- Produces `build_parser(repo_root: Path) -> argparse.ArgumentParser`.
- Produces `resolve_config(argv: list[str] | None, *, environ: Mapping[str, str] | None = None, repo_root: Path | None = None) -> ServiceConfig`.
- Produces `load_dotenv(path: Path) -> dict[str, str]` and `scrub_mcp_environment(environment: Mapping[str, str], api_key_ref: str | None) -> dict[str, str]`.
- `ServiceConfig` exposes `mcp_argv()`, `sync_argv()`, `redacted_summary()`, and resolved path/time/profile fields used by later tasks.

- [ ] **Step 1: Write failing precedence, validation, argv, and scrubbing tests**

```python
class LauncherConfigTests(unittest.TestCase):
    def test_cli_overrides_process_environment_and_dotenv(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = make_repository(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".env").write_text("CODING_TOOLS_SERVICES_PORT=7000\n", encoding="utf-8")
            config = resolve_config(
                ["--workspace", str(workspace), "--mcp-repository", str(repo), "--port", "9000", "--no-tunnel"],
                environ={"CODING_TOOLS_SERVICES_PORT": "8000"},
                repo_root=repo,
            )
            self.assertEqual(config.port, 9000)

    def test_invalid_dotenv_reports_line_without_value(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CONTROL_PLANE_API_KEY=secret\nnot valid\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, r"line 2") as raised:
                load_dotenv(path)
            self.assertNotIn("secret", str(raised.exception))

    def test_profile_modes_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ConfigError, "mutually exclusive"):
            resolve_fixture(["--tunnel-profile", "dev", "--tunnel-profile-file", "profile.yaml"])

    def test_literal_control_plane_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "env:NAME or file:/path"):
            resolve_fixture(["--tunnel-id", "tunnel_test", "--control-plane-api-key-ref", "sk-secret"])

    def test_mcp_argv_uses_uv_locked_and_ephemeral_mode(self) -> None:
        config = resolve_fixture(["--no-tunnel"])
        self.assertEqual(config.mcp_argv()[:7], ["uv", "run", "--project", str(config.mcp_repository), "--locked", "python", "-m"])
        self.assertIn("ephemeral", config.mcp_argv())

    def test_mcp_environment_removes_tunnel_credentials(self) -> None:
        scrubbed = scrub_mcp_environment(
            {"PATH": "bin", "CONTROL_PLANE_API_KEY": "secret", "OPENAI_API_KEY": "fallback", "CONTROL_PLANE_CLIENT_KEY": "key.pem"},
            "env:CONTROL_PLANE_API_KEY",
        )
        self.assertEqual(scrubbed, {"PATH": "bin"})
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --locked python -m unittest tests.test_launcher_config -v`

Expected: FAIL because `scripts.launcher.config` does not exist.

- [ ] **Step 3: Implement the resolved configuration model**

Implement the exact parser groups from the approved spec. Parse environment-backed defaults with helpers that name malformed variables without echoing values. Resolve workspace/repository/env/log paths after merging the selected `.env`. Validate ports, positive durations, repository markers, existing profile files, profile-mode conflicts, generated-profile requirements, remote health listeners, and existing persistent destinations.

Use this public shape:

```python
@dataclass(frozen=True)
class TunnelSelection:
    mode: Literal["disabled", "profile", "profile-file", "generated"]
    profile: str | None
    profile_dir: Path | None
    profile_file: Path | None
    tunnel_id: str | None
    api_key_ref: str | None
    write_profile: Path | None


@dataclass(frozen=True)
class ServiceConfig:
    repository_root: Path
    workspace: Path
    mcp_repository: Path
    host: str
    port: int
    permission_mode: str
    shell_env_inherit: str
    http_session_mode: str
    enable_view_image: bool
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
    process_environment: dict[str, str]

    def sync_argv(self) -> list[str]: ...
    def mcp_argv(self) -> list[str]: ...
    def redacted_summary(self) -> dict[str, object]: ...
```

- [ ] **Step 4: Run config tests**

Run: `uv run --locked python -m unittest tests.test_launcher_config -v`

Expected: PASS.

- [ ] **Step 5: Run Ruff and commit**

Run: `uv run --locked python -m ruff check scripts/launcher/config.py tests/test_launcher_config.py`

```bash
git add scripts/__init__.py scripts/launcher/__init__.py scripts/launcher/config.py tests/test_launcher_config.py
git commit -m "feat: resolve launcher configuration safely"
```

---

### Task 3: Run diagnostics and secret-safe artifacts

**Files:**
- Create: `scripts/launcher/diagnostics.py`
- Create: `tests/test_launcher_diagnostics.py`

**Interfaces:**
- Produces `RunArtifacts` with run directory, manifest path, four child logs, and tunnel diagnostic paths.
- Produces `allocate_run_artifacts(logs_root: Path, *, now: datetime | None = None) -> RunArtifacts`.
- Produces `atomic_write_json(path: Path, value: object) -> None`.
- Produces mutable `RunManifest` with `transition`, `record_process`, `record_ready`, `record_exit`, `record_failure`, and `finish` methods.
- Produces `bounded_log_tail(path: Path, *, max_bytes: int = 8192, max_lines: int = 40) -> str`.
- Produces `download_http_artifact(url: str, destination: Path, *, timeout: float = 10.0) -> None`.

- [ ] **Step 1: Write failing artifact, atomicity, redaction, and tail tests**

```python
class LauncherDiagnosticsTests(unittest.TestCase):
    def test_run_directories_are_unique(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)
            first = allocate_run_artifacts(root, now=now)
            second = allocate_run_artifacts(root, now=now)
            self.assertNotEqual(first.run_directory, second.run_directory)

    def test_manifest_never_serializes_secret_values(self) -> None:
        with TemporaryDirectory() as tmp:
            artifacts = allocate_run_artifacts(Path(tmp))
            manifest = RunManifest.start(artifacts, {"api_key_ref": "env:CONTROL_PLANE_API_KEY"})
            manifest.record_failure("doctor", "failed while using secret-value")
            text = artifacts.manifest.read_text(encoding="utf-8")
            self.assertNotIn("secret-value", text)
            self.assertIn("doctor", text)

    def test_bounded_log_tail_limits_lines_and_invalid_utf8(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.log"
            path.write_bytes(b"old\n" * 100 + b"new\xff\n")
            tail = bounded_log_tail(path, max_bytes=128, max_lines=3)
            self.assertLessEqual(len(tail.splitlines()), 3)
            self.assertIn("new", tail)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --locked python -m unittest tests.test_launcher_diagnostics -v`

Expected: FAIL because `scripts.launcher.diagnostics` does not exist.

- [ ] **Step 3: Implement diagnostics**

Use `os.replace` for atomic JSON writes, `json.dumps(..., sort_keys=True, indent=2)`, UTF-8 without BOM, and a temporary file in the destination directory. Redact error text by replacing known secret values supplied to `RunManifest.start`; omit all environment dictionaries and raw argv. `download_http_artifact` streams with `urllib.request.urlopen` into a temporary file before replacement.

- [ ] **Step 4: Run tests and Ruff**

Run: `uv run --locked python -m unittest tests.test_launcher_diagnostics -v`

Run: `uv run --locked python -m ruff check scripts/launcher/diagnostics.py tests/test_launcher_diagnostics.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/launcher/diagnostics.py tests/test_launcher_diagnostics.py
git commit -m "feat: capture launcher run diagnostics"
```

---

### Task 4: Tunnel profile resolution and diagnostics

**Files:**
- Create: `scripts/launcher/tunnel.py`
- Create: `tests/test_launcher_tunnel.py`

**Interfaces:**
- Consumes `ServiceConfig`, `TunnelSelection`, `RunArtifacts`, and child environments.
- Produces `TunnelRuntime` with `run_args`, `doctor_args`, `health_url_file`, `generated_directory`, and `cleanup()`.
- Produces `prepare_tunnel(config: ServiceConfig, artifacts: RunArtifacts, runner: CommandRunner = subprocess.run) -> TunnelRuntime | None`.
- Produces `run_tunnel_doctor(runtime: TunnelRuntime, *, environment: Mapping[str, str], runner: CommandRunner = subprocess.run) -> dict[str, object]`.
- Produces `wait_for_tunnel_ready(process: Popen[bytes], url_file: Path, *, timeout: float, poll_interval: float) -> str`.
- Produces `capture_tunnel_diagnostics(runtime: TunnelRuntime, artifacts: RunArtifacts, *, environment: Mapping[str, str], log_minutes: int) -> list[str]`.

- [ ] **Step 1: Write failing profile-command and readiness tests**

```python
class LauncherTunnelTests(unittest.TestCase):
    def test_profile_file_mode_passes_profile_file_to_doctor_and_run(self) -> None:
        config = resolve_fixture(["--tunnel-profile-file", "profile.yaml"])
        with TemporaryDirectory() as tmp:
            artifacts = allocate_run_artifacts(Path(tmp))
            runtime = prepare_tunnel(config, artifacts, runner=fake_successful_runner)
            self.assertIn("--profile-file", runtime.doctor_args)
            self.assertIn("--profile-file", runtime.run_args)

    def test_generated_mode_calls_tunnel_init_without_literal_secret(self) -> None:
        calls: list[list[str]] = []
        config = resolve_fixture(["--tunnel-id", "tunnel_example", "--control-plane-api-key-ref", "env:CONTROL_PLANE_API_KEY"])
        with TemporaryDirectory() as tmp:
            artifacts = allocate_run_artifacts(Path(tmp))
            prepare_tunnel(config, artifacts, runner=recording_runner(calls))
        init = calls[0]
        self.assertEqual(init[1], "init")
        self.assertIn("env:CONTROL_PLANE_API_KEY", init)
        self.assertNotIn("secret", " ".join(init))

    def test_tunnel_ready_uses_url_file_and_readyz(self) -> None:
        with local_ready_server() as base_url, TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "health.url"
            url_file.write_text(base_url, encoding="utf-8")
            self.assertEqual(wait_for_tunnel_ready(running_process(), url_file, timeout=1, poll_interval=0.01), base_url)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --locked python -m unittest tests.test_launcher_tunnel -v`

Expected: FAIL because `scripts.launcher.tunnel` does not exist.

- [ ] **Step 3: Implement tunnel handling**

For generated profiles call:

```python
[
    config.tunnel_client,
    "init",
    "--profile", generated_name,
    "--profile-dir", str(profile_directory),
    "--tunnel-id", tunnel_id,
    "--mcp-server-url", tunnel_mcp_url,
    "--control-plane-base-url", control_plane_base_url,
    "--control-plane-api-key-ref", api_key_ref,
    "--health-listen-addr", config.tunnel_health_listen_addr,
]
```

Append optional URL-path/open-UI/force flags only when configured. `run_args` always append `--health.url-file <run-private path>`. Readiness performs bounded GET `/readyz`; diagnostics attempt `/api/status`, `tunnel-client health --require-control-plane-poll --json`, and `/api/logs/export` independently.

- [ ] **Step 4: Run tests and Ruff**

Run: `uv run --locked python -m unittest tests.test_launcher_tunnel -v`

Run: `uv run --locked python -m ruff check scripts/launcher/tunnel.py tests/test_launcher_tunnel.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/launcher/tunnel.py tests/test_launcher_tunnel.py
git commit -m "feat: manage OpenAI tunnel profiles"
```

---

### Task 5: Cross-platform child supervision

**Files:**
- Create: `scripts/launcher/processes.py`
- Create: `tests/test_launcher_processes.py`

**Interfaces:**
- Produces `ManagedProcess(name, process, stdout_path, stderr_path, stdout_handle, stderr_handle)`.
- Produces `start_process(name: str, argv: Sequence[str], *, cwd: Path, environment: Mapping[str, str], stdout_path: Path, stderr_path: Path) -> ManagedProcess`.
- Produces `wait_for_tcp(process: ManagedProcess, host: str, port: int, *, timeout: float, poll_interval: float) -> None`.
- Produces `supervise(processes: Sequence[ManagedProcess], *, poll_interval: float, stop_requested: Callable[[], bool]) -> tuple[str, int] | None`.
- Produces `terminate_process_tree(process: ManagedProcess, *, timeout: float, force: bool = False) -> None` and `close_process(process: ManagedProcess) -> None`.
- Produces `normalized_child_exit(exit_code: int) -> int` returning `1` for an unexpected zero exit.

- [ ] **Step 1: Write failing startup/readiness/normalization tests**

```python
class LauncherProcessTests(unittest.TestCase):
    def test_normalizes_unexpected_clean_child_exit(self) -> None:
        self.assertEqual(normalized_child_exit(0), 1)
        self.assertEqual(normalized_child_exit(7), 7)

    def test_wait_for_tcp_stops_when_child_exits(self) -> None:
        child = fake_managed_process(exit_code=4)
        with self.assertRaisesRegex(ProcessError, "exited before readiness"):
            wait_for_tcp(child, "127.0.0.1", unused_port(), timeout=1, poll_interval=0.01)

    def test_start_process_uses_new_process_group(self) -> None:
        with patch("scripts.launcher.processes.subprocess.Popen") as popen:
            start_process("child", ["python", "-V"], cwd=Path.cwd(), environment={}, stdout_path=Path("out"), stderr_path=Path("err"))
            kwargs = popen.call_args.kwargs
            if os.name == "nt":
                self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                self.assertTrue(kwargs["start_new_session"])
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --locked python -m unittest tests.test_launcher_processes -v`

Expected: FAIL because `scripts.launcher.processes` does not exist.

- [ ] **Step 3: Implement supervision**

Open logs in binary write mode and pass handles directly to `Popen`. On POSIX terminate with `os.killpg(os.getpgid(pid), signal.SIGTERM)` then `SIGKILL`; on Windows first send `CTRL_BREAK_EVENT` when possible, then run `taskkill /PID <pid> /T /F` after timeout. Always check `poll()` before signaling and close both log handles once the process has exited.

- [ ] **Step 4: Run tests and Ruff**

Run: `uv run --locked python -m unittest tests.test_launcher_processes -v`

Run: `uv run --locked python -m ruff check scripts/launcher/processes.py tests/test_launcher_processes.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/launcher/processes.py tests/test_launcher_processes.py
git commit -m "feat: supervise launcher child processes"
```

---

### Task 6: Launcher orchestration and CLI

**Files:**
- Create: `scripts/start_services.py`
- Create: `scripts/launcher/app.py`
- Create: `tests/test_launcher_integration.py`

**Interfaces:**
- Produces `LauncherDependencies` for injected command runner, process starter, readiness functions, sleep, and clock.
- Produces `run_services(config: ServiceConfig, dependencies: LauncherDependencies | None = None) -> int`.
- Produces `main(argv: list[str] | None = None) -> int` in `scripts/start_services.py`.

- [ ] **Step 1: Write failing orchestration tests**

```python
class LauncherIntegrationTests(unittest.TestCase):
    def test_dry_run_does_not_sync_or_start_children(self) -> None:
        calls = FakeDependencies()
        code = run_services(resolve_fixture(["--dry-run", "--no-tunnel"]), calls.dependencies())
        self.assertEqual(code, 0)
        self.assertEqual(calls.commands, [])
        self.assertEqual(calls.started, [])

    def test_sync_only_runs_uv_sync_and_exits(self) -> None:
        calls = FakeDependencies()
        code = run_services(resolve_fixture(["--sync-only", "--no-tunnel"]), calls.dependencies())
        self.assertEqual(code, 0)
        self.assertEqual(calls.commands[0][:3], ["uv", "sync", "--project"])
        self.assertEqual(calls.started, [])

    def test_tunnel_exit_stops_mcp_and_returns_failure(self) -> None:
        calls = FakeDependencies(tunnel_exit_code=0)
        code = run_services(resolve_fixture(["--tunnel-profile", "dev", "--no-sync"]), calls.dependencies())
        self.assertEqual(code, 1)
        self.assertEqual(calls.terminated, ["tunnel", "mcp"])

    def test_no_tunnel_starts_only_mcp(self) -> None:
        calls = FakeDependencies(stop_after_ready=True)
        code = run_services(resolve_fixture(["--no-tunnel", "--no-sync"]), calls.dependencies())
        self.assertEqual(code, 0)
        self.assertEqual(calls.started, ["mcp"])
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --locked python -m unittest tests.test_launcher_integration -v`

Expected: FAIL because orchestration modules do not exist.

- [ ] **Step 3: Implement the orchestration state machine**

Sequence:

```text
allocate artifacts -> starting
optional uv sync -> synchronizing
tool/version/capability/profile validation -> validating
optional doctor-only exit
start MCP -> starting-mcp -> waiting-for-mcp
optional start tunnel -> starting-tunnel -> waiting-for-tunnel
supervise -> running
capture tunnel diagnostics -> capturing-diagnostics
terminate tunnel then MCP -> stopping
final manifest -> stopped or failed
```

Use one cleanup path in `finally`. Install SIGTERM/SIGINT handlers in the main thread; first signal sets a stop flag and second signal requests forced cleanup. Configuration errors return `2`; interrupts return `130`; unexpected child exits use `normalized_child_exit`.

The CLI file must insert the repository root into `sys.path` when invoked as `python scripts/start_services.py`, parse with `resolve_config`, print concise redacted status, and end with `raise SystemExit(main())`.

- [ ] **Step 4: Run launcher tests and Ruff**

Run: `uv run --locked python -m unittest tests.test_launcher_toolchain tests.test_launcher_config tests.test_launcher_diagnostics tests.test_launcher_tunnel tests.test_launcher_processes tests.test_launcher_integration -v`

Run: `uv run --locked python -m ruff check scripts tests/test_launcher_*.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/start_services.py scripts/launcher/app.py tests/test_launcher_integration.py
git commit -m "feat: orchestrate MCP and tunnel services"
```

---

### Task 7: Operator documentation and deployment contract

**Files:**
- Create: `docs/services-launcher.md`
- Modify: `docs/quickstart.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_launcher_toolchain.py`

**Interfaces:**
- Documents clone + `mise install`, direct `uv` execution, all tunnel profile modes, secret references, diagnostics, dangerous mode, and a non-root `systemd` service.
- Adds a visible canonical-launcher link from README and quickstart.

- [ ] **Step 1: Extend documentation contract tests**

```python
def test_launcher_documentation_contains_required_workflows(self) -> None:
    text = (ROOT / "docs" / "services-launcher.md").read_text(encoding="utf-8")
    for required in (
        "mise install",
        "uv run --locked python scripts/start_services.py",
        "--tunnel-profile-file",
        "--tunnel-id",
        "--no-sync",
        "CONTROL_PLANE_API_KEY",
        "Restart=on-failure",
        "EnvironmentFile=",
    ):
        self.assertIn(required, text)
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run --locked python -m unittest tests.test_launcher_toolchain -v`

Expected: FAIL because `docs/services-launcher.md` does not exist.

- [ ] **Step 3: Write operator documentation**

Include executable examples for:

```bash
mise install
mise run setup
mise run start -- --workspace /srv/repos --tunnel-profile coding-tools-dev
uv run --locked python scripts/start_services.py --workspace /srv/repos --tunnel-profile-file /etc/coding-tools-mcp/profile.yaml
uv run --locked python scripts/start_services.py --workspace /srv/repos --tunnel-id tunnel_... --control-plane-api-key-ref file:/etc/coding-tools-mcp/control-plane.key
uv run --locked python scripts/start_services.py --workspace /srv/repos --no-tunnel --no-sync
```

The `systemd` unit uses a fixed `WorkingDirectory`, non-root `User`, `EnvironmentFile=/etc/coding-tools-mcp/coding-tools-mcp.env`, `ExecStart=/usr/bin/env mise exec -- uv run --locked python scripts/start_services.py ...`, `Restart=on-failure`, and `KillMode=control-group`.

- [ ] **Step 4: Run docs contract and link checks**

Run: `uv run --locked python -m unittest tests.test_launcher_toolchain -v`

Run: `git grep -n "services-launcher.md" -- README.md docs/quickstart.md`

Expected: PASS and both files link the guide.

- [ ] **Step 5: Commit**

```bash
git add docs/services-launcher.md docs/quickstart.md README.md CHANGELOG.md tests/test_launcher_toolchain.py
git commit -m "docs: add services launcher operations guide"
```

---

### Task 8: Full verification, live local smoke, and fork publication

**Files:**
- Modify only files required to fix verification findings.

**Interfaces:**
- Produces a clean, verified, pushed branch containing the cumulative base features and launcher.

- [ ] **Step 1: Run launcher unit and integration tests**

Run:

```bash
uv run --locked python -m unittest \
  tests.test_launcher_toolchain \
  tests.test_launcher_config \
  tests.test_launcher_diagnostics \
  tests.test_launcher_tunnel \
  tests.test_launcher_processes \
  tests.test_launcher_integration -v
```

Expected: all launcher tests pass.

- [ ] **Step 2: Run inherited feature regression tests**

Run:

```bash
uv run --locked python -m unittest \
  tests.test_reliable_command_recovery_http \
  tests.test_http_session_ephemeral \
  tests.test_transport_http \
  tests.test_project_catalog \
  tests.test_skill_catalog \
  tests.test_project_skills_integration \
  tests.test_project_skills_runtime \
  tests.test_git_workdir_resolution -v
```

Expected: all inherited feature tests pass.

- [ ] **Step 3: Run full static and dynamic gates**

Run:

```bash
uv run --locked python -m ruff check scripts tests
uv run --locked python -m unittest discover -s tests -p "test_*.py"
mise tasks ls
mise run test-launcher
cd npm/coding-tools-mcp && npm test && npm pack --dry-run --json
git diff --check fork/fix/git-workdir-resolution...HEAD
```

Expected: every command exits `0`.

- [ ] **Step 4: Run safe local CLI smoke checks**

Run:

```bash
uv run --locked python scripts/start_services.py --help
uv run --locked python scripts/start_services.py --workspace . --no-tunnel --dry-run
uv run --locked python scripts/start_services.py --workspace . --tunnel-id tunnel_example --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY --dry-run
tunnel-client --version
uv --version
```

Expected: help and both redacted plans succeed without starting services or requiring credentials; tool versions match the declared contract when invoked through `mise exec`.

- [ ] **Step 5: Review and push to fork**

Run:

```bash
git status --short --branch
git log --oneline --decorate fork/fix/git-workdir-resolution..HEAD
git push -u fork feat/multiplatform-services-launcher
git ls-remote --heads fork feat/multiplatform-services-launcher
```

Expected: clean tree, all implementation commits visible, push succeeds only to `fork`, and the remote branch SHA matches local `HEAD`.
