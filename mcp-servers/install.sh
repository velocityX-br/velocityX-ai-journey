#!/usr/bin/env bash
#
# install.sh — one-shot builder + env/token checker for the MCP servers in this repo.
#
# What it DOES:
#   • verifies required toolchains (uv, node, python3) are on PATH
#   • installs dependencies + builds each server (uv sync / npm build / pip install)
#   • ensures a .env exists (copies .env.example / env.example when missing)
#   • inspects each .env for placeholder / empty required secrets and ALERTS you
#   • PRINTS the exact `claude mcp add` command for each server (never runs it)
#
# What it does NOT do (by design):
#   • it never runs `claude mcp add` — you copy/paste the printed commands
#   • it never writes secrets — you fill the flagged .env values yourself
#   • it does not start Qdrant or run Gardener ingestion (separate, long-running)
#
# Usage:
#   ./install.sh                      # all servers
#   ./install.sh --only gardener      # subset (comma-sep: gardener,sap-wiki,plato)
#   ./install.sh --skip-build         # only check env + print commands
#   ./install.sh --help
#
set -euo pipefail

# --- locations -------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GARDENER_DIR="$SCRIPT_DIR/gardener-ai-mcp"
SAPWIKI_DIR="$SCRIPT_DIR/sap-wiki-mcp"
PLATO_DIR="$SCRIPT_DIR/plato-mcp"

# --- colours ---------------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  BLUE=$'\033[34m'; CYAN=$'\033[36m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; DIM=""; RESET=""
fi

info()  { printf '%s\n' "${BLUE}==>${RESET} $*"; }
ok()    { printf '%s\n' "${GREEN}  ✓${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}  ⚠${RESET} $*"; }
err()   { printf '%s\n' "${RED}  ✗${RESET} $*"; }
hdr()   { printf '\n%s\n' "${BOLD}${CYAN}$*${RESET}"; }

# --- collected registration commands + warnings ----------------------------
REG_CMDS=()
NEEDS_ATTENTION=()

# --- args ------------------------------------------------------------------
ONLY=""
DO_BUILD=1

usage() {
  cat <<EOF
${BOLD}mcp-servers installer${RESET}

Builds the MCP servers in this repo, checks their .env / token setup, and
prints the ${BOLD}claude mcp add${RESET} commands for you to review and run yourself.

${BOLD}Usage:${RESET}
  ./install.sh [options]

${BOLD}Options:${RESET}
  --only <list>   Comma-separated subset: gardener,sap-wiki,plato
  --skip-build    Skip dependency install/build; only check env + print commands
  -h, --help      Show this help

${BOLD}Examples:${RESET}
  ./install.sh
  ./install.sh --only gardener,sap-wiki
  ./install.sh --skip-build
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --only)       ONLY="${2:-}"; shift 2 ;;
    --skip-build) DO_BUILD=0; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) err "Unknown option: $1"; usage; exit 1 ;;
  esac
done

wants() {
  # returns 0 if server $1 should be processed
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

# --- toolchain checks ------------------------------------------------------
check_tool() {
  local name="$1" hint="$2"
  if command -v "$name" >/dev/null 2>&1; then
    ok "$name found ($("$name" --version 2>&1 | head -n1))"
    return 0
  fi
  err "$name NOT found — $hint"
  return 1
}

# --- .env handling ---------------------------------------------------------
# ensure_env <dir> <example-filename>
ensure_env() {
  local dir="$1" example="$2"
  if [ -f "$dir/.env" ]; then
    ok ".env exists"
    return 0
  fi
  if [ -f "$dir/$example" ]; then
    cp "$dir/$example" "$dir/.env"
    warn "created .env from $example — ${BOLD}you must fill in secrets${RESET}"
    return 0
  fi
  warn "no .env and no $example template found — skipping env check"
  return 1
}

# check_secret <dir> <VAR_NAME> <human label>
# Flags a var that is missing, empty, or still a placeholder.
check_secret() {
  local dir="$1" var="$2" label="$3"
  local envfile="$dir/.env"
  [ -f "$envfile" ] || return 0
  local line val
  line="$(grep -E "^${var}=" "$envfile" | tail -n1 || true)"
  if [ -z "$line" ]; then
    warn "${var} is not set in .env  ${DIM}($label)${RESET}"
    NEEDS_ATTENTION+=("$(basename "$dir"): set ${var}  — $label")
    return 0
  fi
  val="${line#*=}"
  case "$val" in
    ""|your-*|your_*|*-here|*-here"") \
      warn "${var} still a placeholder  ${DIM}($label)${RESET}"
      NEEDS_ATTENTION+=("$(basename "$dir"): fill ${var}  — $label")
      ;;
    *) ok "${var} looks set" ;;
  esac
}

# =====================================================================
# gardener-ai-mcp
# =====================================================================
install_gardener() {
  hdr "gardener-ai-mcp  (Python 3.12 · uv)"
  [ -d "$GARDENER_DIR" ] || { err "directory missing: $GARDENER_DIR"; return; }

  if [ "$DO_BUILD" -eq 1 ]; then
    if command -v uv >/dev/null 2>&1; then
      info "uv sync (installing dependencies)"
      ( cd "$GARDENER_DIR" && uv sync ) && ok "dependencies installed" \
        || err "uv sync failed"
    else
      err "uv not found — cannot build gardener-ai-mcp"
    fi
  fi

  ensure_env "$GARDENER_DIR" ".env.example"
  check_secret "$GARDENER_DIR" "GITHUB_TOKEN"        "GitHub PAT (read:repo) for github.com/gardener ingestion"
  check_secret "$GARDENER_DIR" "GITHUB_SAP_TOKEN"    "PAT for github.tools.sap (optional if not using SAP ingestion)"
  check_secret "$GARDENER_DIR" "ANTHROPIC_AUTH_TOKEN" "SAP Hyperspace bearer token (root_cause_analysis LLM calls)"

  warn "runtime deps: a reachable ${BOLD}Qdrant${RESET} (QDRANT_URL, default http://localhost:6333) and a populated"
  warn "vector store. Run ingestion separately, e.g.:  ${DIM}cd gardener-ai-mcp && uv run python -m scripts.ingest_docs${RESET}"

  REG_CMDS+=("claude mcp add --scope user gardener-ai-mcp -- uv --directory \"$GARDENER_DIR\" run python -m gardener_mcp.server")
}

