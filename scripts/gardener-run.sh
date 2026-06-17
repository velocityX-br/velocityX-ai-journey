#!/usr/bin/env bash
# gardener-run.sh — fan out a kubectl command to multiple Gardener shoot clusters
#
# Usage:
#   gardener-run.sh [OPTIONS] "kubectl command"
#
# Options:
#   --garden  live|canary|cn   Target landscape (prompted if omitted)
#   --all                       Run on all shoots in the project
#   --shoots  s1,s2,s3         Run on named shoots (comma-separated)
#   --project <name>            Garden project (default: sni)
#   --dry-run                   Print commands without executing

set -euo pipefail

# ── Pre-flight ────────────────────────────────────────────────────────────────
if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "ERROR: KUBECONFIG is not set." >&2
  echo "Add the following to your shell profile and re-open your terminal:" >&2
  echo '  eval "$(gardenctl kubectl-env bash)"' >&2
  exit 1
fi

# ── Argument parsing ──────────────────────────────────────────────────────────
GARDEN=""
PROJECT="sni"
SHOOTS_ARG=""
RUN_ALL=false
DRY_RUN=false
CMD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --garden)   GARDEN="$2";      shift 2 ;;
    --project)  PROJECT="$2";     shift 2 ;;
    --shoots)   SHOOTS_ARG="$2";  shift 2 ;;
    --all)      RUN_ALL=true;     shift ;;
    --dry-run)  DRY_RUN=true;     shift ;;
    --*)
      echo "ERROR: Unknown option '$1'" >&2
      exit 1
      ;;
    *)
      CMD="$1"
      shift
      ;;
  esac
done

if [[ -z "$CMD" ]]; then
  echo "ERROR: No command provided." >&2
  echo "Usage: gardener-run.sh [OPTIONS] \"kubectl command\"" >&2
  exit 1
fi

# ── Garden selection ──────────────────────────────────────────────────────────
resolve_garden() {
  local input
  input=$(echo "$1" | tr '[:upper:]' '[:lower:]')
  case "${input#sap-landscape-}" in
    live)    echo "sap-landscape-live" ;;
    canary)  echo "sap-landscape-canary" ;;
    cn|ac-live|livecn) echo "sap-landscape-ac-live" ;;
    *)       echo "" ;;
  esac
}

if [[ -n "$GARDEN" ]]; then
  RESOLVED=$(resolve_garden "$GARDEN")
  if [[ -z "$RESOLVED" ]]; then
    echo "ERROR: Unknown garden '$GARDEN'. Valid values: live, canary, cn" >&2
    exit 1
  fi
  GARDEN="$RESOLVED"
else
  echo ""
  echo "Select the Gardener landscape:"
  echo "  1) sap-landscape-live"
  echo "  2) sap-landscape-canary"
  echo "  3) sap-landscape-ac-live (china)"
  while true; do
    read -rp "Enter choice [1-3]: " choice
    case "$choice" in
      1) GARDEN="sap-landscape-live";    break ;;
      2) GARDEN="sap-landscape-canary";  break ;;
      3) GARDEN="sap-landscape-ac-live"; break ;;
      *) echo "Invalid choice. Enter 1, 2, or 3." ;;
    esac
  done
fi

echo ""
echo "Targeting garden: $GARDEN, project: $PROJECT"
gardenctl target --garden "$GARDEN"

# ── Shoot resolution ──────────────────────────────────────────────────────────
NAMESPACE="garden-${PROJECT}"
echo "Fetching shoots from namespace '$NAMESPACE'..."
ALL_SHOOTS=()
while IFS= read -r s; do
  [[ -n "$s" ]] && ALL_SHOOTS+=("$s")
done < <(kubectl get shoots -n "$NAMESPACE" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)

