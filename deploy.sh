#!/bin/bash
# Deploy script for SnitchX agents on EC2.
# Run with: sudo ./deploy.sh

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/app}"
SECRETS_DIR="${SECRETS_DIR:-/app/.secrets}"
SERVICE_ACCOUNT_SOURCE="${SERVICE_ACCOUNT_SOURCE:-/home/ubuntu/app/ai-everyone/serviceAccountKey.json}"
SERVICE_USER="${SERVICE_USER:-ubuntu}"

TEAMS_DIR="${APP_DIR}/agents/teams-agent"
TODO_DIR="${APP_DIR}/agents/todo-agent"
SYSTEMD_SRC_DIR="${APP_DIR}/systemd"
NGINX_SRC_CONF="${APP_DIR}/nginx/sites-available/agents"


require_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        echo "This script must be run as root. Use: sudo ./deploy.sh"
        exit 1
    fi
}


require_dir() {
    local dir_path="$1"
    if [[ ! -d "${dir_path}" ]]; then
        echo "Required directory missing: ${dir_path}"
        exit 1
    fi
}


require_user() {
    local user_name="$1"
    if ! id -u "${user_name}" >/dev/null 2>&1; then
        echo "Required user does not exist: ${user_name}"
        exit 1
    fi
}


install_system_packages() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        nginx \
        certbot \
        python3-certbot-nginx
}


setup_python_env() {
    local service_dir="$1"
    local requirements_file="${service_dir}/requirements.txt"
    local venv_dir="${service_dir}/venv"

    if [[ ! -f "${requirements_file}" ]]; then
        echo "Missing requirements file: ${requirements_file}"
        exit 1
    fi

    python3 -m venv "${venv_dir}"
    "${venv_dir}/bin/pip" install --upgrade pip wheel
    "${venv_dir}/bin/pip" install -r "${requirements_file}"
}


install_systemd_units() {
    install -m 0644 "${SYSTEMD_SRC_DIR}/teams-agent.service" /etc/systemd/system/teams-agent.service
    install -m 0644 "${SYSTEMD_SRC_DIR}/todo-agent.service" /etc/systemd/system/todo-agent.service
    systemctl daemon-reload
}


install_nginx_config() {
    install -m 0644 "${NGINX_SRC_CONF}" /etc/nginx/sites-available/agents
    ln -sfn /etc/nginx/sites-available/agents /etc/nginx/sites-enabled/agents

    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
    fi

    nginx -t
    systemctl enable nginx
    systemctl restart nginx
}


start_services() {
    systemctl enable --now teams-agent todo-agent
    systemctl restart teams-agent todo-agent
}


setup_service_account_key() {
    local target_key="${SECRETS_DIR}/serviceAccountKey.json"

    if [[ -f "${SERVICE_ACCOUNT_SOURCE}" ]]; then
        install -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0600 "${SERVICE_ACCOUNT_SOURCE}" "${target_key}"
        echo "Firebase service account key copied to ${target_key}"
        return
    fi

    if [[ -f "${target_key}" ]]; then
        chown "${SERVICE_USER}:${SERVICE_USER}" "${target_key}"
        chmod 600 "${target_key}"
        echo "Firebase service account key already present at ${target_key}"
        return
    fi

    echo "WARNING: Firebase key not found."
    echo "Expected source: ${SERVICE_ACCOUNT_SOURCE}"
    echo "Set FIREBASE_SERVICE_ACCOUNT_KEY in todo-agent .env or copy key to ${target_key}."
}


print_post_deploy_notes() {
    echo "Deployment complete."
    echo ""
    echo "Next steps:"
    echo "1) Verify Firebase key at ${SECRETS_DIR}/serviceAccountKey.json."
    echo "2) Agents are available on HTTP via 13.206.83.175 (no domain required)."
    echo "3) Optional later: attach domain + certbot for HTTPS."
    echo ""
    echo "Useful checks:"
    echo "- systemctl status teams-agent --no-pager"
    echo "- systemctl status todo-agent --no-pager"
    echo "- curl http://127.0.0.1:8100/health"
    echo "- curl http://127.0.0.1:8200/health"
    echo "- curl http://13.206.83.175/health"
    echo "- curl http://13.206.83.175/todo/health"
}


main() {
    require_root
    require_user "${SERVICE_USER}"

    require_dir "${APP_DIR}"
    require_dir "${TEAMS_DIR}"
    require_dir "${TODO_DIR}"
    require_dir "${SYSTEMD_SRC_DIR}"

    mkdir -p "${SECRETS_DIR}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${SECRETS_DIR}"
    chmod 700 "${SECRETS_DIR}"

    install_system_packages
    setup_python_env "${TEAMS_DIR}"
    setup_python_env "${TODO_DIR}"
    setup_service_account_key

    install_systemd_units
    install_nginx_config
    start_services
    print_post_deploy_notes
}


main "$@"
