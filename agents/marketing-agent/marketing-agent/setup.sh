#!/usr/bin/env bash
# ============================================================
# Marketing AI Agent — One-Command Setup Script
# ============================================================
set -e

BOLD=$(tput bold 2>/dev/null || echo "")
RESET=$(tput sgr0 2>/dev/null || echo "")
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "${BOLD}${CYAN}╔══════════════════════════════════════════╗${NC}"
echo "${BOLD}${CYAN}║   MAIA — Marketing AI Agent Setup        ║${NC}"
echo "${BOLD}${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Prerequisites Check ────────────────────────────────────────────────────────
echo "${YELLOW}▶ Checking prerequisites...${NC}"

check_cmd() {
  if ! command -v "$1" &> /dev/null; then
    echo "${RED}✗ $1 not found. Please install $1 first.${NC}"
    exit 1
  fi
  echo "${GREEN}✓ $1 found${NC}"
}

check_cmd python3
check_cmd node
check_cmd npm
check_cmd redis-cli
check_cmd mongod || echo "${YELLOW}  ⚠ mongod not in PATH — ensure MongoDB is running${NC}"

# ── Check services ─────────────────────────────────────────────────────────────
echo ""
echo "${YELLOW}▶ Checking services...${NC}"

if redis-cli ping &>/dev/null; then
  echo "${GREEN}✓ Redis is running${NC}"
else
  echo "${RED}✗ Redis is not running. Start it with: redis-server${NC}"
  echo "  Continuing anyway — please start Redis before running the app."
fi

# ── Backend Setup ──────────────────────────────────────────────────────────────
echo ""
echo "${YELLOW}▶ Setting up Python backend...${NC}"
cd backend

if [ ! -d "venv" ]; then
  echo "  Creating virtual environment..."
  python3 -m venv venv
fi

echo "  Activating virtual environment..."
source venv/bin/activate

echo "  Installing Python dependencies..."
pip install -r requirements.txt --quiet

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "${GREEN}  ✓ Created backend/.env from .env.example${NC}"
  echo "${YELLOW}  ⚠  Edit backend/.env to configure your LLM provider${NC}"
else
  echo "${GREEN}  ✓ backend/.env already exists${NC}"
fi

mkdir -p uploads
echo "${GREEN}  ✓ Upload directory created${NC}"

cd ..

# ── Frontend Setup ─────────────────────────────────────────────────────────────
echo ""
echo "${YELLOW}▶ Setting up Next.js frontend...${NC}"
cd frontend

echo "  Installing Node.js dependencies..."
npm install --silent

if [ ! -f ".env.local" ]; then
  cp .env.local.example .env.local
  echo "${GREEN}  ✓ Created frontend/.env.local${NC}"
else
  echo "${GREEN}  ✓ frontend/.env.local already exists${NC}"
fi

cd ..

# ── Ollama Models ──────────────────────────────────────────────────────────────
echo ""
echo "${YELLOW}▶ Ollama model setup...${NC}"

if command -v ollama &> /dev/null; then
  echo "${CYAN}  Pulling recommended models (this may take a while)...${NC}"
  echo "  • gemma3:12b  (text model — ~7GB)"
  echo "  • llava:13b   (vision model — ~8GB)"
  echo ""
  read -p "  Pull models now? [y/N] " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    ollama pull gemma3:12b
    ollama pull llava:13b
    echo "${GREEN}  ✓ Models pulled${NC}"
    echo ""
    echo "${YELLOW}  Note: Update OLLAMA_MODEL in backend/.env to 'gemma3:12b'${NC}"
    echo "${YELLOW}        and OLLAMA_VISION_MODEL to 'llava:13b'${NC}"
  else
    echo "${YELLOW}  Skipping model pull. Pull manually:${NC}"
    echo "    ollama pull gemma3:12b"
    echo "    ollama pull llava:13b"
  fi
else
  echo "${YELLOW}  ⚠ Ollama not found. Install from https://ollama.ai${NC}"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo "${BOLD}${GREEN}║   ✅ Setup Complete!                              ║${NC}"
echo "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo "${CYAN}To start the application:${NC}"
echo ""
echo "  ${BOLD}Terminal 1 — Backend:${NC}"
echo "    cd backend && source venv/bin/activate"
echo "    uvicorn main:app --reload --port 8000"
echo ""
echo "  ${BOLD}Terminal 2 — Frontend:${NC}"
echo "    cd frontend && npm run dev"
echo ""
echo "  ${BOLD}Then open:${NC} http://localhost:3000"
echo ""
echo "${YELLOW}Configure your LLM provider in: backend/.env${NC}"
echo "  Default: Ollama (gemma4:27b)"
echo "  Switch:  Set LLM_PROVIDER=anthropic|openai|groq"
echo ""
