# ai_agent shell auto-start helper.
#
# Source this file from ~/.zshrc.  It uses a PID file to detect this specific
# service (instead of treating any listener as ai_agent), chooses the first free
# port in a configurable range, and serializes concurrent terminal startups.

_ai_agent_runtime_dir="${TMPDIR:-/tmp}/ai-agent-${UID}"
_ai_agent_pid_file="${_ai_agent_runtime_dir}/server.pid"
_ai_agent_port_file="${_ai_agent_runtime_dir}/server.port"
_ai_agent_log_file="${_ai_agent_runtime_dir}/server.log"
_ai_agent_lock_dir="${_ai_agent_runtime_dir}/startup.lock"

_ai_agent_running() {
  local pid
  [[ -r "$_ai_agent_pid_file" ]] || return 1
  pid="$(<"$_ai_agent_pid_file")"
  [[ "$pid" == <-> ]] && kill -0 "$pid" >/dev/null 2>&1
}

_ai_agent_autostart() {
  local dir="/Users/I577081/Workdir/Github/veloxityX-ai-journey/ai_agent"
  local first_port="${AI_AGENT_PORT:-18080}"
  local last_port="$first_port"
  local port pid
  local executable="${dir}/.venv/bin/agent"

  [[ -d "$dir" ]] || return 0
  [[ -x "$executable" ]] || return 0
  mkdir -p "$_ai_agent_runtime_dir" || return 0

  # This identifies our process, not an unrelated program that happens to use
  # the same port.
  if _ai_agent_running; then
    return 0
  fi

  rm -f "$_ai_agent_pid_file" "$_ai_agent_port_file"

  # Opening several terminals at once must not launch several copies.
  if ! mkdir "$_ai_agent_lock_dir" 2>/dev/null; then
    return 0
  fi

  # Recheck after acquiring the lock.
  if _ai_agent_running; then
    rmdir "$_ai_agent_lock_dir" 2>/dev/null
    return 0
  fi

  port="$first_port"
  while (( port <= last_port )); do
    if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      (
        cd "$dir" || exit 1
        nohup "$executable" serve --port "$port" \
          >>"$_ai_agent_log_file" 2>&1 &
        pid=$!
        print -r -- "$pid" >"$_ai_agent_pid_file"
        print -r -- "$port" >"$_ai_agent_port_file"
      )
      rmdir "$_ai_agent_lock_dir" 2>/dev/null
      return 0
    fi
    (( port++ ))
  done

  print -u2 -- "ai_agent: no free port in ${first_port}-${last_port}; not started"
  rmdir "$_ai_agent_lock_dir" 2>/dev/null
  return 1
}

ai-agent-url() {
  local port
  if _ai_agent_running && [[ -r "$_ai_agent_port_file" ]]; then
    port="$(<"$_ai_agent_port_file")"
    print -r -- "http://127.0.0.1:${port}"
  else
    print -u2 -- "ai_agent is not running; see $_ai_agent_log_file"
    return 1
  fi
}

ai-agent-stop() {
  local pid
  if _ai_agent_running; then
    pid="$(<"$_ai_agent_pid_file")"
    kill "$pid" >/dev/null 2>&1
  fi
  rm -f "$_ai_agent_pid_file" "$_ai_agent_port_file"
}

_ai_agent_autostart
