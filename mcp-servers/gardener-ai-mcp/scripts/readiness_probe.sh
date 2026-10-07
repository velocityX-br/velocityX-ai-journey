#!/usr/bin/env bash
# =============================================================================
# readiness_probe.sh — Verify the gardener-ai-mcp stack is ready for a
# Claude Code MCP session (stdio transport).
#
# Purpose:
#   Before starting or restarting Claude Code, run this script to confirm
#   that every dependency the MCP server needs at stdio-startup is reachable.
#   A green run here means the 30-second Claude Code MCP handshake will
#   succeed; a red run tells you exactly which layer to fix.
#
# What it checks (in order — fails fast on first error):
#   1. Required CLIs on PATH:        kubectl, uv, curl
#   2. Correct kubectl context:      kind-gardener-ai-mcp
#   3. Pods running in ns:           gardener-ai-mcp deployment + qdrant
#   4. Port-forward: MCP HTTP        localhost:8080 (only if SSE deploy used)
#   5. Port-forward: Qdrant          localhost:6333  → /collections
#   6. SAP Hai proxy:                localhost:6655  → /anthropic/ returns 401
#      (401 is the expected "healthy" response — proves the proxy is up)
#   7. Qdrant collections present:   gardener_docs, gardener_issues
#   8. MCP stdio handshake:          spawn `uv run python -m gardener_mcp.server`
#                                    send JSON-RPC `initialize`, expect a
#                                    result within 15 seconds.
#
# Usage:
#   bash scripts/readiness_probe.sh          # normal run
#   bash scripts/readiness_probe.sh --quiet  # only print failures + summary
#   bash scripts/readiness_probe.sh --help
#
# Exit codes:
#   0  — all checks passed; Claude Code can (re)connect
#   1  — at least one check failed (see red lines above the summary)
#   2  — invalid usage
#
# Environment overrides:
#   QDRANT_URL           default: http://localhost:6333
#   HAI_PROXY_URL        default: http://localhost:6655
#   KUBE_CONTEXT         default: kind-gardener-ai-mcp
#   KUBE_NAMESPACE       default: gardener-mcp
#   HANDSHAKE_TIMEOUT    default: 15   (seconds)
# =============================================================================

set -u
set -o pipefail

# -----------------------------------------------------------------------------
# CLI parsing
# -----------------------------------------------------------------------------
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --quiet|-q) QUIET=1 ;;
        --help|-h)
            # Print the leading comment block (lines starting with "#")
            # up to the first blank/non-comment line.
            awk 'NR>1 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (use --help)" >&2
            exit 2
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Config (env-overridable)
# -----------------------------------------------------------------------------
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
HAI_PROXY_URL="${HAI_PROXY_URL:-http://localhost:6655}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-gardener-ai-mcp}"
KUBE_NAMESPACE="${KUBE_NAMESPACE:-gardener-mcp}"
HANDSHAKE_TIMEOUT="${HANDSHAKE_TIMEOUT:-15}"

# Resolve script/project dirs so the probe works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# -----------------------------------------------------------------------------
# ANSI helpers (only when stdout is a TTY)
# -----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
    BLUE=$'\033[34m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; RESET=""
fi

FAILURES=()
STEP=0

# Print an info line (suppressed under --quiet, but headers always show).
log()  { [[ $QUIET -eq 1 ]] || echo "$@"; }
head_() { echo "${BOLD}${BLUE}==>${RESET} $*"; }

pass() {
    STEP=$((STEP + 1))
    log "  ${GREEN}✓${RESET} $*"
}

fail() {
    STEP=$((STEP + 1))
    echo "  ${RED}✗${RESET} $*" >&2
    FAILURES+=("$*")
}

# -----------------------------------------------------------------------------
# 1. Required CLIs
# -----------------------------------------------------------------------------
head_ "1/8  Required CLIs on PATH"
for bin in kubectl uv curl; do
    if command -v "$bin" >/dev/null 2>&1; then
        pass "$bin  ($(command -v "$bin"))"
    else
        fail "$bin  NOT FOUND on PATH"
    fi
done

# -----------------------------------------------------------------------------
# 2. kubectl context
# -----------------------------------------------------------------------------
head_ "2/8  kubectl context"
if command -v kubectl >/dev/null 2>&1; then
    current_ctx="$(kubectl config current-context 2>/dev/null || echo '')"
    if [[ "$current_ctx" == "$KUBE_CONTEXT" ]]; then
        pass "current-context = ${current_ctx}"
    else
        fail "current-context is '${current_ctx}', expected '${KUBE_CONTEXT}' — run: kubectl config use-context ${KUBE_CONTEXT}"
    fi
else
    fail "kubectl unavailable — skipping"
fi

# -----------------------------------------------------------------------------
# 3. Pods running
# -----------------------------------------------------------------------------
head_ "3/8  Pods in namespace ${KUBE_NAMESPACE}"
if command -v kubectl >/dev/null 2>&1; then
    pods_json="$(kubectl --context="$KUBE_CONTEXT" -n "$KUBE_NAMESPACE" get pods -o json 2>/dev/null || echo '')"
    if [[ -z "$pods_json" ]]; then
        fail "kubectl cannot list pods in ${KUBE_NAMESPACE} — is the cluster running?"
    else
        mcp_ready=$(echo "$pods_json" | grep -o '"name": *"gardener-ai-mcp-[^"]*"' | grep -v qdrant | head -1)
        qdrant_ready=$(echo "$pods_json" | grep -o '"name": *"gardener-ai-mcp-qdrant-[^"]*"' | head -1)
        if [[ -n "$mcp_ready" ]]; then
            pass "gardener-ai-mcp deployment pod found"
        else
            fail "no gardener-ai-mcp deployment pod found (this is optional if you only use stdio locally)"
        fi
        if [[ -n "$qdrant_ready" ]]; then
            pass "gardener-ai-mcp-qdrant-0 pod found"
        else
            fail "gardener-ai-mcp-qdrant-0 pod NOT found — bring up the kind stack (bash scripts/setup_kind.sh)"
        fi
    fi
