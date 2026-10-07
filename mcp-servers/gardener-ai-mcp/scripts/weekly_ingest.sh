#!/usr/bin/env bash
#
# weekly_ingest.sh
# ----------------
# Unattended weekly refresh of the gardener-ai-mcp RAG index (Qdrant
# collections gardener_docs, gardener_issues, gardener_prs, gardener_code).
#
# Ingestion (scripts/ingest_docs.py) is NOT idempotent: Document IDs are random
# UUIDs (ingestion/base.py), so re-running over an existing collection appends
# fresh chunks instead of overwriting — the point count keeps growing and
# upstream deletions are never reflected (see docs/ingestion-runbook.md §4).
# So this wrapper does a CLEAN REBUILD: delete the target collections, then
# re-ingest from scratch. ingest_docs.py auto-recreates each collection with
# the correct HNSW config.
#
# The rebuild depends on two local services being up:
#   - Qdrant           (default :6333)  — the vector store
#   - SAP Hai/Hyperspace proxy (:6655)  — embeddings for ingestion
# ...and on a VALID GitHub token (docs/issues/prs/code are fetched from
# github.com/gardener/*). On a laptop these are often down at night (Mac
# asleep, VPN off, Hai closed) and the token may have silently expired.
#
# To avoid ever wiping the index when it cannot be rebuilt, this script
# PREFLIGHTS all four preconditions (Qdrant, Hai proxy, uv, GitHub token) and
# SOFT-SKIPS (exit 0, no deletion) if any is not ready — the stale-but-intact
# index is left for next week's run.
#
# As a second layer of defence, each collection is SNAPSHOTTED (Qdrant
# server-side snapshot) immediately before it is deleted, so a rebuild that
# fails mid-flight can still be recovered from last week's data.
#
# (History: on 2026-09-07 an expired GITHUB_TOKEN passed the old service-only
#  preflight, the DELETE ran, and the re-ingest died on `401 Bad credentials`,
#  wiping all four collections with no backup. The token + snapshot safeguards
#  below exist to make that failure mode impossible.)
#
# Designed to run unattended via launchd (see com.veloxityx.gardener-ingest.plist).
#
# Usage:
#   bash scripts/weekly_ingest.sh
#
# Configuration is via environment variables (with sane defaults):
#   GARDENER_DIR   Project dir (has .env / scripts/)   (default: script's ../)
#   QDRANT_URL     Qdrant base URL                     (default: http://localhost:6333)
#   HAI_PROXY_URL  SAP Hai/Hyperspace proxy base URL   (default: http://localhost:6655)
#   COLLECTIONS    Aliases to rebuild                  (default: "docs issues prs code")
#   LOG_FILE       Log file path                       (default: $GARDENER_DIR/logs/weekly-ingest.log)
#
# Exit codes:
#   0  — rebuild succeeded, OR soft-skip (a service was down; index left intact)
#   1  — rebuild ran but a collection ended up empty (propagated from --check)
#   2  — misconfiguration (project dir / ingest script not found)
#
set -uo pipefail

# launchd starts with a minimal PATH; uv lives in Homebrew or ~/.local/bin.
# Make sure the common tool locations are present so `uv` and `curl` resolve.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ---- Resolve project dir -----------------------------------------------------
# GARDENER_DIR defaults to the script's parent (scripts/.. == project root),
# resolved via BASH_SOURCE so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GARDENER_DIR="${GARDENER_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# ---- Configuration -----------------------------------------------------------
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
HAI_PROXY_URL="${HAI_PROXY_URL:-http://localhost:6655}"
COLLECTIONS="${COLLECTIONS:-docs issues prs code}"
LOG_FILE="${LOG_FILE:-${GARDENER_DIR}/logs/weekly-ingest.log}"

# Map a collection alias -> Qdrant collection name (for the DELETE step).
# A case statement is used instead of an associative array because macOS ships
# bash 3.2, which has no `declare -A`.
_coll_name() {
  case "$1" in
    docs)   echo "gardener_docs" ;;
    issues) echo "gardener_issues" ;;
    prs)    echo "gardener_prs" ;;
    code)   echo "gardener_code" ;;
    *)      echo "" ;;
  esac
}

