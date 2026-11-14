#!/usr/bin/env bash
# SNSX SPACE master runner.
#
# One command to install, stop old project processes, start PostgreSQL/PostGIS,
# apply migrations, seed the catalog, build the web app, start API + worker + web,
# and verify that the system is healthy.
#
# Usage:
#   ./run.sh                 Full clean start (default)
#   ./run.sh restart         Same as the default
#   ./run.sh stop            Stop API, worker, and web (keeps PostGIS)
#   ./run.sh stop --all      Stop API, worker, web, and PostGIS
#   ./run.sh status          Show process, database, and health status
#   ./run.sh logs [service]  Follow logs: api, worker, web, db, or all
#   ./run.sh install         Install/update Python and Node dependencies
#   ./run.sh doctor          Check prerequisites and current state
#
# Options for start/restart:
#   --skip-install           Do not run uv sync / npm ci
#   --skip-build             Reuse the existing Next.js production build
#   --open                   Open the web app after a successful start

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
ENV_FILE="$ROOT/.env"
RUN_DIR="$ROOT/.run"
PID_DIR="$RUN_DIR/pids"
LOG_DIR="$RUN_DIR/logs"

COMMAND="run"
SERVICE="all"
SKIP_INSTALL=0
SKIP_BUILD=0
OPEN_BROWSER=0
STOP_DB=0

if [[ $# -gt 0 && "$1" != -* ]]; then
  COMMAND="$1"
  shift
fi

if [[ "$COMMAND" == "logs" ]]; then
  SERVICE="${1:-all}"
  shift || true
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install) SKIP_INSTALL=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --open) OPEN_BROWSER=1 ;;
    --all) STOP_DB=1 ;;
    -h|--help) COMMAND="help" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

log()  { printf '\033[1;36m[SNSX]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[SNSX]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[SNSX]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
SNSX SPACE master runner

  ./run.sh [restart] [--skip-install] [--skip-build] [--open]
  ./run.sh start
  ./run.sh stop [--all]
  ./run.sh status
  ./run.sh logs [api|worker|web|db|all]
  ./run.sh install
  ./run.sh doctor

The default command performs a clean start: it installs dependencies, stops old
project processes, starts PostGIS, applies migrations, seeds the catalog,
builds the web app, starts API + worker + web, and verifies health.
EOF
}

read_env() {
  local key="$1" line value
  [[ -f "$ENV_FILE" ]] || return 0
  line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  value="${value%$'\r'}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ROOT/.env.example" ]] || die ".env.example is missing"
    cp "$ROOT/.env.example" "$ENV_FILE"
    log "Created .env from .env.example"
  fi
}

export_runtime_env() {
  ensure_env_file

  if [[ -z "${DATABASE_URL:-}" ]]; then
    DATABASE_URL="$(read_env DATABASE_URL)"
  fi
  export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://terrax:terrax@localhost:5434/terrax}"

  if [[ -z "${NEXT_PUBLIC_API_URL:-}" ]]; then
    NEXT_PUBLIC_API_URL="$(read_env NEXT_PUBLIC_API_URL)"
  fi
  export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"

  if [[ -z "${TERRAX_API_BASE_URL:-}" ]]; then
    TERRAX_API_BASE_URL="$(read_env TERRAX_API_BASE_URL)"
  fi
  export TERRAX_API_BASE_URL="${TERRAX_API_BASE_URL:-http://localhost:8000}"

  local key value
  for key in TERRAX_DATA_DIR TERRAX_CACHE_DIR TERRAX_REPORT_DIR; do
    if [[ -z "${!key:-}" ]]; then
      value="$(read_env "$key")"
      if [[ -n "$value" ]]; then
        export "$key=$value"
      fi
    fi
  done
  return 0
}

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    if command -v brew >/dev/null 2>&1; then
      brew install uv
    else
      curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    export PATH="$HOME/.local/bin:$PATH"
  fi
  command -v uv >/dev/null 2>&1 || die "uv is not available on PATH"
}

ensure_node() {
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    log "Installing Node.js..."
    if command -v brew >/dev/null 2>&1; then
      brew install node
    else
      die "Node.js 20+ and npm are required. Install them from https://nodejs.org/"
    fi
  fi
  local major
  major="$(node -p 'Number(process.versions.node.split(".")[0])')"
  if (( major < 20 )); then
    die "Node.js 20+ is required; found $(node --version)"
  fi
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Desktop..."
    if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
      brew install --cask docker
    else
      die "Docker is required. Install Docker Engine and the Compose plugin."
    fi
  fi

  if ! docker info >/dev/null 2>&1; then
    if [[ "$(uname -s)" == "Darwin" ]] && [[ -d /Applications/Docker.app ]]; then
      log "Starting Docker Desktop..."
      open -a Docker >/dev/null 2>&1 || true
      local i
      for i in $(seq 1 120); do
        docker info >/dev/null 2>&1 && break
        sleep 1
      done
    fi
  fi

  docker info >/dev/null 2>&1 || die "The Docker daemon is not running"
  docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is required"
}

