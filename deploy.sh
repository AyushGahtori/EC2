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
maybe_die() {
    if [[ "${STRICT_ENV_VALIDATION}" == "1" ]]; then
        die "$@"
    fi
    warn "$@"
}

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo ./deploy.sh)"

APP_DIR="/home/ubuntu/app"
SYSTEMD_SRC_DIR="${APP_DIR}/systemd"
NGINX_SRC="${APP_DIR}/nginx/sites-available/agents"
NGINX_DST="/etc/nginx/sites-available/agents"
NGINX_ENABLED="/etc/nginx/sites-enabled/agents"
SECRETS_DIR="${APP_DIR}/.secrets"
ENV_PUBLIC_BASE_URL="$(awk -F= '/^AGENT_PUBLIC_BASE_URL=/{print $2; exit}' /etc/environment 2>/dev/null || true)"
ENV_PUBLIC_BASE_URL="${ENV_PUBLIC_BASE_URL%\"}"
ENV_PUBLIC_BASE_URL="${ENV_PUBLIC_BASE_URL#\"}"
ENV_SHARED_SECRET="$(awk -F= '/^AGENT_OAUTH_SHARED_SECRET=/{print $2; exit}' /etc/environment 2>/dev/null || true)"
ENV_SHARED_SECRET="${ENV_SHARED_SECRET%\"}"
ENV_SHARED_SECRET="${ENV_SHARED_SECRET#\"}"
PUBLIC_BASE_URL="${AGENT_PUBLIC_BASE_URL:-${ENV_PUBLIC_BASE_URL:-}}"
SHARED_SECRET="${AGENT_OAUTH_SHARED_SECRET:-${ENV_SHARED_SECRET:-}}"
STRICT_ENV_VALIDATION="${STRICT_ENV_VALIDATION:-1}"

