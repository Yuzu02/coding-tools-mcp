# Dynamic Credential Provider Registry Design

**Date:** 2026-08-18
**Status:** Proposed
**Scope:** Replace static `security.exec_credentials` with dynamically reloaded, host-managed credential providers that are isolated per executed command.

## 1. Purpose

The current provider feature scopes selected environment variables to a simple
allowlisted executable, but its credential stores are visible to the whole MCP
service namespace. In `permission_mode = "dangerous"`, the existing general
Landlock policy is intentionally disabled, so an arbitrary `exec_command` can
open a mounted provider store.

This design makes credentials an explicit host-authorized capability with four
separate boundaries:

1. A root-managed registry authorizes provider metadata.
2. A dedicated broker contains the MCP-specific copies of persistent CLI
   stores, outside the service user's home directory.
3. A provider-only Landlock ruleset gives a selected child access to exactly
   its own store and the minimum runtime roots it needs.
4. Host tooling, never the MCP runtime, creates stores, changes ownership, and
   publishes or removes registry fragments.

The MCP parent remains a trusted boundary: someone who can alter its process,
registry directory, service account, or host filesystem controls the service.
This feature prevents an untrusted or mistaken `exec_command` child from
reading a credential store that was not selected for that command.

## 2. Non-goals

- Mounting paths, changing a systemd unit, or escalating privileges from the
  MCP process.
- Protecting credentials from the MCP parent, a root operator, or another
  process already able to impersonate the service account outside this child
  sandbox.
- Relaxing `dangerous` for arbitrary non-provider commands. Its existing
  filesystem behavior remains unchanged.
- Automatically migrating personal stores or running a deployment.
- Adding journal-group membership or broad host-log access.

## 3. Verified Constraints

- The existing general Landlock wrapper works on the target Linux kernel, but
  is disabled in `dangerous` to preserve that mode's broad command semantics.
- A focused Landlock ruleset can let one test provider open its own store while
  blocking a sibling store. A real CLI version command also runs under that
  style of ruleset.
- Bubblewrap is installed but cannot create a user namespace in the target
  service/host. It is not the selected backend.
- `ProtectHome=tmpfs` must remain effective; the finished service must not
  bind any personal-home path for credential access.

## 4. Architecture

```text
root operator
  ├── /etc/coding-tools-mcp/credentials.d/*.toml     registry fragments
  └── /var/lib/coding-tools-mcp/credentials/<name>/  isolated CLI stores
                         │
                         ▼
MCP Runtime ── lazy stat/fingerprint reload ── CredentialProviderRegistry
                         │
                         └── credential-isolation Landlock child (every exec_command)
                                   ├── non-provider: workspace + normal system/runtime roots
                                   └── provider: same roots + selected provider store
```

The service receives one generic writable broker directory through its stable
systemd state directory. Adding, removing, or replacing a provider changes
only a fragment and broker contents; it never changes the unit, a `BindPaths`
drop-in, or the running MCP process.

### 4.1 Stable locations

The host configuration directory containing `config.toml` owns the sibling
registry directory `credentials.d`. The broker is always
`<state-root>/credentials`; a standard unit uses
`/var/lib/coding-tools-mcp/credentials`.

The runtime derives both locations from its selected HostConfig and runtime
state root. It does not accept an environment override or a project-owned
configuration value for either location. This prevents a project from
authorizing a provider or redirecting a credential store.

The one-time system migration renames the current candidate state directory to
the stable `coding-tools-mcp` state directory, removes credential-home bind
paths, and exposes the generic state directory. Later provider changes require
none of those actions.

### 4.2 Provider fragment format

Each root-owned `*.toml` file contains exactly one provider table:

```toml
name = "example"
commands = ["example-cli"]
read_roots = ["/var/lib/coding-tools-mcp/credentials/example/read-only"]
write_roots = ["/var/lib/coding-tools-mcp/credentials/example/state"]
env_passthrough = ["EXAMPLE_CLI_TOKEN"]
env_paths = ["EXAMPLE_CLI_CONFIG_DIR=/var/lib/coding-tools-mcp/credentials/example/state"]
```

All declared roots must be canonical descendants of that provider's broker
directory. `read_roots` permit read-only access; `write_roots` permit read and
write access. A path may not name another provider's broker directory, the
broker parent, a workspace, a home directory, or a system root.

The parser reuses the existing command, duplicate-owner, environment-name, and
environment-path validation. It additionally rejects symlinks in roots and
requires that every declared root belongs to the provider broker subtree.
`env_paths` cannot hold a secret-like name and cannot override HOME, PATH,
PATHEXT, TMP/TEMP/TMPDIR, COMSPEC/SYSTEMROOT/WINDIR, or an XDG root.
`env_passthrough` cannot override those process/XDG roots either.