if [[ ${#ALL_SHOOTS[@]} -eq 0 ]]; then
  echo "ERROR: No shoots found in namespace '$NAMESPACE'." >&2
  exit 1
fi

TARGET_SHOOTS=()

if $RUN_ALL; then
  TARGET_SHOOTS=("${ALL_SHOOTS[@]}")
elif [[ -n "$SHOOTS_ARG" ]]; then
  IFS=',' read -ra REQUESTED <<< "$SHOOTS_ARG"
  for req in "${REQUESTED[@]}"; do
    found=false
    for s in "${ALL_SHOOTS[@]}"; do
      [[ "$s" == "$req" ]] && found=true && break
    done
    if ! $found; then
      echo "ERROR: Shoot '$req' not found in garden '$GARDEN', project '$PROJECT'." >&2
      echo "Valid shoots:" >&2
      printf "  %s\n" "${ALL_SHOOTS[@]}" >&2
      exit 1
    fi
    TARGET_SHOOTS+=("$req")
  done
else
  # Interactive multi-select
  echo ""
  echo "Available shoots in '$GARDEN' / '$PROJECT':"
  for i in "${!ALL_SHOOTS[@]}"; do
    printf "  %2d) %s\n" $((i+1)) "${ALL_SHOOTS[$i]}"
  done
  echo "   a) All shoots"
  echo ""
  read -rp "Enter shoot numbers separated by spaces, or 'a' for all: " selection
  if [[ "$selection" == "a" ]]; then
    TARGET_SHOOTS=("${ALL_SHOOTS[@]}")
  else
    for tok in $selection; do
      idx=$((tok - 1))
      if [[ "$idx" -ge 0 && "$idx" -lt "${#ALL_SHOOTS[@]}" ]]; then
        TARGET_SHOOTS+=("${ALL_SHOOTS[$idx]}")
      else
        echo "ERROR: '$tok' is not a valid selection." >&2
        exit 1
      fi
    done
  fi
fi

if [[ ${#TARGET_SHOOTS[@]} -eq 0 ]]; then
  echo "ERROR: No shoots selected." >&2
  exit 1
fi

echo ""
echo "Target shoots (${#TARGET_SHOOTS[@]}):"
printf "  %s\n" "${TARGET_SHOOTS[@]}"

# ── Delete guard ──────────────────────────────────────────────────────────────
DANGEROUS_PATTERN='(^|\s)(delete|drain|cordon|taint)(\s|$)'
if echo "$CMD" | grep -Eq "$DANGEROUS_PATTERN"; then
  echo ""
  echo "⚠️  WARNING: The command contains a potentially destructive operation."
  echo "   Command : $CMD"
  echo "   Clusters: ${TARGET_SHOOTS[*]}"
  echo ""
  read -rp "Type 'yes' to confirm you want to run this on all target clusters: " confirm
  if [[ "$confirm" != "yes" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

# ── Execution ─────────────────────────────────────────────────────────────────
declare -A RESULTS

for SHOOT in "${TARGET_SHOOTS[@]}"; do
  echo ""
  echo "──────────────────────────────────────────"
  echo "[$SHOOT] Targeting..."

  if $DRY_RUN; then
    echo "[$SHOOT] DRY-RUN: gardenctl target --garden $GARDEN --project $PROJECT --shoot $SHOOT"
    echo "[$SHOOT] DRY-RUN: eval \"\$(gardenctl kubectl-env bash)\""
    echo "[$SHOOT] DRY-RUN: $CMD"
    RESULTS[$SHOOT]="dry-run"
    continue
  fi

  gardenctl target --garden "$GARDEN" --project "$PROJECT" --shoot "$SHOOT"
  eval "$(gardenctl kubectl-env bash)"

  set +e
  eval "$CMD" 2>&1 | sed "s/^/[$SHOOT] /"
  EXIT_CODE=${PIPESTATUS[0]}
  set -e

  if [[ $EXIT_CODE -eq 0 ]]; then
    RESULTS[$SHOOT]="pass"
  else
    RESULTS[$SHOOT]="fail:$EXIT_CODE"
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "Results"
echo "══════════════════════════════════════════"

OVERALL=0
for SHOOT in "${TARGET_SHOOTS[@]}"; do
  STATUS="${RESULTS[$SHOOT]}"
  case "$STATUS" in
    pass)     echo "  ✓ $SHOOT" ;;
    dry-run)  echo "  ~ $SHOOT (dry-run)" ;;
    fail:*)   echo "  ✗ $SHOOT (exit code ${STATUS#fail:})"; OVERALL=1 ;;
  esac
done

echo ""
exit $OVERALL