TS() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(TS)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")"

log "=========================================================="
log "gardener-ai-mcp weekly ingest starting"
log "  GARDENER_DIR  = $GARDENER_DIR"
log "  QDRANT_URL    = $QDRANT_URL"
log "  HAI_PROXY_URL = $HAI_PROXY_URL"
log "  COLLECTIONS   = $COLLECTIONS"

# ---- cd into project dir (REQUIRED for pydantic-settings to find .env) --------
# config/settings.py loads env_file=".env" relative to the current working
# directory, so ingestion only sees credentials when run from the project root.
if [[ ! -d "$GARDENER_DIR" ]]; then
  log "ERROR: project dir not found: $GARDENER_DIR"
  exit 2
fi
cd "$GARDENER_DIR" || { log "ERROR: cannot cd into $GARDENER_DIR"; exit 2; }

if [[ ! -f "scripts/ingest_docs.py" ]]; then
  log "ERROR: scripts/ingest_docs.py not found under $GARDENER_DIR"
  exit 2
fi

# ---- Preflight (skip-safe: NO deletion happens before all checks pass) -------

# 1) uv must be available.
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found on PATH — skipping, index left intact."
  exit 0
fi

# 2) Qdrant must answer GET /collections with 200.
qdrant_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${QDRANT_URL}/collections" 2>/dev/null)"
qdrant_code="${qdrant_code:-000}"
if [[ "$qdrant_code" != "200" ]]; then
  log "Qdrant down (GET ${QDRANT_URL}/collections -> ${qdrant_code}) — skipping, index left intact."
  exit 0
fi
log "Preflight: Qdrant OK (200)."

# 3) SAP Hai/Hyperspace proxy must be up. 401 is the healthy "awaiting auth"
#    response; 200/403 also mean the proxy is reachable.
hai_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${HAI_PROXY_URL}/anthropic/" 2>/dev/null)"
hai_code="${hai_code:-000}"
case "$hai_code" in
  401|200|403)
    log "Preflight: Hai proxy OK (${hai_code})."
    ;;
  *)
    log "Hai proxy down (GET ${HAI_PROXY_URL}/anthropic/ -> ${hai_code}) — skipping, index left intact."
    exit 0
    ;;
esac

# 4) GitHub token must be present AND valid.
#    This is the check that would have prevented the 2026-09-07 data loss:
#    the token had expired, the DELETE ran anyway, and the re-ingest then died
#    on `401 Bad credentials`, leaving all four collections wiped. We validate
#    the token against api.github.com/rate_limit (a cheap, side-effect-free
#    endpoint) BEFORE any deletion. On failure we soft-skip (exit 0) so the
#    stale-but-intact index survives.
#
#    The token is read from the same env vars pydantic-settings uses
#    (GARDENER_MCP_GITHUB_TOKEN preferred, then GITHUB_TOKEN); if not already
#    exported, we source it from the project's .env.
GH_TOKEN="${GARDENER_MCP_GITHUB_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$GH_TOKEN" && -f ".env" ]]; then
  # Extract GITHUB_TOKEN=... from .env without sourcing the whole file
  # (avoids executing arbitrary lines). Strips optional surrounding quotes
  # and any surrounding whitespace.
  GH_TOKEN="$(grep -E '^(GARDENER_MCP_)?GITHUB_TOKEN=' .env | head -1 | cut -d= -f2-)"
  GH_TOKEN="${GH_TOKEN%\"}"; GH_TOKEN="${GH_TOKEN#\"}"
  GH_TOKEN="${GH_TOKEN%\'}"; GH_TOKEN="${GH_TOKEN#\'}"
  GH_TOKEN="$(printf '%s' "$GH_TOKEN" | tr -d '[:space:]')"
fi

if [[ -z "$GH_TOKEN" ]]; then
  log "GitHub token missing (GITHUB_TOKEN unset and not in .env) — skipping, index left intact."
  exit 0
fi

gh_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/rate_limit" 2>/dev/null)"
gh_code="${gh_code:-000}"
if [[ "$gh_code" != "200" ]]; then
  log "GitHub token INVALID (GET api.github.com/rate_limit -> ${gh_code}) — skipping, index left intact."
  log "  Refresh GITHUB_TOKEN in ${GARDENER_DIR}/.env, then re-run."
  exit 0
