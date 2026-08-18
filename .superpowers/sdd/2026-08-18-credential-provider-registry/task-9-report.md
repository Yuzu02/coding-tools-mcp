# Task 9 report — Document the Git broker profile

## Result

Updated the operator documentation for a generic Git credential provider.
The docs now use the implemented repeatable `--env-path
NAME=relative-broker-path` and `--env-passthrough NAME` syntax, with global
registry, broker, and service identity options before `provision`.

The profile documents separate broker-owned configuration for Git commit
author/committer metadata (`GIT_CONFIG_GLOBAL`) and GitHub CLI authentication
(`GH_CONFIG_DIR`). It states that root stages the source with the operator's
canonical identity, that the repository's pinned identity is not an automatic
deployment property, and that `gh auth status` does not verify commit metadata.

## Scope

Changed only:

- `docs/services-launcher.md`
- `docs/credential-provider-migration.md`
- this report

No runtime/admin code, Git configuration/history, host state, credentials, or
deployment state was changed.

## Validation

- `git diff --check` passed.
- Reviewed the diff for secret values, personal identities, private host
  markers, and commands that read or print credential contents.
- Confirmed there is exactly one root-only migration block, marked as not for
  routine execution, in the migration page.