Fragment filenames are not identity. Provider `name` is the identity and must
be unique. Command basenames have exactly one owner across the complete
registry.

### 4.3 Reload, generations, and errors

`CredentialProviderRegistry` owns an immutable `CredentialRegistrySnapshot`.
Before every `exec_command` and `server_info`, it computes a cheap directory
generation from the sorted `*.toml` entries' filename, device/inode, size, and
nanosecond mtime. It parses and SHA-256 fingerprints file bytes only when that
generation changes.

Fragments are published by writing a same-directory temporary file, fsyncing
it, atomically replacing the target, and fsyncing the directory. The runtime
then observes one whole old or new registry, never a partially written file.

If a new generation is malformed, has duplicate provider/command ownership,
or violates broker containment, the registry enters an unhealthy snapshot:

- no previous provider snapshot is retained for authorization;
- all inherited and explicit secret-like environment values remain scrubbed;
- no provider roots or provider environment values are granted;
- commands with no credential selection continue under their normal policy;
- a simple command whose formerly matching provider is unavailable runs only
  without a credential grant, with an explicit non-secret warning;
- `server_info` reports `health = "invalid"`, generation/fingerprint, and a
  bounded redacted validation error.

Removing every fragment is a valid healthy empty generation. Concurrent
commands keep the immutable snapshot selected when they started; later calls
use the newly observed snapshot.

## 5. Command Selection and Environment

Provider activation preserves the existing conservative parser. It requires
one simple executable basename owned by exactly one provider. Inline
assignments, explicit executable paths, multiple commands, newlines, command
substitution, shell control operators, pipes, redirections, and heredocs never
activate a provider.

When a registry directory exists, `exec_command` always removes sensitive
environment names from its inherited and caller-supplied environment. Only a
healthy selected provider may add its own allowlisted `env_passthrough` values
and non-secret `env_paths`. HOME, TMPDIR, and the runtime's XDG isolation stay
under MCP control for every command.

This is independent of the global `permission_mode` filter. Provider
activation never turns an inline environment assignment into a bypass. The
credential filesystem profile is a documented security boundary, not an
accidental change hidden behind the `dangerous` label.

## 6. Credential-isolation Landlock Backend

The existing general Landlock ruleset remains coupled to safe/trusted command
policy. A new credential-isolation ruleset is selected for **every**
`exec_command` once this broker architecture is bootstrapped. It is deliberately
separate from the general permission-mode policy:

- a non-provider command gets no broker root; and
- a healthy selected provider gets only its declared roots.

Applying the profile to every child is necessary. The MCP service user can see
the generic state directory, so sandboxing only provider children would leave
a dangerous non-provider child able to open all broker stores. Landlock has no
deny-only rule that can hide one subtree while leaving all of `/` available.

The profile's read allowlist contains:

- the selected workspace and the minimal operating-system/toolchain roots
  required to start a shell and resolved executable;
- runtime-owned command HOME, temporary, and cache directories; and
- for a selected provider only, its declared read and write roots.

Its write allowlist contains the workspace's existing permission-mode writes,
the runtime-owned command directories, and only a selected provider's declared
`write_roots`. It deliberately does not reuse arbitrary extra roots from
`CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS`: such a broad root could encompass the
broker and defeat cross-provider isolation.

This is an intentional, visible filesystem narrowing for `dangerous` commands
while the credential broker exists. It preserves normal workspace commands,
system toolchains, network capability, shell expansion, and the normal runtime
directories; it no longer promises arbitrary host-path reads from a child.
`server_info` reports this as `credential_isolation.enforced_for = "all_exec"`.
It is the required trade-off for ensuring non-provider children cannot see the
broker on a host where mount namespaces are unavailable.

The runtime resolves the selected executable from its sanitized command
environment and includes only its canonical executable/runtime parents when
needed. A resolved path that would encompass the broker is rejected. Failure
to create, populate, or apply this credential ruleset fails closed: the command
does not execute, and a clear `CREDENTIAL_SANDBOX_UNAVAILABLE` security error
is returned. Selected provider commands see only their own roots; a Vercel
child cannot open Neon state and vice versa.

## 7. Runtime Metadata and Diagnostics

`server_info` keeps `exec_policy.secret_env_filter` for compatibility, but
adds a separate `credential_providers` object:

