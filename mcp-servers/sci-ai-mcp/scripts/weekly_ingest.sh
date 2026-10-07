#!/usr/bin/env bash
#
# weekly_ingest.sh
# ----------------
# Unattended weekly refresh of the sci-ai-mcp RAG index (Qdrant collections
# sci_docs_operation and sci_docs_customer).
#
# Ingestion (scripts/ingest_docs.py) is NOT incremental: every run does a full
# re-fetch, and re-running over an existing collection can double the point
# count (WAL keeps partial upserts). So this wrapper does a CLEAN REBUILD:
# delete the target collections, then re-ingest from scratch.
#
# The rebuild depends on two local services being up:
#   - Qdrant           (default :6333)  — the vector store
#   - SAP Hai/Hyperspace proxy (:6655)  — embeddings for ingestion
# On a laptop these are often down at night (Mac asleep, VPN off, Hai closed).
# To avoid ever wiping the index when it cannot be rebuilt, this script
# PREFLIGHTS both services and SOFT-SKIPS (exit 0, no deletion) if either is
# down — the stale-but-intact index is left for next week's run.
#
# The proxy preflight is TWO-STAGE: (a) reachability, then (b) a real embedding
# probe. Reachability alone is a trap — a reachable proxy holding an EXPIRED
# Hyperspace JWT returns a healthy-looking 401 while embeddings fail with
# "Jwt is expired", which would let the clean rebuild wipe the index and then
# re-ingest into an empty collection. The embedding probe closes that gap: it
# must return HTTP 200 with an embedding vector before any deletion happens.
#
# As a second layer of defence, each collection is SNAPSHOTTED (Qdrant
# server-side snapshot) immediately before it is deleted, so a rebuild that
# fails mid-flight can still be recovered from last week's data. On failure the
# --check exit branch logs the exact recovery commands.
#
# Designed to run unattended via launchd (see com.veloxityx.sci-ingest.plist).
#
# Usage:
#   bash scripts/weekly_ingest.sh
#
# Configuration is via environment variables (with sane defaults):
#   SCI_DIR        Project dir (has .env / scripts/)   (default: script's ../)
#   QDRANT_URL     Qdrant base URL                     (default: http://localhost:6333)
#   HAI_PROXY_URL  SAP Hai/Hyperspace proxy base URL   (default: http://localhost:6655)
#   COLLECTIONS    Aliases to rebuild                  (default: "operation customer")
#   LOG_FILE       Log file path                       (default: $SCI_DIR/logs/weekly-ingest.log)
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
# SCI_DIR defaults to the script's parent (scripts/.. == project root),
# resolved via BASH_SOURCE so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCI_DIR="${SCI_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# ---- Configuration -----------------------------------------------------------
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
HAI_PROXY_URL="${HAI_PROXY_URL:-http://localhost:6655}"
COLLECTIONS="${COLLECTIONS:-operation customer}"
LOG_FILE="${LOG_FILE:-${SCI_DIR}/logs/weekly-ingest.log}"

# Map a collection alias -> Qdrant collection name (for the DELETE step).
# A case statement is used instead of an associative array because macOS ships
# bash 3.2, which has no `declare -A`.
_coll_name() {
  case "$1" in
    operation) echo "sci_docs_operation" ;;
    customer)  echo "sci_docs_customer" ;;
    *)         echo "" ;;
  esac
}

TS() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(TS)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")"

log "=========================================================="
log "sci-ai-mcp weekly ingest starting"
log "  SCI_DIR       = $SCI_DIR"
log "  QDRANT_URL    = $QDRANT_URL"
log "  HAI_PROXY_URL = $HAI_PROXY_URL"
log "  COLLECTIONS   = $COLLECTIONS"

# ---- cd into project dir (REQUIRED for pydantic-settings to find .env) --------
# config/settings.py loads env_file=".env" relative to the current working
# directory, so ingestion only sees credentials when run from the project root.
if [[ ! -d "$SCI_DIR" ]]; then
  log "ERROR: project dir not found: $SCI_DIR"
  exit 2
fi
cd "$SCI_DIR" || { log "ERROR: cannot cd into $SCI_DIR"; exit 2; }

