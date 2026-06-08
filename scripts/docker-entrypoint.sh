#!/usr/bin/env sh
set -eu

WORKSPACE="${CODING_TOOLS_MCP_WORKSPACE:-/workspace}"
HOST="${CODING_TOOLS_MCP_HOST:-0.0.0.0}"
PORT="${CODING_TOOLS_MCP_PORT:-8765}"
MODE="${CODING_TOOLS_MCP_PERMISSION_MODE:-trusted}"
OAUTH_MODE="${CODING_TOOLS_MCP_OAUTH_MODE:-0}"

: "${CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS:=/etc/java-17-openjdk:/etc/maven:/usr/share/maven:/usr/lib/jvm/java-17-openjdk-amd64}"
export CODING_TOOLS_MCP_EXEC_ALLOW_ROOTS

if [ "$MODE" = "dangerous" ]; then
  {
    echo "WARNING: permission_mode=dangerous disables MCP safety gates."
    echo "Use only inside an isolated container or VM."
  } >&2
fi

case "$HOST" in
  ""|"127.0.0.1"|"localhost"|"::1") ;;
  *)
    if [ -z "${CODING_TOOLS_MCP_AUTH_TOKEN:-}" ] && [ "$OAUTH_MODE" != "1" ]; then
      CODING_TOOLS_MCP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
      export CODING_TOOLS_MCP_AUTH_TOKEN
      {
        echo "Generated CODING_TOOLS_MCP_AUTH_TOKEN for non-loopback Docker binding."
        echo "Bearer token: $CODING_TOOLS_MCP_AUTH_TOKEN"
      } >&2
    fi
    ;;
esac

exec coding-tools-mcp \
  --workspace "$WORKSPACE" \
  --host "$HOST" \
  --port "$PORT" \
  --permission-mode "$MODE" \
  "$@"