# =====================================================================
# sap-wiki-mcp
# =====================================================================
install_sapwiki() {
  hdr "sap-wiki-mcp  (TypeScript · Node)"
  [ -d "$SAPWIKI_DIR" ] || { err "directory missing: $SAPWIKI_DIR"; return; }

  if [ "$DO_BUILD" -eq 1 ]; then
    if command -v npm >/dev/null 2>&1; then
      info "npm install"
      ( cd "$SAPWIKI_DIR" && npm install ) && ok "dependencies installed" \
        || err "npm install failed"
      info "npm run build (tsc)"
      ( cd "$SAPWIKI_DIR" && npm run build ) && ok "built dist/" \
        || err "build failed"
    else
      err "npm not found — cannot build sap-wiki-mcp"
    fi
  fi

  ensure_env "$SAPWIKI_DIR" "env.example"
  check_secret "$SAPWIKI_DIR" "CONFLUENCE_BASE_URL"  "your Confluence base URL"
  check_secret "$SAPWIKI_DIR" "CONFLUENCE_API_TOKEN" "Confluence API token"
  check_secret "$SAPWIKI_DIR" "CONFLUENCE_SPACE_KEYS" "space key(s), comma-separated"

  local entry="$SAPWIKI_DIR/dist/server.js"
  [ -f "$entry" ] || warn "expected build output not found yet: dist/server.js (run without --skip-build)"
  REG_CMDS+=("claude mcp add --scope user sap-wiki -- node \"$entry\"")
}

# =====================================================================
# plato-mcp
# =====================================================================
install_plato() {
  hdr "plato-mcp  (Python 3.10+ · uv)"
  [ -d "$PLATO_DIR" ] || { err "directory missing: $PLATO_DIR"; return; }

  if [ "$DO_BUILD" -eq 1 ]; then
    if command -v uv >/dev/null 2>&1; then
      info "uv pip install -r requirements.txt"
      ( cd "$PLATO_DIR" && uv venv --allow-existing >/dev/null 2>&1 || true
        cd "$PLATO_DIR" && uv pip install -r requirements.txt ) \
        && ok "dependencies installed" || err "dependency install failed"
    else
      err "uv not found — cannot install plato-mcp deps"
    fi
  fi

  # plato uses SSO (auto) — no .env required, but CA bundle + optional token matter.
  if [ -n "${PLATO_CA_BUNDLE:-}" ]; then
    ok "PLATO_CA_BUNDLE set ($PLATO_CA_BUNDLE)"
  else
    warn "${BOLD}PLATO_CA_BUNDLE${RESET} not set — required for TLS to the SAP internal Plato endpoint."
    warn "export it in your shell profile, e.g.  ${DIM}export PLATO_CA_BUNDLE=/path/to/cia-plato-agent/ca_bundle.pem${RESET}"
    NEEDS_ATTENTION+=("plato-mcp: export PLATO_CA_BUNDLE  — SAP internal CA bundle path (TLS)")
  fi
  if [ -f "$HOME/.local/cia_token/.cia_token" ]; then
    ok "CIA token file present (auto-refreshes; browser SSO on first call otherwise)"
  else
    warn "no CIA token yet — first plato_query opens a browser for SAP IAS SSO (this is expected)"
  fi

  REG_CMDS+=("claude mcp add --scope user plato-mcp -- uv --directory \"$PLATO_DIR\" run python server.py")
}

# =====================================================================
# main
# =====================================================================
hdr "Toolchain check"
check_tool uv     "install from https://docs.astral.sh/uv/ (needed for gardener + plato)" || true
check_tool node   "install Node.js (needed for sap-wiki)" || true
check_tool npm    "ships with Node.js (needed for sap-wiki)" || true
check_tool python3 "install Python 3.12+" || true

wants gardener && install_gardener
wants sap-wiki && install_sapwiki
wants plato    && install_plato

# --- summary: attention needed --------------------------------------------
hdr "Setup requiring your attention"
if [ ${#NEEDS_ATTENTION[@]} -eq 0 ]; then
  ok "No missing secrets / tokens detected."
else
  err "${#NEEDS_ATTENTION[@]} item(s) need values before the servers will work:"
  for item in "${NEEDS_ATTENTION[@]}"; do
    printf '     %s• %s%s\n' "$YELLOW" "$RESET" "$item"
  done
  printf '\n%s   Edit the relevant .env file(s) (or export the shell var) then re-run to verify.%s\n' "$DIM" "$RESET"
fi

# --- summary: registration commands ---------------------------------------
hdr "Registration commands  ${DIM}(review, then run yourself)${RESET}"
printf '%s   These are NOT executed by this script. Copy/paste the ones you want:%s\n\n' "$DIM" "$RESET"
for cmd in "${REG_CMDS[@]}"; do
  printf '%s%s%s\n\n' "$BOLD" "$cmd" "$RESET"
done
printf '%sVerify afterwards with:%s  claude mcp list\n' "$DIM" "$RESET"
