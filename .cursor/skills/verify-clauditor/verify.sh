#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SKILL_DIR/../../.." && pwd)"
VERIFY_HOME="${CLAUDITOR_VERIFY_HOME:-$HOME/.cache/clauditor-verify}"
PROJECT_DIR_NAME="-Users-dev-code-acme-web"

usage() {
  cat <<EOF
usage: RUN=<id> $0 <command>

  setup                 create disposable projects root, tokens, and evidence dir for RUN
  serve                 start an isolated clauditor server for RUN on a free port
  doctor                read-only health check of RUN
  env                   print the variables capture exports, for reading
  rpc <tool> [k=v ...]  print MCP initialize, tools/list and tools/call lines to pipe into clauditor mcp
  capture <name> <cmd>  run <cmd> with bash in the repo and save cmd/stdout/stderr/exit to evidence
  stop                  stop RUN's server, keep scratch and evidence
  cleanup               stop RUN's server, delete scratch, keep evidence
  list                  list runs under $VERIFY_HOME

RUN ids: letters, digits, dot, dash, underscore. Home: \$CLAUDITOR_VERIFY_HOME (default ~/.cache/clauditor-verify).
EOF
  exit 2
}

need_run() {
  [[ "${RUN:-}" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "set RUN=<id> (letters, digits, . _ -)" >&2; exit 2; }
  RUN_DIR="$VERIFY_HOME/$RUN"
  SCRATCH="$RUN_DIR/scratch"
  EVIDENCE="$RUN_DIR/evidence"
}

need_setup() {
  [[ -f "$SCRATCH/run.env" ]] || { echo "run $RUN is not set up (or was cleaned up): RUN=$RUN $0 setup" >&2; exit 1; }
  source "$SCRATCH/run.env"
}

server_pid() {
  [[ -f "$SCRATCH/server.pid" ]] && cat "$SCRATCH/server.pid" || true
}

owns_pid() {
  local pid="$1"
  [[ -n "$pid" ]] && ps -p "$pid" -o command= 2>/dev/null | grep -qF -- "--db $SCRATCH/clauditor.db"
}

cmd_setup() {
  [[ -e "$SCRATCH" ]] && { echo "run $RUN already exists at $RUN_DIR; pick a new RUN or cleanup first" >&2; exit 1; }
  mkdir -p "$SCRATCH/projects/$PROJECT_DIR_NAME" "$EVIDENCE"
  cp "$REPO/fixtures/coding_session.jsonl" "$SCRATCH/projects/$PROJECT_DIR_NAME/"
  cp -R "$REPO/fixtures/coding_session" "$SCRATCH/projects/$PROJECT_DIR_NAME/"
  cp "$SKILL_DIR/seed/drift_session.jsonl" "$SCRATCH/projects/$PROJECT_DIR_NAME/"
  local ingest read
  ingest="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  read="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$SCRATCH/run.env" <<EOF
V_RUN=$RUN
V_HELPER=$SKILL_DIR/verify.sh
V_ROOT=$SCRATCH/projects
V_STATE=$SCRATCH/collector-state.json
V_DB=$SCRATCH/clauditor.db
V_EVIDENCE=$EVIDENCE
V_INGEST_TOKEN=$ingest
V_READ_TOKEN=$read
EOF
  echo "run $RUN ready"
  echo "  projects root  $SCRATCH/projects (2 sessions: coding_session, drift_session)"
  echo "  evidence       $EVIDENCE"
}

cmd_serve() {
  need_setup
  owns_pid "$(server_pid)" && { echo "server for $RUN already running (pid $(server_pid))" >&2; exit 1; }
  : > "$SCRATCH/server.log"
  (
    cd "$REPO"
    unset CLAUDITOR_SERVER
    CLAUDITOR_INGEST_TOKEN="$V_INGEST_TOKEN" CLAUDITOR_READ_TOKEN="$V_READ_TOKEN" \
      nohup python3 -m clauditor serve --host 127.0.0.1 --port 0 --db "$V_DB" >>"$SCRATCH/server.log" 2>&1 &
    echo $! > "$SCRATCH/server.pid"
  )
  local port=""
  for _ in $(seq 1 50); do
    port="$(sed -nE 's#^clauditor serving on http://127\.0\.0\.1:([0-9]+) .*#\1#p' "$SCRATCH/server.log")"
    [[ -n "$port" ]] && break
    owns_pid "$(server_pid)" || break
    sleep 0.2
  done
  if [[ -z "$port" ]]; then
    echo "server did not become ready; log:" >&2
    cat "$SCRATCH/server.log" >&2
    exit 1
  fi
  echo "V_URL=http://127.0.0.1:$port" >> "$SCRATCH/run.env"
  echo "server for $RUN ready at http://127.0.0.1:$port (pid $(server_pid), db $V_DB)"
}

cmd_doctor() {
  local failed=0
  check() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1"; failed=1; fi; }
  check "python3 >= 3.12 ($(python3 -c 'import sys; print(sys.version.split()[0])'))" \
    'python3 -c "import sys; sys.exit(sys.version_info < (3, 12))"'
  echo "info  repo $REPO at $(git -C "$REPO" rev-parse --short HEAD) branch $(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  if [[ ! -f "$SCRATCH/run.env" ]]; then
    echo "FAIL  run $RUN not set up at $RUN_DIR"
    exit 1
  fi
  source "$SCRATCH/run.env"
  check "projects root has 2 transcripts" '[[ $(ls "$V_ROOT"/*/*.jsonl 2>/dev/null | wc -l | tr -d " ") == 2 ]]'
  check "evidence dir exists ($V_EVIDENCE)" '[[ -d "$V_EVIDENCE" ]]'
  if [[ -z "${V_URL:-}" ]]; then
    echo "info  no server started for this run (CLI-only)"
    exit $failed
  fi
  local pid port
  pid="$(server_pid)"
  port="${V_URL##*:}"
  check "server pid $pid alive and serving this run's db" 'owns_pid "$pid"'
  check "port $port is held by pid $pid" '[[ "$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null)" == "$pid" ]]'
  check "GET /healthz returns ok" '[[ "$(curl -s --max-time 3 "$V_URL/healthz")" == ok ]]'
  check "GET /v1/roles without token is 401" '[[ "$(curl -s -o /dev/null -w %{http_code} --max-time 3 "$V_URL/v1/roles")" == 401 ]]'
  check "GET /v1/roles with ingest token is 401" '[[ "$(curl -s -o /dev/null -w %{http_code} --max-time 3 -H "authorization: Bearer $V_INGEST_TOKEN" "$V_URL/v1/roles")" == 401 ]]'
  check "GET /v1/roles with read token is 200" '[[ "$(curl -s -o /dev/null -w %{http_code} --max-time 3 -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/roles")" == 200 ]]'
  exit $failed
}

cmd_env() {
  need_setup
  cat "$SCRATCH/run.env"
}

cmd_capture() {
  [[ $# -eq 2 ]] || usage
  local name="$1" command="$2"
  [[ "$name" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "capture name must be a path-safe slug" >&2; exit 2; }
  need_setup
  mkdir -p "$(dirname "$EVIDENCE/$name")"
  local code=0
  {
    echo "\$ $command"
    echo
    echo "# run $RUN at $(date -u +%Y-%m-%dT%H:%M:%SZ), repo $(git -C "$REPO" rev-parse --short HEAD)"
    sed 's/TOKEN=.*/TOKEN=<redacted>/' "$SCRATCH/run.env" | sed 's/^/# /'
  } > "$EVIDENCE/$name.cmd"
  (
    cd "$REPO"
    unset CLAUDITOR_SERVER CLAUDITOR_INGEST_TOKEN CLAUDITOR_READ_TOKEN
    set -a
    source "$SCRATCH/run.env"
    set +a
    export CLAUDITOR_INGEST_TOKEN="$V_INGEST_TOKEN" CLAUDITOR_READ_TOKEN="$V_READ_TOKEN"
    bash -c "$command"
  ) >"$EVIDENCE/$name.out" 2>"$EVIDENCE/$name.err" || code=$?
  echo "$code" > "$EVIDENCE/$name.exit"
  echo "== $name: exit $code (evidence $EVIDENCE/$name.{cmd,out,err,exit})"
  echo "-- stdout"
  head -c 6000 "$EVIDENCE/$name.out"
  if [[ -s "$EVIDENCE/$name.err" ]]; then
    echo "-- stderr"
    head -c 2000 "$EVIDENCE/$name.err"
  fi
  return "$code"
}

cmd_rpc() {
  [[ $# -ge 1 ]] || usage
  python3 - "$@" <<'EOF'
import json, sys
tool, pairs = sys.argv[1], sys.argv[2:]
def value(raw):
    return {"true": True, "false": False}.get(raw, raw)
arguments = {k: value(v) for k, _, v in (p.partition("=") for p in pairs)}
messages = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
]
print("\n".join(json.dumps(m) for m in messages))
EOF
}

cmd_stop() {
  local pid
  pid="$(server_pid)"
  if ! owns_pid "$pid"; then
    echo "no server of run $RUN is running"
    return 0
  fi
  kill "$pid"
  for _ in $(seq 1 25); do
    owns_pid "$pid" || { echo "stopped server pid $pid"; return 0; }
    sleep 0.2
  done
  kill -9 "$pid"
  echo "killed server pid $pid"
}

cmd_cleanup() {
  [[ -d "$RUN_DIR" ]] || { echo "no run $RUN at $RUN_DIR"; return 0; }
  cmd_stop
  mkdir -p "$EVIDENCE"
  [[ -f "$SCRATCH/server.log" ]] && cp "$SCRATCH/server.log" "$EVIDENCE/server.log"
  rm -rf "$SCRATCH"
  echo "removed $SCRATCH"
  echo "evidence kept at $EVIDENCE:"
  ls -1 "$EVIDENCE" | sed 's/^/  /'
}

cmd_list() {
  [[ -d "$VERIFY_HOME" ]] || { echo "no runs under $VERIFY_HOME"; return 0; }
  for dir in "$VERIFY_HOME"/*/; do
    RUN="$(basename "$dir")"
    need_run
    local state="cleaned"
    if [[ -d "$SCRATCH" ]]; then
      state="set up"
      owns_pid "$(server_pid)" && state="serving (pid $(server_pid))"
    fi
    echo "$RUN  $state  evidence $(ls -1 "$EVIDENCE" 2>/dev/null | wc -l | tr -d ' ') files"
  done
}

[[ $# -ge 1 ]] || usage
command="$1"
shift
case "$command" in
  list) cmd_list ;;
  rpc) cmd_rpc "$@" ;;
  setup) need_run; cmd_setup ;;
  serve) need_run; cmd_serve ;;
  doctor) need_run; cmd_doctor ;;
  env) need_run; cmd_env ;;
  capture) need_run; cmd_capture "$@" ;;
  stop) need_run; cmd_stop ;;
  cleanup) need_run; cmd_cleanup ;;
  *) usage ;;
esac