ensure_tools() {
  ensure_uv
  ensure_node
  ensure_docker
  for tool in curl lsof pgrep; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
  done
}

install_deps() {
  log "Installing Python dependencies..."
  if ! ( cd "$API_DIR" && uv sync --frozen --extra dev ); then
    warn "Frozen Python lock did not match; falling back to editable sync."
    ( cd "$API_DIR" && uv sync --extra dev )
  fi

  log "Installing Node dependencies..."
  if ! ( cd "$WEB_DIR" && npm ci --no-audit --no-fund ); then
    warn "npm ci failed; falling back to npm install."
    ( cd "$WEB_DIR" && npm install --no-audit --no-fund )
  fi
}

project_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | awk '/^n/{sub(/^n/,""); print; exit}'
}

is_project_pid() {
  local pid="$1" cwd
  [[ -n "$pid" ]] || return 1
  cwd="$(project_cwd "$pid" || true)"
  [[ -n "$cwd" && ( "$cwd" == "$ROOT" || "$cwd" == "$ROOT"/* ) ]]
}

terminate_pid() {
  local pid="$1" i
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM "$pid" 2>/dev/null || true
  for i in $(seq 1 40); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.25
  done
  warn "Process $pid did not exit after 10s; sending SIGKILL."
  kill -KILL "$pid" 2>/dev/null || true
}

stop_pidfile() {
  local name="$1" file pid
  file="$PID_DIR/$name.pid"
  [[ -f "$file" ]] || return 0
  pid="$(cat "$file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    if is_project_pid "$pid"; then
      log "Stopping $name (PID $pid)..."
      terminate_pid "$pid"
    else
      warn "Ignoring stale $name PID $pid: it belongs to another directory."
    fi
  fi
  rm -f "$file"
}

stop_worker_processes() {
  local pid
  for pid in $(pgrep -f 'terrax\.worker' 2>/dev/null || true); do
    if is_project_pid "$pid"; then
      log "Stopping worker (PID $pid)..."
      terminate_pid "$pid"
    fi
  done
}

stop_npm_processes() {
  local pid
  for pid in $(pgrep -f 'npm run start' 2>/dev/null || true); do
    if is_project_pid "$pid"; then
      log "Stopping npm wrapper (PID $pid)..."
      terminate_pid "$pid"
    fi
  done
}

stop_port() {
  local port="$1" pid cmd
  for pid in $(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    if is_project_pid "$pid"; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      log "Stopping listener on :$port (PID $pid)..."
      terminate_pid "$pid"
    else
      warn "Port $port is held by an unrelated process (PID $pid: ${cmd:-unknown})."
    fi
  done
}

assert_ports_free() {
  local port pid cmd
  for port in 8000 3000; do
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1)"
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      die "Port $port is still in use by PID $pid (${cmd:-unknown}). Stop it and try again."
    fi
  done
}

stop_apps() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
  stop_pidfile api
  stop_pidfile worker
  stop_pidfile web
  stop_worker_processes
  stop_npm_processes
  stop_port 8000
  stop_port 3000
}

db_container() {
  if docker container inspect terrax-db >/dev/null 2>&1; then
    printf 'terrax-db'
    return 0
  fi
  ( cd "$ROOT" && docker compose ps -q db 2>/dev/null | head -n 1 ) || true
}

ensure_db() {
  if docker container inspect terrax-db >/dev/null 2>&1; then
    log "Starting existing PostGIS container terrax-db..."
    docker start terrax-db >/dev/null 2>&1 || true
  else
    log "Starting PostGIS with Docker Compose..."
    ( cd "$ROOT" && docker compose up -d db )
  fi

  local container state i
  container="$(db_container)"
  [[ -n "$container" ]] || die "Could not find or start the PostGIS container"

  for i in $(seq 1 90); do
    state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)"
    if [[ "$state" == "running" ]]; then
      if docker exec "$container" sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
        log "PostGIS is ready ($container)."
        return 0
      fi
    fi
    sleep 1
  done

  docker logs --tail 40 "$container" >&2 || true
  die "PostGIS did not become ready"
}

migrate_and_seed() {
  log "Applying database migrations..."
  ( cd "$API_DIR" && "$API_DIR/.venv/bin/alembic" upgrade head )
  log "Seeding the authoritative catalog (idempotent)..."
  ( cd "$API_DIR" && "$API_DIR/.venv/bin/python" -m terrax.seed )
}

build_web() {
  log "Building the Next.js production app..."
  ( cd "$WEB_DIR" && npm run build )
}

start_api() {
  local pid
  : > "$LOG_DIR/api.log"
  (
    cd "$ROOT"
    export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"
    nohup "$API_DIR/.venv/bin/python" -m uvicorn terrax.main:app \
      --host 0.0.0.0 --port 8000 >> "$LOG_DIR/api.log" 2>&1 &
    echo $! > "$PID_DIR/api.pid"
  )
  pid="$(cat "$PID_DIR/api.pid")"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    tail -n 40 "$LOG_DIR/api.log" >&2 || true
    die "API failed to start"
  fi
  log "API started on :8000 (PID $pid)."
}

start_worker() {
  local pid
  : > "$LOG_DIR/worker.log"
  (
    cd "$ROOT"
    export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"
    nohup "$API_DIR/.venv/bin/python" -m terrax.worker >> "$LOG_DIR/worker.log" 2>&1 &
    echo $! > "$PID_DIR/worker.pid"
  )
  pid="$(cat "$PID_DIR/worker.pid")"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    tail -n 40 "$LOG_DIR/worker.log" >&2 || true
    die "Worker failed to start"
  fi
  log "Worker started (PID $pid)."
}

start_web() {
  local pid
  : > "$LOG_DIR/web.log"
  (
    cd "$WEB_DIR"
    export NEXT_PUBLIC_API_URL="$NEXT_PUBLIC_API_URL"
    export TERRAX_API_BASE_URL="$TERRAX_API_BASE_URL"
    nohup node "$WEB_DIR/node_modules/next/dist/bin/next" start \
      -H 0.0.0.0 -p 3000 >> "$LOG_DIR/web.log" 2>&1 &
    echo $! > "$PID_DIR/web.pid"
  )
  pid="$(cat "$PID_DIR/web.pid")"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    tail -n 40 "$LOG_DIR/web.log" >&2 || true
    die "Web app failed to start"
  fi
  log "Web app started on :3000 (PID $pid)."
}

wait_api() {
  local pid="$1" i body
  for i in $(seq 1 90); do
    kill -0 "$pid" 2>/dev/null || {
      tail -n 60 "$LOG_DIR/api.log" >&2 || true
      return 1
    }
    body="$(curl -fsS --max-time 2 http://127.0.0.1:8000/api/v1/health 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      log "API health check passed."
      return 0
    fi
    sleep 1
  done
  tail -n 60 "$LOG_DIR/api.log" >&2 || true
  return 1
}

wait_web() {
  local pid="$1" i
  for i in $(seq 1 90); do
    kill -0 "$pid" 2>/dev/null || {
      tail -n 60 "$LOG_DIR/web.log" >&2 || true
      return 1
    }
    if curl -fsS --max-time 3 http://127.0.0.1:3000/ >/dev/null 2>&1; then
      log "Web health check passed."
      return 0
    fi
    sleep 1
  done
  tail -n 60 "$LOG_DIR/web.log" >&2 || true
  return 1
}

start_all() {
  mkdir -p "$PID_DIR" "$LOG_DIR"
  start_api
  start_worker
  start_web

  if ! wait_api "$(cat "$PID_DIR/api.pid")"; then
    stop_apps
    die "API did not become healthy"
  fi
  if ! wait_web "$(cat "$PID_DIR/web.pid")"; then
    stop_apps
    die "Web app did not become healthy"
  fi
}

open_browser() {
  (( OPEN_BROWSER )) || return 0
  if [[ "$(uname -s)" == "Darwin" ]]; then
    open http://localhost:3000 >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:3000 >/dev/null 2>&1 || true
  fi
}

cmd_run() {
  ensure_tools
  ensure_env_file
  export_runtime_env

  stop_apps
  assert_ports_free

  if (( ! SKIP_INSTALL )); then
    install_deps
  fi

  [[ -x "$API_DIR/.venv/bin/python" ]] || die "Python environment missing; run ./run.sh install"
  [[ -d "$WEB_DIR/node_modules" ]] || die "Node modules missing; run ./run.sh install"

  ensure_db
  migrate_and_seed

  if (( SKIP_BUILD )) && [[ -f "$WEB_DIR/.next/BUILD_ID" ]]; then
    log "Reusing the existing Next.js production build."
  else
    build_web
  fi

  start_all
  cmd_status

  log "SNSX SPACE is running."
  log "Web:  http://localhost:3000"
  log "API:  http://localhost:8000"
  log "Docs: http://localhost:8000/docs"
  log "Logs: ./run.sh logs"
  open_browser
}

cmd_install() {
  ensure_tools
  ensure_env_file
  export_runtime_env
  install_deps
  log "Dependencies installed."
}

stop_db() {
  local container
  container="$(db_container)"
  if [[ -z "$container" ]]; then
    log "No PostGIS container found."
    return 0
  fi
  if docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null | grep -q '^running$'; then
    log "Stopping PostGIS ($container)..."
    docker stop "$container" >/dev/null 2>&1 || true
  fi
}

cmd_stop() {
  stop_apps
  if (( STOP_DB )); then
    stop_db
  else
    log "PostGIS left running. Use './run.sh stop --all' to stop it too."
  fi
  log "Stopped."
}

cmd_status() {
  local db state health api web worker pid
  db="$(db_container || true)"
  if [[ -n "$db" ]]; then
    state="$(docker inspect -f '{{.State.Status}}' "$db" 2>/dev/null || echo unknown)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$db" 2>/dev/null || echo unknown)"
    printf '%-10s %s (%s, health=%s)\n' "PostGIS" "$db" "$state" "$health"
  else
    printf '%-10s %s\n' "PostGIS" "not found"
  fi

  if [[ -f "$PID_DIR/worker.pid" ]]; then
    pid="$(cat "$PID_DIR/worker.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      printf '%-10s %s (PID %s)\n' "Worker" "running" "$pid"
    else
      printf '%-10s %s\n' "Worker" "stopped"
    fi
  else
    printf '%-10s %s\n' "Worker" "not started"
  fi

  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    api="ok"
  else
    api="down"
  fi
  printf '%-10s %s\n' "API" "$api (http://localhost:8000)"

  if curl -fsS --max-time 3 http://127.0.0.1:3000/ >/dev/null 2>&1; then
    web="ok"
  else
    web="down"
  fi
  printf '%-10s %s\n' "Web" "$web (http://localhost:3000)"
}

cmd_logs() {
  mkdir -p "$LOG_DIR"
  case "$SERVICE" in
    api|worker|web)
      [[ -f "$LOG_DIR/$SERVICE.log" ]] || die "No log for $SERVICE yet"
      exec tail -n 80 -f "$LOG_DIR/$SERVICE.log"
      ;;
    db)
      local container
      container="$(db_container)"
      [[ -n "$container" ]] || die "No PostGIS container found"
      exec docker logs -f "$container"
      ;;
    all)
      touch "$LOG_DIR/api.log" "$LOG_DIR/worker.log" "$LOG_DIR/web.log"
      exec tail -n 40 -f "$LOG_DIR/api.log" "$LOG_DIR/worker.log" "$LOG_DIR/web.log"
      ;;
    *)
      die "Unknown service: $SERVICE (use api, worker, web, db, or all)"
      ;;
  esac
}

cmd_doctor() {
  echo "SNSX SPACE doctor"
  echo "Repository: $ROOT"
  echo "OS:         $(uname -s) $(uname -m)"
  printf 'uv:         '; uv --version 2>/dev/null || echo missing
  printf 'node:       '; node --version 2>/dev/null || echo missing
  printf 'npm:        '; npm --version 2>/dev/null || echo missing
  printf 'docker:     '; docker --version 2>/dev/null || echo missing
  printf 'compose:    '; docker compose version 2>/dev/null || echo missing
  echo "env:        $([[ -f "$ENV_FILE" ]] && echo present || echo missing)"
  echo "api venv:   $([[ -x "$API_DIR/.venv/bin/python" ]] && echo present || echo missing)"
  echo "node_modules: $([[ -d "$WEB_DIR/node_modules" ]] && echo present || echo missing)"
  echo "web build:  $([[ -f "$WEB_DIR/.next/BUILD_ID" ]] && echo present || echo missing)"
  echo
  cmd_status
}

mkdir -p "$PID_DIR" "$LOG_DIR"

case "$COMMAND" in
  run|restart|start|up)
    if [[ "$COMMAND" == "start" ]]; then
      SKIP_INSTALL=1
    fi
    cmd_run
    ;;
  stop)   cmd_stop ;;
  status) cmd_status ;;
  logs)   cmd_logs ;;
  install) cmd_install ;;
  doctor) cmd_doctor ;;
  help|-h|--help) usage ;;
  *) usage; die "Unknown command: $COMMAND" ;;
esac