else
    fail "kubectl unavailable — skipping"
fi

# -----------------------------------------------------------------------------
# 4. Optional MCP HTTP port-forward (only informational)
# -----------------------------------------------------------------------------
head_ "4/8  Optional: MCP HTTP port-forward on :8080"
if curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:8080/" 2>/dev/null | grep -qE '^(200|404)$'; then
    pass "localhost:8080 reachable (only needed for SSE transport)"
else
    log "  ${YELLOW}~${RESET} localhost:8080 not reachable — fine for stdio transport"
fi

# -----------------------------------------------------------------------------
# 5. Qdrant port-forward
# -----------------------------------------------------------------------------
head_ "5/8  Qdrant reachable at ${QDRANT_URL}"
qdrant_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${QDRANT_URL}/collections" || echo '000')"
if [[ "$qdrant_code" == "200" ]]; then
    pass "GET ${QDRANT_URL}/collections → 200"
else
    fail "GET ${QDRANT_URL}/collections → ${qdrant_code} — start the port-forward: kubectl --context=${KUBE_CONTEXT} -n ${KUBE_NAMESPACE} port-forward svc/gardener-ai-mcp-qdrant 6333:6333 &"
fi

# -----------------------------------------------------------------------------
# 6. SAP Hai proxy (Hyperspace passthrough)
# -----------------------------------------------------------------------------
head_ "6/8  SAP Hai proxy at ${HAI_PROXY_URL}/anthropic/"
hai_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${HAI_PROXY_URL}/anthropic/" || echo '000')"
case "$hai_code" in
    401)
        pass "GET /anthropic/ → 401 (expected — proxy is up, awaiting auth)"
        ;;
    200|403)
        pass "GET /anthropic/ → ${hai_code}"
        ;;
    000)
        fail "GET /anthropic/ → connection failed — start the SAP Hai desktop app"
        ;;
    *)
        fail "GET /anthropic/ → ${hai_code} — unexpected status; is the Hai proxy healthy?"
        ;;
esac

# -----------------------------------------------------------------------------
# 7. Qdrant collections present
# -----------------------------------------------------------------------------
head_ "7/8  Qdrant collections"
if [[ "$qdrant_code" == "200" ]]; then
    cols_json="$(curl -sS --max-time 5 "${QDRANT_URL}/collections" || echo '')"
    for coll in gardener_docs gardener_issues; do
        if echo "$cols_json" | grep -q "\"name\": *\"${coll}\""; then
            pass "collection '${coll}' present"
        else
            fail "collection '${coll}' MISSING — run: uv run python scripts/ingest_docs.py"
        fi
    done
else
    log "  ${YELLOW}~${RESET} skipped (Qdrant not reachable)"
fi

# -----------------------------------------------------------------------------
# 8. MCP stdio handshake (the big one)
# -----------------------------------------------------------------------------
head_ "8/8  MCP stdio handshake (timeout ${HANDSHAKE_TIMEOUT}s)"
if ! command -v uv >/dev/null 2>&1; then
    fail "uv unavailable — cannot exercise the MCP server"
else
    # We can't rely on GNU `timeout` on macOS. Use a background PID + kill.
    tmp_stdout="$(mktemp -t gardener_mcp_probe.stdout.XXXXXX)"
    tmp_stderr="$(mktemp -t gardener_mcp_probe.stderr.XXXXXX)"
    init_json='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"readiness-probe","version":"1.0"}}}'

    (
        printf '%s\n' "$init_json"
        # Keep stdin open long enough for the server to write its response.
        sleep "$HANDSHAKE_TIMEOUT"
    ) | uv --directory "$PROJECT_DIR" run python -m gardener_mcp.server \
        >"$tmp_stdout" 2>"$tmp_stderr" &
    server_pid=$!

    # Poll stdout for a JSON-RPC response containing "serverInfo".
    deadline=$((SECONDS + HANDSHAKE_TIMEOUT))
    got_response=0
    while (( SECONDS < deadline )); do
        if grep -q '"serverInfo"' "$tmp_stdout" 2>/dev/null; then
            got_response=1
            break
        fi
        sleep 0.5
    done

    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true

    if [[ $got_response -eq 1 ]]; then
        version="$(grep -o '"version": *"[^"]*"' "$tmp_stdout" | head -1 || echo '?')"
        pass "server responded to initialize (${version})"
    else
        fail "server did NOT respond to initialize within ${HANDSHAKE_TIMEOUT}s"
        echo "" >&2
        echo "${YELLOW}--- last 15 lines of server stderr: ---${RESET}" >&2
        tail -15 "$tmp_stderr" >&2 || true
    fi

    rm -f "$tmp_stdout" "$tmp_stderr"
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo ""
if [[ ${#FAILURES[@]} -eq 0 ]]; then
    echo "${BOLD}${GREEN}READY${RESET}  —  all ${STEP} checks passed. Claude Code can (re)connect."
    exit 0
else
    echo "${BOLD}${RED}NOT READY${RESET}  —  ${#FAILURES[@]} of ${STEP} checks failed:"
    for f in "${FAILURES[@]}"; do
        echo "  ${RED}•${RESET} $f"
    done
    exit 1
fi
