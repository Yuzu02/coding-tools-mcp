# Known Limitations

- `exec_command` is policy-constrained and uses Linux Landlock filesystem confinement where available, but it is not a complete OS/container sandbox.
- Command classification uses string/path checks for non-filesystem risk classes and can miss behavior hidden inside interpreters, package scripts, static binaries, or generated files.
- Network denial is policy-based unless the operator runs the server in an external sandbox with egress controls.
- Non-Linux platforms or Linux kernels without Landlock are not production targets for `exec_command` without an external sandbox.
- This build uses real POSIX PTYs but does not implement Windows ConPTY;
  `tty=true` returns `TTY_UNSUPPORTED` on Windows.
- Windows string commands require PowerShell 7 (`pwsh`) and run with
  `-NoLogo -NoProfile -NonInteractive`. Operators may pin an absolute trusted
  executable with `CODING_TOOLS_MCP_PWSH_PATH`; otherwise the server searches
  absolute entries on its own process `PATH` while excluding the current
  directory tree. There is no `cmd.exe` fallback.
- Windows `safe` mode cannot statically decide what PowerShell dynamic syntax
  resolves to, so variables (`$`), splatting (`@`), the call and dot-source
  operators, .NET member access (`::`), and alias or expression evaluation
  cmdlets require the `shell_expansion` permission even when the command would
  turn out to be harmless. Nested shells (`pwsh -Command`, `pwsh
  -EncodedCommand`, `cmd /c`) require `inline_script`. Use
  `request_permissions` or `trusted` mode for commands that need them. Command
  scanning is not a sandbox: this build has no OS-level confinement on Windows,
  so `safe` mode there is a best-effort gate rather than a boundary.
- Portable filesystems do not provide a transaction across unrelated
  directories. `apply_patch` keeps same-directory backups and rolls back the
  full staged set, but a storage failure that also prevents rollback is surfaced
  as `PATCH_ROLLBACK_FAILED` and may require operator recovery.
- OAuth dynamic client registrations and pending authorization codes are held in
  process memory. Restarting the server requires dynamic clients to register
  again.
- Current SWE-bench scaffold is preflight-only by default; an explicit official Docker harness attempt is blocked in this environment when Docker or the harness is unavailable.
- Checked-in SWE-bench predictions are placeholders until replaced by real native baseline and MCP-candidate patches.