fi
log "Preflight: GitHub token OK (200)."

# ---- Baseline: before-counts -------------------------------------------------
log "Baseline collection counts (before rebuild):"
QDRANT_URL="$QDRANT_URL" uv run python scripts/ingest_docs.py --check 2>&1 | tee -a "$LOG_FILE" || true

# ---- Clean rebuild: snapshot, delete target collections, then re-ingest ------
# SAFETY NET: before deleting each collection we ask Qdrant to take a snapshot
# (POST /collections/{name}/snapshots). Snapshots live on the Qdrant server's
# own storage volume and SURVIVE the collection delete, so if the re-ingest
# fails we still have a recoverable copy of last week's data. Recover with:
#   PUT /collections/{name}/snapshots/recover  {"location": "<snapshot_name>"}
# Snapshot failures are non-fatal (logged as WARN) — a missing snapshot must
# not block the weekly refresh, but it is surfaced so it can be investigated.
for alias in $COLLECTIONS; do
  name="$(_coll_name "$alias")"
  if [[ -z "$name" ]]; then
    log "WARN: unknown collection alias '$alias' — skipping its snapshot+delete."
    continue
  fi

  # Only snapshot if the collection currently exists (avoid noisy 404s on a
  # first-ever run where nothing has been ingested yet).
  exists_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
    "${QDRANT_URL}/collections/${name}" 2>/dev/null)"
  if [[ "${exists_code:-000}" == "200" ]]; then
    snap_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 \
      -X POST "${QDRANT_URL}/collections/${name}/snapshots" 2>/dev/null)"
    snap_code="${snap_code:-000}"
    if [[ "$snap_code" == "200" ]]; then
      log "Snapshot taken for '${name}' (HTTP 200) — recoverable if rebuild fails."
    else
      log "WARN: snapshot for '${name}' failed (HTTP ${snap_code}) — proceeding without safety net."
    fi
  else
    log "Collection '${name}' does not exist yet (HTTP ${exists_code:-000}) — no snapshot needed."
  fi

  del_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
    -X DELETE "${QDRANT_URL}/collections/${name}" 2>/dev/null)"
  del_code="${del_code:-000}"
  log "Deleted collection '${name}' (HTTP ${del_code})."
done

# Give Qdrant a moment to settle the deletions before recreating.
sleep 2

log "Re-ingesting collections: $COLLECTIONS"
# ingest_docs.py auto-recreates each collection with the correct HNSW config.
# shellcheck disable=SC2086  # COLLECTIONS is an intentional word-split list.
QDRANT_URL="$QDRANT_URL" uv run python scripts/ingest_docs.py --collections $COLLECTIONS 2>&1 | tee -a "$LOG_FILE"
ingest_rc="${PIPESTATUS[0]}"
log "Ingestion exit code: $ingest_rc"

# ---- Verify + exit code ------------------------------------------------------
log "Final collection counts (after rebuild):"
QDRANT_URL="$QDRANT_URL" uv run python scripts/ingest_docs.py --check 2>&1 | tee -a "$LOG_FILE"
check_rc="${PIPESTATUS[0]}"

if [[ "$check_rc" -ne 0 ]]; then
  log "DONE with FAILURE: at least one collection is empty (--check exit ${check_rc})."
  log "  RECOVERY: pre-delete snapshots were taken this run. Restore the last-good"
  log "  data per collection with:"
  log "    SNAP=\$(curl -s \"${QDRANT_URL}/collections/<name>/snapshots\" | python3 -c 'import sys,json; s=json.load(sys.stdin)[\"result\"]; print(sorted(s,key=lambda x:x[\"creation_time\"])[-1][\"name\"]) if s else \"\"')"
  log "    curl -X PUT \"${QDRANT_URL}/collections/<name>/snapshots/recover\" -H 'Content-Type: application/json' -d \"{\\\"location\\\": \\\"\$SNAP\\\"}\""
  exit "$check_rc"
fi

log "DONE: rebuild verified, all target collections populated."
exit 0