if [[ ! -f "scripts/ingest_docs.py" ]]; then
  log "ERROR: scripts/ingest_docs.py not found under $SCI_DIR"
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

# 3) SAP Hai/Hyperspace proxy must be REACHABLE. 401 is the healthy "awaiting
#    auth" response; 200/403 also mean the proxy is listening. NOTE: reachability
#    alone is NOT sufficient — a reachable proxy holding an EXPIRED Hyperspace
#    JWT still returns 401 here while embeddings fail with "Jwt is expired",
#    which previously let the rebuild wipe the index and re-ingest into an empty
#    collection. The decisive check is the embedding probe in step 4 below.
hai_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "${HAI_PROXY_URL}/anthropic/" 2>/dev/null)"
hai_code="${hai_code:-000}"
case "$hai_code" in
  401|200|403)
    log "Preflight: Hai proxy reachable (${hai_code})."
    ;;
  *)
    log "Hai proxy down (GET ${HAI_PROXY_URL}/anthropic/ -> ${hai_code}) — skipping, index left intact."
    exit 0
    ;;
esac

# 4) EMBEDDING probe — the decisive credential check. Send a minimal real
#    embedding request through the proxy and require HTTP 200 with an
#    "embedding" array in the body. This catches an expired Hyperspace JWT
#    (proxy reachable but embeddings rejected), which step 3 cannot see.
#    Any failure SOFT-SKIPS (exit 0) so a stale-but-intact index is preserved.
#
#    The embedder authenticates with ANTHROPIC_AUTH_TOKEN as the OpenAI-style
#    bearer key (see embeddings/openai_embedder.py). Read it from .env the same
#    way — SCI_MCP_-prefixed var wins, then the bare var; skip (do not fail) if
#    absent so misconfigured envs never trigger a destructive rebuild.
EMBED_URL="${HAI_PROXY_URL}/openai/v1/embeddings"
EMBED_MODEL="${SCI_MCP_EMBEDDING_MODEL:-${EMBEDDING_MODEL:-text-embedding-3-small}}"
_env_val() {
  # _env_val KEY — echo the value of an uncommented KEY=... line in .env,
  # stripping surrounding single/double quotes. bash 3.2 compatible.
  sed -n "s/^${1}=//p" .env 2>/dev/null | head -n1 | sed -e "s/^[\"']//" -e "s/[\"']$//"
}
embed_token="$(_env_val SCI_MCP_ANTHROPIC_AUTH_TOKEN)"
[[ -z "$embed_token" ]] && embed_token="$(_env_val ANTHROPIC_AUTH_TOKEN)"

if [[ -z "$embed_token" ]]; then
  log "Embedding probe: no ANTHROPIC_AUTH_TOKEN in .env — skipping, index left intact."
  exit 0
fi

probe_body="$(printf '{"model":"%s","input":"sci-ingest-healthcheck"}' "$EMBED_MODEL")"
probe_out="$(curl -sS -m 20 -w $'\n__HTTP__%{http_code}' \
  -H "Authorization: Bearer ${embed_token}" \
  -H "Content-Type: application/json" \
  -d "$probe_body" \
  "$EMBED_URL" 2>/dev/null)"
probe_code="$(printf '%s' "$probe_out" | sed -n 's/.*__HTTP__//p')"
probe_code="${probe_code:-000}"
probe_json="$(printf '%s' "$probe_out" | sed 's/__HTTP__.*//')"

if [[ "$probe_code" != "200" ]] || ! printf '%s' "$probe_json" | grep -q '"embedding"'; then
  # Surface the proxy's reason (e.g. "Jwt is expired") without dumping vectors.
  reason="$(printf '%s' "$probe_json" | tr -d '\n' | cut -c1-200)"
  log "Embedding probe FAILED (HTTP ${probe_code}) — skipping, index left intact."
  log "  proxy said: ${reason:-<empty>}"
  log "  hint: refresh the Hyperspace JWT (restart 'hai proxy start' or run 'hai auth login')."
  exit 0
fi
log "Preflight: embedding probe OK (200, ${EMBED_MODEL})."

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