AGENT_DIRS=(
    "teams-agent"
    "todo-agent"
    "google-agent"
    "notion-agent"
    "maps-agent"
    "emergency-response-agent"
    "strata-agent"
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
    "dia-helper-agent"
    "shopgenie-agent"
    "career-switch-agent"
    "dashboard-designer-agent"
    "smart-gtm-agent"
    "seo-agent"
    "startup-fundraising-agent"
    "ats-agent"
    "building-construction-agent"
    "lms-agent"
    "travel-halper-agent"
    "restaurant-concierge-agent"
    "devika-engineer-agent"
    "data-analyst-agent"
    "cyber-soc-agent"
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

OAUTH_AGENT_DIRS=(
    "teams-agent"
    "google-agent"
    "notion-agent"
    "canva-agent"
    "discord-agent"
    "dropbox-agent"
    "github-agent"
    "gitlab-agent"
    "jira-agent"
    "linkedin-agent"
    "zoom-agent"
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

read_env_value() {
    local env_file="$1"
    local key="$2"
    awk -F= -v key="${key}" '$1 == key { print substr($0, index($0,$2)); exit }' "${env_file}" 2>/dev/null || true
}

ensure_env_value() {
    local env_file="$1"
    local key="$2"
    local value
    value="$(read_env_value "${env_file}" "${key}")"
    if [[ -z "${value// }" ]]; then
        maybe_die "Missing ${key} in ${env_file}"
        return 1
    fi
    return 0
}

check_health() {
    local name="$1"
    local port="$2"
    local path="${3:-/health}"
    local url="http://127.0.0.1:${port}${path}"
    local attempts=20
    local sleep_seconds=1
    local i

    for ((i=1; i<=attempts; i++)); do
        if curl -sf --max-time 5 "${url}" > /dev/null 2>&1; then
            echo -e "  ${GREEN}OK${NC}  ${name} (127.0.0.1:${port}${path})"
            return 0
        fi
        sleep "${sleep_seconds}"
    done

    echo -e "  ${RED}FAIL${NC} ${name} (127.0.0.1:${port}${path}) - check: journalctl -u ${name} -n 30"
    return 1
}

check_nginx() {
    local route="$1"
    local url="${PUBLIC_BASE_URL}${route}"
    local attempts=5
    local sleep_seconds=1
    local i

    for ((i=1; i<=attempts; i++)); do
        if curl -sf --max-time 5 "${url}" > /dev/null 2>&1; then
            echo -e "  ${GREEN}OK${NC}  ${url}"
            return 0
        fi
        sleep "${sleep_seconds}"
    done

    echo -e "  ${YELLOW}WARN${NC} ${url} - agent may need env vars configured"
    return 1
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

if [[ -z "${PUBLIC_BASE_URL}" ]]; then
    maybe_die "AGENT_PUBLIC_BASE_URL is required (env or /etc/environment)."
fi

if [[ -z "${SHARED_SECRET}" ]]; then
    maybe_die "AGENT_OAUTH_SHARED_SECRET is required (env or /etc/environment)."
fi

if [[ -z "${WEB_BASE_URL:-}" && -z "${NEXT_PUBLIC_APP_URL:-}" ]]; then
    warn "WEB_BASE_URL/NEXT_PUBLIC_APP_URL are not set in the shell environment."
    warn "Set one of them (or OAUTH_ALLOWED_RETURN_ORIGINS per-agent) so OAuth popups can postMessage back to the web app origin."
fi

if [[ -n "${PUBLIC_BASE_URL}" && "${PUBLIC_BASE_URL}" != https://* ]]; then
    warn "AGENT_PUBLIC_BASE_URL is not HTTPS (${PUBLIC_BASE_URL}). Configure TLS before production traffic."
fi

for oauth_agent in "${OAUTH_AGENT_DIRS[@]}"; do
    env_file="${APP_DIR}/agents/${oauth_agent}/.env"
    if [[ ! -f "${env_file}" ]]; then
        maybe_die "Missing ${env_file} for OAuth-capable agent ${oauth_agent}."
        continue
    fi

    ensure_env_value "${env_file}" "AGENT_PUBLIC_BASE_URL" || true
    ensure_env_value "${env_file}" "AGENT_OAUTH_SHARED_SECRET" || true
done

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
check_health "emergency-response-agent" 8510
check_health "strata-agent" 8012
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
check_health "dia-helper-agent" 8020 "/health"
check_health "shopgenie-agent" 8021 "/health"
check_health "career-switch-agent" 8022 "/health"
check_health "dashboard-designer-agent" 8024 "/health"
check_health "smart-gtm-agent" 8033 "/health"
check_health "seo-agent" 8034 "/health"
check_health "startup-fundraising-agent" 8035 "/health"
check_health "ats-agent" 8036 "/health"
check_health "building-construction-agent" 8037 "/health"
check_health "lms-agent" 8039 "/health"
check_health "travel-halper-agent" 8040 "/health"
check_health "restaurant-concierge-agent" 8044 "/restaurant/health"
check_health "devika-engineer-agent" 8041 "/health"
check_health "data-analyst-agent" 8042 "/health"
check_health "cyber-soc-agent" 8043 "/cybersoc/health"

info "Smoke testing public Nginx routes..."
check_nginx "/health"
check_nginx "/teams/health"
check_nginx "/todo/health"
check_nginx "/google/health"
check_nginx "/notion/health"
check_nginx "/maps/health"
check_nginx "/emergency/health"
check_nginx "/strata/health"
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
check_nginx "/diahelper/health"
check_nginx "/shopgenie/health"
check_nginx "/career-switch/health"
check_nginx "/dashboarddesigner/health"
check_nginx "/smartgtm/health"
check_nginx "/seo/health"
check_nginx "/fundraising/health"
check_nginx "/ats/health"
check_nginx "/building/health"
check_nginx "/lms/health"
check_nginx "/travelhalper/health"
check_nginx "/restaurant/health"
check_nginx "/devika/health"
check_nginx "/dataanalyst/health"
check_nginx "/cybersoc/health"

info "Smoke testing public OAuth routes..."
for slug in "${AUTH_SLUGS[@]}"; do
    check_auth_route "/${slug}/auth/login"
done

info "================================================================"
info "Deployment complete."
info "All 33 agent services are configured and started."
info ""
info "Next steps:"
info "  1. Copy your serviceAccountKey.json to ${SECRETS_DIR}/serviceAccountKey.json"
info "  2. Fill in each agent .env with provider keys, GEMINI_API_KEY, AGENT_OAUTH_SHARED_SECRET, and AGENT_PUBLIC_BASE_URL"
info "  3. Use an HTTPS domain for AGENT_PUBLIC_BASE_URL and terminate TLS at Nginx/ALB"
info "  4. Ensure OAuth redirect URIs point to <AGENT_PUBLIC_BASE_URL>/<slug>/auth/callback"
info "  5. Run 'sudo systemctl restart <agent-name>' after updating .env"
info "================================================================"
