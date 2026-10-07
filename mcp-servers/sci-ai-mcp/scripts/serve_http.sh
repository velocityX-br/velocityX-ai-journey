#!/usr/bin/env bash
#
# serve_http.sh
# -------------
# Run the sci-ai-mcp server in the FOREGROUND over HTTP (streamable-http),
# exposing it on a local TCP port so other programs / MCP clients can connect
# without needing an external mcp-proxy.
#
# FastMCP natively serves the "http" (streamable-http) and "sse" transports,
# so no proxy layer is required — this script just launches the server with
# the right transport + host/port env vars.
#
# Usage:
#   bash scripts/serve_http.sh                 # http on 127.0.0.1:8899
#   HOST=0.0.0.0 PORT=9000 bash scripts/serve_http.sh
#   TRANSPORT=sse bash scripts/serve_http.sh   # legacy SSE transport
#
# Endpoints (once running):
#   http  transport → http://HOST:PORT/mcp
#   sse   transport → http://HOST:PORT/sse
#
# Configuration (env vars, with defaults):
#   SCI_DIR    Project dir (has .env)          (default: script's ../)
#   TRANSPORT  http | sse | streamable-http    (default: http)
#   HOST       Bind address                    (default: 127.0.0.1)
#   PORT       Bind port                        (default: 8899)
#
set -uo pipefail

# Ensure uv resolves regardless of the caller's minimal PATH.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ---- Resolve project dir -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCI_DIR="${SCI_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# ---- Configuration -----------------------------------------------------------
TRANSPORT="${TRANSPORT:-http}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8899}"

cd "${SCI_DIR}" || { echo "ERROR: project dir not found: ${SCI_DIR}" >&2; exit 2; }

# Export transport settings so the FastMCP server binds on HOST:PORT.
# These aliases are recognised by config/settings.py.
export SCI_MCP_TRANSPORT="${TRANSPORT}"
export SCI_MCP_HOST="${HOST}"
export SCI_MCP_PORT="${PORT}"

# Pick the endpoint path for the friendly banner.
case "${TRANSPORT}" in
  sse) ENDPOINT="http://${HOST}:${PORT}/sse" ;;
  *)   ENDPOINT="http://${HOST}:${PORT}/mcp" ;;
esac

echo "======================================================================"
echo " sci-ai-mcp — serving over ${TRANSPORT}"
echo " Endpoint : ${ENDPOINT}"
echo " Project  : ${SCI_DIR}"
echo " Stop     : Ctrl-C"
echo "======================================================================"

# Run in the foreground (blocks). uv run loads .env via pydantic-settings.
exec uv run python -m sci_mcp.server