```json
{
  "sensitive_env_filter": "enabled-when-registry-present",
  "registry": {"health": "healthy", "generation": "…", "fingerprint": "…"},
  "filesystem_isolation": {"backend": "landlock", "status": "available", "enforced_for": "all_exec"},
  "providers": [{"name": "example", "commands": ["example-cli"], "read_roots": ["…"], "write_roots": ["…"], "env_paths": {"EXAMPLE_CLI_CONFIG_DIR": "…"}}]
}
```

No provider metadata includes a value from `env_passthrough`, a credential
file's contents, a secret fingerprint, or a raw exception containing one.

Runtime metadata also exposes the existing `server_instance_id`, process start
time, host-config fingerprint, dynamic registry generation, and a stable hash
of the exposed tool names. The launcher-owned run manifest remains the source
for tunnel health and launcher errors; a new read-only diagnostics helper
combines its bounded, already-redacted fields with local MCP readiness and
semantic worker status. It never reads the system journal. A root-only
`credentials doctor --system` may query systemd explicitly when an operator
requests it.

These identifiers permit correlating intermittent connector `Resource not
found` errors with a true process restart, configuration churn, tunnel target,
or a healthy unchanged runtime, without attributing a remote control-plane
issue to the local MCP prematurely.

## 8. Host Administrative Tooling

`scripts/credentials.py` is an explicit host operator tool, not an MCP tool.
It supports:

- `list`: prints fragment/provider names and paths only.
- `doctor`: validates ownership, modes, broker containment, registry health,
  available Landlock support, and optionally `--system` unit state.
- `provision`: validates a proposed fragment, creates the provider broker
  subtree, copies an operator-selected regular-file store through a private
  staging directory, applies service-account ownership/modes, then atomically
  publishes the fragment.
- `remove`: with an explicit provider name, prints a removal plan by default;
  `--apply` atomically unpublishes the fragment and removes only its validated
  broker subtree.

Every operation supports `--dry-run`; destructive work requires both `--apply`
and an explicit provider name. The tool rejects non-root calls for operations
that would need ownership, mode, or `/etc` changes. It never invokes sudo,
never prints file content, secret values, hashes of secret material, or a
complete environment. The MCP runtime has no import path that calls this
tooling.

## 9. One-time System Migration

The repository will document, but not execute, one root migration block. The
unit/drop-in rollback is deterministic; credential provisioning is deliberately
non-destructive on rollback and is not safe to rerun blindly because the block
has no reservation or locking mechanism for concurrent registry/broker state.
It will:

1. stop the unit;
2. create the stable state/broker and registry directories with root-managed
   registry ownership and service-account broker access;
3. use the administrative provision command to copy the deliberately selected
   stores into separate provider roots;
4. replace the static HostConfig `exec_credentials` declarations with the
   empty dynamic-registry bootstrap;
5. remove only the two credential-specific home BindPaths from the dedicated
   drop-in, preserving unrelated service hardening;
6. update the one-time state directory name if required, daemon-reload, and
   start the unit;
7. run non-secret doctor and local MCP probes; and
8. roll back the unit/drop-in change if startup or provider sandbox checks
   fail, leaving every credential fragment and broker subtree intact. Any
   cleanup of state left by a failed attempt requires a separate operator
   review with `doctor` followed by a dry-run and explicit `remove --apply`.

After that migration, provider changes use the administrative tool only. They
do not edit the unit, daemon-reload, or restart the MCP.

## 10. Testing and Acceptance

Tests are written RED then GREEN, one behavior at a time. Required coverage:

- valid fragments, duplicate command rejection, broker containment, and every
  inherited validation prohibition;
- atomic add/remove/replacement observed by one Runtime without re-creation;
- malformed replacement invalidates all grants rather than retaining stale
  providers, while ordinary commands still run safely;
- provider-only sensitive environment allowlisting and metadata redaction;
- rejection of inline assignment, explicit path, shell control, pipes,
  redirection, heredoc, and substitutions as provider activators;
- provider Landlock profiles: non-provider cannot open either store, A cannot
  open B, B cannot open A, and a provider can read/write its declared roots;
- sandbox failure is fail-closed; dangerous non-provider behavior remains
  unchanged;
- isolated HOME/XDG, distinct server-info filter fields, instance/generation
  metadata, and bounded invalid-registry errors;
- host tool dry-run, containment, ownership checks, and no-secret output.

Final acceptance requires focused tests, the complete suite, Ruff, mypy on
modified files with the unrelated existing OAuth redeclaration called out
separately, `git diff --check`, and public-fork hygiene. Live acceptance occurs
only after an operator applies the one-time block: service health, four project
smokes, HOME isolation, dynamic no-restart provider changes, non-provider and
cross-provider opening probes, and authenticated read-only Vercel/Neon calls
without secret output.
