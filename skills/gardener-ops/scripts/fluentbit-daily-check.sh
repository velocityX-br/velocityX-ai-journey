#!/usr/bin/env bash
#
# fluentbit-daily-check.sh
# ------------------------
# Daily health check of fluent-bit pods in the `monitoring` namespace of a
# SAP Gardener shoot cluster. Produces a plain-text report summarizing which
# fluent-bit pods have restarted (with restart count, reason, exit code, and
# last termination time) and emails it via the local sendmail binary.
#
# Designed to run unattended via launchd. Because Gardener live uses an
# interactive browser SSO, this script CANNOT refresh an expired session on
# its own. If login fails, it sends an "auth expired" alert email instead of
# a false healthy report, then exits non-zero.
#
# Usage:
#   fluentbit-daily-check.sh
#
# Configuration is via environment variables (with sane defaults):
#   MAIL_TO       Recipient email address              (REQUIRED)
#   MAIL_FROM     Sender email address                 (default: <user>@localhost)
#   GARDEN        Gardener landscape                    (default: live)
#   SHOOT         Shoot cluster name                    (default: sni-staging)
#   NAMESPACE     Namespace to inspect                  (default: monitoring)
#   POD_FILTER    grep pattern to select pods           (default: fluent)
#   LOGIN_CMD     Login command                         (default: gardener_sni_login)
#   LOG_FILE      Log file path                         (default: ~/Library/Logs/fluentbit-check.log)
#
set -uo pipefail

# ---- Configuration -----------------------------------------------------------
MAIL_TO="${MAIL_TO:-}"
MAIL_FROM="${MAIL_FROM:-$(whoami)@localhost}"
GARDEN="${GARDEN:-live}"
SHOOT="${SHOOT:-sni-staging}"
NAMESPACE="${NAMESPACE:-monitoring}"
POD_FILTER="${POD_FILTER:-fluent}"
LOGIN_CMD="${LOGIN_CMD:-gardener_sni_login}"
LOG_FILE="${LOG_FILE:-$HOME/Library/Logs/fluentbit-check.log}"
SENDMAIL_BIN="${SENDMAIL_BIN:-/usr/sbin/sendmail}"

# launchd runs with a minimal PATH; make sure common tool locations are present
# and that the user's login environment (which defines gardener_sni_login,
# gardenctl, kubectl) is sourced.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

TS() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(TS)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")"

if [[ -z "$MAIL_TO" ]]; then
  log "ERROR: MAIL_TO is not set. Refusing to run. Set MAIL_TO to a recipient address."
  exit 2
fi

# ---- Load login helper if it is a shell function ----------------------------
# gardener_sni_login is typically defined as a function/alias in the user's
# shell profile. Source common profiles so it becomes available under launchd.
for rc in "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile" "$HOME/.bashrc"; do
  # shellcheck disable=SC1090
  [[ -f "$rc" ]] && source "$rc" >/dev/null 2>&1
done

# ---- Email helper ------------------------------------------------------------
# Sends a plain-text email via sendmail. Args: <subject> <body-file>
send_mail() {
  local subject="$1" body_file="$2"
  {
    echo "From: ${MAIL_FROM}"
    echo "To: ${MAIL_TO}"
    echo "Subject: ${subject}"
    echo "Content-Type: text/plain; charset=UTF-8"
    echo "MIME-Version: 1.0"
    echo
    cat "$body_file"
  } | "$SENDMAIL_BIN" -t -i
  local rc=$?
  if [[ $rc -eq 0 ]]; then
    log "Email handed to sendmail OK (subject: ${subject})"
  else
    log "ERROR: sendmail exited with code $rc"
  fi
  return $rc
}

REPORT="$(mktemp -t fluentbit-report)"
trap 'rm -f "$REPORT"' EXIT

# ---- Step 1: Login -----------------------------------------------------------
log "Starting fluent-bit check: garden=$GARDEN shoot=$SHOOT ns=$NAMESPACE"

if ! command -v "$LOGIN_CMD" >/dev/null 2>&1 && ! type "$LOGIN_CMD" >/dev/null 2>&1; then
  log "ERROR: login command '$LOGIN_CMD' not found even after sourcing profiles."
  {
    echo "fluent-bit daily check could not run."
    echo
    echo "Reason: login command '$LOGIN_CMD' was not found in the launchd environment."
    echo "Fix: ensure it is defined in your shell profile (~/.zshrc etc.)."
    echo
    echo "Time: $(TS)"
  } > "$REPORT"
  send_mail "[FAIL] fluent-bit check ($SHOOT): login tool missing" "$REPORT"
  exit 3
fi

LOGIN_OUT="$("$LOGIN_CMD" "$GARDEN" "$SHOOT" 2>&1)"
LOGIN_RC=$?
log "Login exit code: $LOGIN_RC"

