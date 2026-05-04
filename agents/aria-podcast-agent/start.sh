#!/usr/bin/env bash
# ═══════════════════════════════════════════
# ARIA Podcast Agent — Start Script
# Usage: ./start.sh [backend|frontend|all]
# ═══════════════════════════════════════════

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${CYAN}[ARIA]${NC} $1"; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC} $1"; }

COMMAND="${1:-all}"

# ── Check prerequisites ──────────────────────
check_prereqs() {
  log "Checking prerequisites…"

  command -v python3 >/dev/null 2>&1 || { err "Python 3 not found"; exit 1; }
  command -v node    >/dev/null 2>&1 || { err "Node.js not found"; exit 1; }
  command -v npm     >/dev/null 2>&1 || { err "npm not found"; exit 1; }

  # Redis
  if ! redis-cli ping >/dev/null 2>&1; then
    warn "Redis not responding — trying to start…"
    if command -v brew >/dev/null 2>&1; then
      brew services start redis >/dev/null 2>&1 || true
    elif command -v systemctl >/dev/null 2>&1; then
      sudo systemctl start redis-server >/dev/null 2>&1 || true
    fi
    sleep 1
    redis-cli ping >/dev/null 2>&1 && ok "Redis started" || warn "Redis still not running (memory features may fail)"
  else
    ok "Redis OK"
  fi

  # MongoDB
  if ! mongosh --eval "db.runCommand({ping:1})" --quiet >/dev/null 2>&1; then
    warn "MongoDB not responding — trying to start…"
    if command -v brew >/dev/null 2>&1; then
      brew services start mongodb-community >/dev/null 2>&1 || true
    elif command -v systemctl >/dev/null 2>&1; then
      sudo systemctl start mongod >/dev/null 2>&1 || true
    fi
    sleep 1
    mongosh --eval "db.runCommand({ping:1})" --quiet >/dev/null 2>&1 && ok "MongoDB started" || warn "MongoDB still not running (persistence may fail)"
  else
    ok "MongoDB OK"
  fi
}

# ── Backend ──────────────────────────────────
start_backend() {
  log "Starting backend…"
  cd "$(dirname "$0")/backend"

  # Create venv if needed
  if [ ! -d "venv" ]; then
    log "Creating Python virtual environment…"
    python3 -m venv venv
  fi

  source venv/bin/activate

  # Install deps if needed
  log "Checking Python dependencies…"
  pip install -r requirements.txt -q --disable-pip-version-check

  # Copy .env if not exists
  if [ ! -f ".env" ]; then
    warn ".env not found — copying from .env.example"
    cp .env.example .env
    warn "Please edit backend/.env with your API keys!"
  fi

  ok "Backend starting at http://localhost:8000"
  ok "API docs at  http://localhost:8000/docs"
  python -m app.main
}

# ── Frontend ─────────────────────────────────
start_frontend() {
  log "Starting frontend…"
  cd "$(dirname "$0")/frontend"

  # Install deps if needed
  if [ ! -d "node_modules" ]; then
    log "Installing npm packages (first run — may take a minute)…"
    npm install
  fi

  # Copy .env.local if not exists
  if [ ! -f ".env.local" ]; then
    cp .env.local.example .env.local
  fi

  ok "Frontend starting at http://localhost:3000"
  npm run dev
}

# ── Main ─────────────────────────────────────
echo ""
echo "  🎙️  ARIA Podcast Agent"
echo "  ─────────────────────────────────"
echo ""

case "$COMMAND" in
  backend)
    check_prereqs
    start_backend
    ;;
  frontend)
    start_frontend
    ;;
  all)
    check_prereqs
    # Start backend in background
    (start_backend) &
    BACKEND_PID=$!
    sleep 4
    # Start frontend in foreground
    start_frontend
    # Cleanup
    kill $BACKEND_PID 2>/dev/null || true
    ;;
  *)
    echo "Usage: $0 [backend|frontend|all]"
    exit 1
    ;;
esac
