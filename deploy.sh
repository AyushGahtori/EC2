#!/usr/bin/env bash
# =============================================================================
# deploy.sh — SnitchX EC2 Agent Deployment Script
# =============================================================================
# Usage:
#   sudo ./deploy.sh
#
# This script:
#   1. Validates the environment (root, required directories)
#   2. Creates Python virtual environments and installs dependencies for all agents
#   3. Installs and enables systemd service units
#   4. Installs and validates the Nginx reverse proxy configuration
#   5. Performs health checks on all agents
#
# Idempotent — safe to run multiple times. Re-running will update packages
# and restart services.
# =============================================================================

set -Eeuo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[deploy]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
die()     { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# ── Root check ─────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo ./deploy.sh)"

# ── Paths ──────────────────────────────────────────────────────────────────────
APP_DIR="/home/ubuntu/app"
SYSTEMD_SRC_DIR="${APP_DIR}/systemd"
NGINX_SRC="${APP_DIR}/nginx/sites-available/agents"
NGINX_DST="/etc/nginx/sites-available/agents"
NGINX_ENABLED="/etc/nginx/sites-enabled/agents"
SECRETS_DIR="/home/ubuntu/app/.secrets"

# ── Agent directory map: <folder-name> : <port> ───────────────────────────────
# Existing agents
TEAMS_DIR="${APP_DIR}/agents/teams-agent"
TODO_DIR="${APP_DIR}/agents/todo-agent"
GOOGLE_DIR="${APP_DIR}/agents/google-agent"
NOTION_DIR="${APP_DIR}/agents/notion-agent"
MAPS_DIR="${APP_DIR}/agents/maps-agent"

# New integration agents
CANVA_DIR="${APP_DIR}/agents/canva-agent"
DAYPLANNER_DIR="${APP_DIR}/agents/day-planner-agent"
DISCORD_DIR="${APP_DIR}/agents/discord-agent"
DROPBOX_DIR="${APP_DIR}/agents/dropbox-agent"
FRESHDESK_DIR="${APP_DIR}/agents/freshdesk-agent"
GITHUB_DIR="${APP_DIR}/agents/github-agent"
GITLAB_DIR="${APP_DIR}/agents/gitlab-agent"
GREENHOUSE_DIR="${APP_DIR}/agents/greenhouse-agent"
JIRA_DIR="${APP_DIR}/agents/jira-agent"
LINKEDIN_DIR="${APP_DIR}/agents/linkedin-agent"
ZOOM_DIR="${APP_DIR}/agents/zoom-agent"

# ── Helper: require directory ──────────────────────────────────────────────────
require_dir() {
    [[ -d "$1" ]] || die "Required directory missing: $1"
}

# ── Helper: setup Python venv + install deps ───────────────────────────────────
setup_python_env() {
    local agent_dir="$1"
    local venv_dir="${agent_dir}/venv"

    info "Setting up Python env for: ${agent_dir}"

    if [[ ! -d "${venv_dir}" ]]; then
        python3 -m venv "${venv_dir}"
    fi

    "${venv_dir}/bin/pip" install --quiet --upgrade pip
    "${venv_dir}/bin/pip" install --quiet -r "${agent_dir}/requirements.txt"
}

# ── Step 1: Validate required directories ─────────────────────────────────────
info "Validating required directories..."
require_dir "${APP_DIR}"
require_dir "${SYSTEMD_SRC_DIR}"
require_dir "$(dirname "${NGINX_SRC}")"

# Existing agents
require_dir "${TEAMS_DIR}"
require_dir "${TODO_DIR}"
require_dir "${GOOGLE_DIR}"
require_dir "${NOTION_DIR}"
require_dir "${MAPS_DIR}"

# New integration agents
require_dir "${CANVA_DIR}"
require_dir "${DAYPLANNER_DIR}"
require_dir "${DISCORD_DIR}"
require_dir "${DROPBOX_DIR}"
require_dir "${FRESHDESK_DIR}"
require_dir "${GITHUB_DIR}"
require_dir "${GITLAB_DIR}"
require_dir "${GREENHOUSE_DIR}"
require_dir "${JIRA_DIR}"
require_dir "${LINKEDIN_DIR}"
require_dir "${ZOOM_DIR}"

# ── Step 2: Secrets directory ──────────────────────────────────────────────────
info "Ensuring secrets directory..."
mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

# ── Step 3: Python virtual environments ───────────────────────────────────────
info "Installing Python dependencies for all agents..."

# Existing agents
setup_python_env "${TEAMS_DIR}"
setup_python_env "${TODO_DIR}"
setup_python_env "${GOOGLE_DIR}"
setup_python_env "${NOTION_DIR}"
setup_python_env "${MAPS_DIR}"

# New integration agents
setup_python_env "${CANVA_DIR}"
setup_python_env "${DAYPLANNER_DIR}"
setup_python_env "${DISCORD_DIR}"
setup_python_env "${DROPBOX_DIR}"
setup_python_env "${FRESHDESK_DIR}"
setup_python_env "${GITHUB_DIR}"
setup_python_env "${GITLAB_DIR}"
setup_python_env "${GREENHOUSE_DIR}"
setup_python_env "${JIRA_DIR}"
setup_python_env "${LINKEDIN_DIR}"
setup_python_env "${ZOOM_DIR}"

# ── Step 4: Install systemd service units ─────────────────────────────────────
info "Installing systemd service units..."

# Existing agents
install -m 0644 "${SYSTEMD_SRC_DIR}/teams-agent.service"       /etc/systemd/system/teams-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/todo-agent.service"        /etc/systemd/system/todo-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/google-agent.service"      /etc/systemd/system/google-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/notion-agent.service"      /etc/systemd/system/notion-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/maps-agent.service"        /etc/systemd/system/maps-agent.service

# New integration agents
install -m 0644 "${SYSTEMD_SRC_DIR}/canva-agent.service"       /etc/systemd/system/canva-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/day-planner-agent.service" /etc/systemd/system/day-planner-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/discord-agent.service"     /etc/systemd/system/discord-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/dropbox-agent.service"     /etc/systemd/system/dropbox-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/freshdesk-agent.service"   /etc/systemd/system/freshdesk-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/github-agent.service"      /etc/systemd/system/github-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/gitlab-agent.service"      /etc/systemd/system/gitlab-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/greenhouse-agent.service"  /etc/systemd/system/greenhouse-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/jira-agent.service"        /etc/systemd/system/jira-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/linkedin-agent.service"    /etc/systemd/system/linkedin-agent.service
install -m 0644 "${SYSTEMD_SRC_DIR}/zoom-agent.service"        /etc/systemd/system/zoom-agent.service

systemctl daemon-reload

# Enable + start/restart all services
info "Enabling and restarting all agent services..."
systemctl enable --now \
    teams-agent todo-agent google-agent notion-agent maps-agent \
    canva-agent day-planner-agent discord-agent dropbox-agent \
    freshdesk-agent github-agent gitlab-agent greenhouse-agent \
    jira-agent linkedin-agent zoom-agent

systemctl restart \
    teams-agent todo-agent google-agent notion-agent maps-agent \
    canva-agent day-planner-agent discord-agent dropbox-agent \
    freshdesk-agent github-agent gitlab-agent greenhouse-agent \
    jira-agent linkedin-agent zoom-agent

# ── Step 5: Nginx configuration ───────────────────────────────────────────────
info "Installing Nginx reverse proxy configuration..."

install -m 0644 "${NGINX_SRC}" "${NGINX_DST}"
ln -sfn "${NGINX_DST}" "${NGINX_ENABLED}"

nginx -t || die "Nginx config test failed — check ${NGINX_DST}"
systemctl reload nginx
info "Nginx reloaded successfully."

# ── Step 6: Health checks ─────────────────────────────────────────────────────
info "Running health checks..."

sleep 3  # Allow services to fully start

check_health() {
    local name="$1"
    local port="$2"
    local path="${3:-/health}"

    if curl -sf --max-time 5 "http://127.0.0.1:${port}${path}" > /dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}  ${name} (127.0.0.1:${port})"
    else
        echo -e "  ${RED}FAIL${NC} ${name} (127.0.0.1:${port}) — check: journalctl -u ${name} -n 30"
    fi
}

