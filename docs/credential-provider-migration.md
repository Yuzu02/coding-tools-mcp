# Credential-provider migration

This page documents the one-time move from legacy credential bind entries to
the dynamic provider registry. It is operator documentation only. Live
migration, deployment, and rollback are outside this task; the block below is
labelled `NEVER RUN` and is not invoked by MCP.

## Runtime model

For a HostConfig deployment, the registry is `credentials.d` beside the
selected HostConfig. The broker is `<state-root>/credentials`, with one
subdirectory per provider. HostConfig is not a source of provider definitions.
Do not retain a personal-home bind as a second authority: copy the required
credential files into the provider broker using the administrative CLI, then
remove only the obsolete credential bind entry from the unit drop-in.

A fragment has one table with these fields: `name` (safe provider name),
`commands` (one or more executable basenames), optional `read_roots`, optional
`write_roots`, optional `env_passthrough` (environment variable names only),
and optional `env_paths` (`NAME=/absolute/path` strings). All roots must stay
inside `<state-root>/credentials/<name>`. Secret values, token files in the
repository, and secret-like `env_paths` names are prohibited. The service
environment supplies values for `env_passthrough` only when a matching simple
command runs.

The host-only `credentials` CLI takes `--registry-dir <dir>` and
`--broker-dir <dir>` before its subcommand. Its four commands are:

- `list`: path/provider metadata and registry health; dry-run and read-only by
  default.
- `doctor`: registry and broker ownership/mode checks; pass the same
  `--service-uid <uid> --service-gid <gid>` identity used by the systemd
  service so broker ownership is checked against the real service account.
  Add `--system` only for an explicit root operator: this is a separate root
  gate and also queries systemd status. A completed check exits zero only when
  all audited trees are safe; any unsafe ownership, mode, or registry result
  exits nonzero (while retaining redacted JSON diagnostics).
- `provision`: validates a source directory, stages a provider broker copy,
  and publishes its fragment; it is a plan by default and mutates only with
  `--apply`. An apply requires root plus explicit
  `--service-uid <uid> --service-gid <gid>`; those values select the account
  that owns the staged broker tree and must identify the service account, not
  the invoking operator.
- `remove <name>`: prints a removal plan by default and removes the named
  fragment and broker subtree only with `--apply`.

The registry reloads lazily before `exec_command` and `server_info`. A changed
fragment generation is picked up without an MCP restart. Invalid or
unavailable registry generations fail closed: no provider roots or provider
environment values are granted. With a registry configured, Landlock is
applied to every command. Non-provider commands get no broker root, while a
selected provider gets only its own roots. This deliberately narrows even
`dangerous` commands; it is the trade-off required to prevent a non-provider
command from opening every broker store. If the credential-isolation profile
cannot be created or verified, the command is not started.

## Verification after an operator applies the change

The local checks should show a healthy registry, the expected provider names,
and `filesystem_isolation.enforced_for` equal to `all_exec`. Call
`server_info` through the authenticated MCP endpoint and inspect only the
non-secret fields. The useful fields are
`credential_providers.registry.health`, `generation`, `fingerprint`,
`credential_providers.providers`, and
`credential_providers.filesystem_isolation` (backend, status, and scope).
Provider metadata contains paths, command names, and environment-variable
names, never environment values. Also verify `/healthz` and `/api/status`
locally before checking any external tunnel path.

## Root-only migration/rollback block — NEVER RUN

Replace every angle-bracket placeholder with the operator's already-reviewed
value. Repeat the `provision` command once per provider. The command may replace
an existing provider, so inspect the registry and broker first and do not rerun
this block blindly. The exact legacy bind entry placeholder must identify only
the credential bind line; do not delete other project or runtime binds. The
backup directory must be retained until post-migration verification is
complete.

```sh
set -eu

UNIT="<unit-name>"
UNIT_FILE="<unit-file>"
DROPIN="<drop-in-file>"
REGISTRY_DIR="<registry-dir>"
BROKER_DIR="<state-root>/credentials"
BACKUP_DIR="<rollback-backup-dir>"
SERVICE_UID="<service-account-uid>"
SERVICE_GID="<service-account-gid>"

rollback() {
  trap - ERR
  systemctl stop "$UNIT" || true
  # Credential fragments and broker trees are intentionally left untouched:
  # this block cannot safely distinguish its writes from concurrent state.
  cp -- "$BACKUP_DIR/unit-before" "$UNIT_FILE"
  if test -f "$BACKUP_DIR/drop-in-before"; then
    cp -- "$BACKUP_DIR/drop-in-before" "$DROPIN"
  else
    rm -f -- "$DROPIN"
  fi
  systemctl daemon-reload
  systemctl start "$UNIT"
}

# Migration: stop first and save the exact unit/drop-in state for rollback.
systemctl stop "$UNIT"
mkdir -p -- "$BACKUP_DIR"
cp -- "$UNIT_FILE" "$BACKUP_DIR/unit-before"
if test -f "$DROPIN"; then cp -- "$DROPIN" "$BACKUP_DIR/drop-in-before"; fi
trap rollback ERR

# Stage and provision each broker copy; --apply is the mutating CLI flag.
uv run --locked python scripts/credentials.py \
  --registry-dir "$REGISTRY_DIR" --broker-dir "$BROKER_DIR" \
  --service-uid "$SERVICE_UID" --service-gid "$SERVICE_GID" provision \
  --name "<provider-name>" --command "<executable-basename>" \
  --source "<source-store>" --read-root "read-only" --write-root "state" --apply

# Remove exactly the reviewed legacy credential bind line, and no other bind.
sed -i '\|<legacy-credential-bind-entry>|d' "$DROPIN"

# Ensure one stable StateDirectory directive in the [Service] drop-in.
grep -qF 'StateDirectory=<state-directory-name>' "$DROPIN" || \
  printf '\n[Service]\nStateDirectory=<state-directory-name>\n' >> "$DROPIN"

systemctl daemon-reload
systemctl start "$UNIT"
uv run --locked python scripts/credentials.py \
  --registry-dir "$REGISTRY_DIR" --broker-dir "$BROKER_DIR" \
  --service-uid "$SERVICE_UID" --service-gid "$SERVICE_GID" doctor
curl --fail --silent "<local-health-url>" >/dev/null
curl --fail --silent "<local-status-url>" >/dev/null
uv run --locked python scripts/mcp_smoke.py "<local-mcp-url>" \
  --expect-permission-mode "<permission-mode>"

# Any failed command above triggers rollback automatically. It restores only
# the saved unit/drop-in and leaves all credential state intact. To roll back
# after a successful verification, run rollback once; it reloads systemd and
# starts the prior service configuration.
trap - ERR
# rollback
```

Rollback restores the prior unit and drop-in state but deliberately does not
delete any credential fragment or broker subtree. If a failed attempt left
state that must be removed, first run `credentials doctor` with the service UID
and GID, inspect its redacted report, then use the normal dry-run
`credentials remove <name>` plan and an explicitly reviewed `remove <name>
--apply`. This manual cleanup is separate from rollback and must never target
pre-existing or concurrently changed provider state.
