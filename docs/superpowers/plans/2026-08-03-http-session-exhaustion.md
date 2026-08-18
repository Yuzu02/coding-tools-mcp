# HTTP Session Exhaustion Plan

**Status:** SUPERSEDED — HTTP transport is now stateless; retained as incident history.

**Goal:** Prevent tunnel clients that create a fresh MCP session for each tool
call from exhausting the 128-session HTTP limit.

**Observed failure:** The tunnel log contained exactly 128 unique initialized
sessions in one hour. Every later `initialize` reached the local MCP server and
received `503 Service Unavailable` with `maximum HTTP session count reached`;
the connector surfaced those responses as `502`.

**Design:** Preserve the legacy stateful policy while adding an explicit
ephemeral policy. The session manager tracks in-flight requests, expires only
idle sessions, and may evict the least-recently-used idle session at capacity.
Workspace command ownership remains outside transport sessions.

## Verification

- Unit-test idle TTL, active-session protection, LRU eviction, rejection, and
  more than 128 sequential sessions.
- Integration-test fresh HTTP clients exceeding a deliberately small retained
  session capacity without sending `DELETE`.
- Run Ruff, mypy, the full unittest suite, and `git diff --check`.
