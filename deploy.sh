#!/usr/bin/env bash
# =============================================================================
# deploy.sh - SnitchX EC2 Agent Deployment Script
# =============================================================================

set -Eeuo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
die() { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo ./deploy.sh)"

APP_DIR="/home/ubuntu/app"
SYSTEMD_SRC_DIR="${APP_DIR}/systemd"
NGINX_SRC="${APP_DIR}/nginx/sites-available/agents"
NGINX_DST="/etc/nginx/sites-available/agents"
NGINX_ENABLED="/etc/nginx/sites-enabled/agents"
SECRETS_DIR="${APP_DIR}/.secrets"
PUBLIC_BASE_URL="${AGENT_PUBLIC_BASE_URL:-http://13.206.83.175}"

AGENT_DIRS=(
    "teams-agent"
    "todo-agent"
    "google-agent"
    "notion-agent"
    "maps-agent"
    "canva-agent"
    "day-planner-agent"
    "discord-agent"
    "dropbox-agent"
    "freshdesk-agent"
    "github-agent"
    "gitlab-agent"
    "greenhouse-agent"
    "jira-agent"
    "linkedin-agent"
    "zoom-agent"
)

AUTH_SLUGS=(
    "teams"
    "google"
    "notion"
    "canva"
    "discord"
    "dropbox"
    "github"
    "gitlab"
    "jira"
    "linkedin"
    "zoom"
)

require_dir() {
    [[ -d "$1" ]] || die "Required directory missing: $1"
}

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

install_service() {
    local service_name="$1"
    install -m 0644 "${SYSTEMD_SRC_DIR}/${service_name}.service" "/etc/systemd/system/${service_name}.service"
}

check_health() {
    local name="$1"
    local port="$2"
    local path="${3:-/health}"

    if curl -sf --max-time 5 "http://127.0.0.1:${port}${path}" > /dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}  ${name} (127.0.0.1:${port}${path})"
    else
        echo -e "  ${RED}FAIL${NC} ${name} (127.0.0.1:${port}${path}) - check: journalctl -u ${name} -n 30"
    fi
}

check_nginx() {
    local route="$1"
    if curl -sf --max-time 5 "${PUBLIC_BASE_URL}${route}" > /dev/null 2>&1; then
        echo -e "  ${GREEN}OK${NC}  ${PUBLIC_BASE_URL}${route}"
    else
        echo -e "  ${YELLOW}WARN${NC} ${PUBLIC_BASE_URL}${route} - agent may need env vars configured"
    fi
}

check_auth_route() {
    local route="$1"
    local status
    status="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${PUBLIC_BASE_URL}${route}" || true)"
    case "$status" in
        200|302|400|401)
            echo -e "  ${GREEN}OK${NC}  ${PUBLIC_BASE_URL}${route} (HTTP ${status})"
            ;;
        *)
            echo -e "  ${YELLOW}WARN${NC} ${PUBLIC_BASE_URL}${route} (HTTP ${status:-unknown})"
            ;;
    esac
}

info "Validating required directories..."
require_dir "${APP_DIR}"
require_dir "${SYSTEMD_SRC_DIR}"
require_dir "$(dirname "${NGINX_SRC}")"

for agent_name in "${AGENT_DIRS[@]}"; do
    require_dir "${APP_DIR}/agents/${agent_name}"
done

info "Ensuring secrets directory..."
mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

info "Installing Python dependencies for all agents..."
for agent_name in "${AGENT_DIRS[@]}"; do
    setup_python_env "${APP_DIR}/agents/${agent_name}"
done

info "Installing systemd service units..."
for agent_name in "${AGENT_DIRS[@]}"; do
    install_service "${agent_name}"
done

systemctl daemon-reload

info "Enabling and restarting all agent services..."
systemctl enable --now "${AGENT_DIRS[@]}"
systemctl restart "${AGENT_DIRS[@]}"

info "Installing Nginx reverse proxy configuration..."
install -m 0644 "${NGINX_SRC}" "${NGINX_DST}"
ln -sfn "${NGINX_DST}" "${NGINX_ENABLED}"

nginx -t || die "Nginx config test failed - check ${NGINX_DST}"
systemctl reload nginx
info "Nginx reloaded successfully."

info "Running health checks..."
sleep 3

check_health "teams-agent" 8100
check_health "todo-agent" 8200 "/health"
check_health "google-agent" 8300
check_health "notion-agent" 8400
check_health "maps-agent" 8500
check_health "canva-agent" 8001
check_health "day-planner-agent" 8002
check_health "discord-agent" 8003
check_health "dropbox-agent" 8004
check_health "freshdesk-agent" 8005
check_health "github-agent" 8006
check_health "gitlab-agent" 8007
check_health "greenhouse-agent" 8008
check_health "jira-agent" 8009
check_health "linkedin-agent" 8010
check_health "zoom-agent" 8011

info "Smoke testing public Nginx routes..."
check_nginx "/health"
check_nginx "/teams/health"
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

info "Smoke testing public OAuth routes..."
for slug in "${AUTH_SLUGS[@]}"; do
    check_auth_route "/${slug}/auth/login"
done

info "================================================================"
info "Deployment complete."
info "All 16 agent services are configured and started."
info ""
info "Next steps:"
info "  1. Copy your serviceAccountKey.json to ${SECRETS_DIR}/serviceAccountKey.json"
info "  2. Fill in each agent .env with provider keys, AGENT_OAUTH_SHARED_SECRET, and AGENT_PUBLIC_BASE_URL"
info "  3. Ensure OAuth redirect URIs point to ${PUBLIC_BASE_URL}/<slug>/auth/callback"
info "  4. Run 'sudo systemctl restart <agent-name>' after updating .env"
info "================================================================"