# Existing agents
check_health "teams-agent"    8100
check_health "todo-agent"     8200 "/health"
check_health "google-agent"   8300
check_health "notion-agent"   8400
check_health "maps-agent"     8500

# New integration agents
check_health "canva-agent"       8001
check_health "day-planner-agent" 8002
check_health "discord-agent"     8003
check_health "dropbox-agent"     8004
check_health "freshdesk-agent"   8005
check_health "github-agent"      8006
check_health "gitlab-agent"      8007
check_health "greenhouse-agent"  8008
check_health "jira-agent"        8009
check_health "linkedin-agent"    8010
check_health "zoom-agent"        8011

# ── Step 7: Smoke test through Nginx ──────────────────────────────────────────
info "Smoke testing public Nginx routes..."

check_nginx() {
    local route="$1"
    if curl -sf --max-time 5 "http://13.206.83.175${route}" > /dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}  http://13.206.83.175${route}"
    else
        echo -e "  ${YELLOW}WARN${NC} http://13.206.83.175${route} — agent may need env vars configured"
    fi
}

check_nginx "/health"
check_nginx "/todo/health"
check_nginx "/google/health"
check_nginx "/notion/health"
check_nginx "/maps/health"
check_nginx "/canva/health"
check_nginx "/dayplanner/health"
check_nginx "/discord/health"
check_nginx "/dropbox/health"
check_nginx "/freshdesk/health"
check_nginx "/github/health"
check_nginx "/gitlab/health"
check_nginx "/greenhouse/health"
check_nginx "/jira/health"
check_nginx "/linkedin/health"
check_nginx "/zoom/health"

info "================================================================"
info "Deployment complete."
info "All 16 agent services are configured and started."
info ""
info "Next steps:"
info "  1. Copy your serviceAccountKey.json to ${SECRETS_DIR}/serviceAccountKey.json"
info "  2. Fill in .env files under each agent directory with OAuth keys"
info "  3. Run 'sudo systemctl restart <agent-name>' after updating .env"
info "================================================================"