# Verify we actually have a working session by probing the cluster.
if [[ $LOGIN_RC -ne 0 ]] || ! kubectl get ns "$NAMESPACE" >/dev/null 2>&1; then
  log "ERROR: login or cluster access failed. Sending auth-expired alert."
  {
    echo "⚠️  fluent-bit daily check FAILED — Gardener session unavailable."
    echo
    echo "Cluster : $GARDEN / $SHOOT"
    echo "Namespace: $NAMESPACE"
    echo
    echo "The unattended check could not authenticate to the Gardener live"
    echo "landscape. This is expected when the interactive browser SSO token"
    echo "has expired — launchd cannot complete the browser login."
    echo
    echo "ACTION REQUIRED:"
    echo "  Run this in your terminal to refresh the session:"
    echo "      $LOGIN_CMD $GARDEN $SHOOT"
    echo
    echo "----- login output (last lines) -----"
    echo "$LOGIN_OUT" | tail -n 15
    echo
    echo "Time: $(TS)"
  } > "$REPORT"
  send_mail "[FAIL] fluent-bit check ($SHOOT): auth expired — re-login needed" "$REPORT"
  exit 4
fi

log "Cluster access confirmed."

# ---- Step 2: List fluent-bit pods -------------------------------------------
PODS="$(kubectl get pods -n "$NAMESPACE" --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null | grep -i "$POD_FILTER")"

if [[ -z "$PODS" ]]; then
  log "No pods matching '$POD_FILTER' found in namespace $NAMESPACE."
  {
    echo "fluent-bit daily check — $GARDEN/$SHOOT — ns:$NAMESPACE"
    echo "Generated: $(TS)"
    echo
    echo "⚠️  No pods matching '$POD_FILTER' found in namespace '$NAMESPACE'."
    echo "This itself may indicate a problem (fluent-bit DaemonSet not running)."
  } > "$REPORT"
  send_mail "[WARN] fluent-bit check ($SHOOT): no fluent-bit pods found" "$REPORT"
  exit 5
fi

POD_COUNT="$(echo "$PODS" | wc -l | tr -d ' ')"
log "Found $POD_COUNT fluent-bit pod(s)."

# ---- Step 3: Build report ----------------------------------------------------
restarted_count=0
{
  echo "fluent-bit Daily Restart Report"
  echo "==============================="
  echo "Cluster   : $GARDEN / $SHOOT"
  echo "Namespace : $NAMESPACE"
  echo "Generated : $(TS)"
  echo "Total pods: $POD_COUNT"
  echo
  printf "%-24s %-8s %-10s %-9s %-22s %s\n" "POD" "RESTARTS" "REASON" "EXITCODE" "LAST_TERMINATED(UTC)" "STATUS"
  printf "%-24s %-8s %-10s %-9s %-22s %s\n" "------------------------" "--------" "----------" "---------" "----------------------" "------"
} > "$REPORT"

while IFS= read -r pod; do
  [[ -z "$pod" ]] && continue
  rc=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null)
  reason=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}' 2>/dev/null)
  exitcode=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}' 2>/dev/null)
  finished=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.finishedAt}' 2>/dev/null)
  phase=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null)
  ready=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null)

  rc="${rc:-0}"
  [[ "$rc" -gt 0 ]] && restarted_count=$((restarted_count + 1))
  reason="${reason:--}"
  exitcode="${exitcode:--}"
  finished="${finished:--}"
  status="${phase:-?}/ready=${ready:-?}"

  printf "%-24s %-8s %-10s %-9s %-22s %s\n" \
    "$pod" "$rc" "$reason" "$exitcode" "$finished" "$status" >> "$REPORT"
done <<< "$PODS"

{
  echo
  echo "Summary"
  echo "-------"
  echo "Pods with >=1 restart: $restarted_count of $POD_COUNT"
  if [[ "$restarted_count" -eq 0 ]]; then
    echo "Status: ✅ All fluent-bit pods stable, no restarts recorded."
  else
    echo "Status: ⚠️  $restarted_count pod(s) have restarted — see LAST_TERMINATED above."
    echo "Note  : restartCount/lastState only retain the MOST RECENT termination;"
    echo "        earlier restarts are not preserved in pod status."
  fi
  echo
  echo "(Generated by fluentbit-daily-check.sh on $(hostname))"
} >> "$REPORT"

log "Report built. restarted_count=$restarted_count"

# ---- Step 4: Send email ------------------------------------------------------
if [[ "$restarted_count" -eq 0 ]]; then
  subject="[OK] fluent-bit check ($SHOOT): $POD_COUNT pods stable"
else
  subject="[WARN] fluent-bit check ($SHOOT): $restarted_count/$POD_COUNT pod(s) restarted"
fi

send_mail "$subject" "$REPORT"
SEND_RC=$?

log "Done. Email send rc=$SEND_RC"
exit $SEND_RC
