#!/usr/bin/env bash
set -Eeuo pipefail

BASE_DIR="/home/ubuntu"
PROD_APP="${BASE_DIR}/app"
CONFIG_OUT="${CONFIG_OUT:-}"
BRANCH_NAME="${BRANCH_NAME:-ci-cd-development}"
START_SERVICES="${START_SERVICES:-0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEVELOPERS=(aaron agamya naveen)

info() { printf '[multi-dev] %s\n' "$*"; }
die() { printf '[multi-dev:error] %s\n' "$*" >&2; exit 1; }

[[ -d "${PROD_APP}" ]] || die "Missing production app folder: ${PROD_APP}"
[[ -f "${PROD_APP}/nginx/sites-available/agents" ]] || die "Missing production Nginx config."

copy_folders() {
    for dev in "${DEVELOPERS[@]}"; do
        local target="${BASE_DIR}/app-${dev}"
        case "${target}" in
            /home/ubuntu/app-aaron|/home/ubuntu/app-agamya|/home/ubuntu/app-naveen) ;;
            *) die "Refusing unsafe target: ${target}" ;;
        esac

        if [[ ! -d "${target}" ]]; then
            info "Creating ${target} from ${PROD_APP}"
            sudo cp -a "${PROD_APP}" "${target}"
        else
            info "${target} already exists"
        fi

        sudo chown -R ubuntu:ubuntu "${target}"
    done
}

prepare_git_branches() {
    for dev in "${DEVELOPERS[@]}"; do
        local target="${BASE_DIR}/app-${dev}"
        info "Preparing ${BRANCH_NAME} in ${target}"
        git -C "${target}" fetch origin "${BRANCH_NAME}"
        git -C "${target}" switch -C "${BRANCH_NAME}" "origin/${BRANCH_NAME}"
        git -C "${target}" reset --hard "origin/${BRANCH_NAME}"
    done
}

install_git_safety_hooks() {
    info "Installing tracked Git safety hooks"
    local targets=("${PROD_APP}")
    local dev
    for dev in "${DEVELOPERS[@]}"; do
        targets+=("${BASE_DIR}/app-${dev}")
    done

    local target
    for target in "${targets[@]}"; do
        if [[ -d "${target}/.git" ]]; then
            if [[ ! -f "${target}/.githooks/pre-commit" || ! -f "${target}/scripts/check_git_safety.sh" ]]; then
                info "Skipping Git safety hook for ${target}; tracked hook files are not present in this checkout yet."
                continue
            fi

            git -C "${target}" config core.hooksPath .githooks
            chmod +x "${target}/.githooks/pre-commit" "${target}/scripts/check_git_safety.sh"
            if [[ -f "${target}/scripts/dev_deploy_agent.sh" ]]; then
                chmod +x "${target}/scripts/dev_deploy_agent.sh"
            fi
        fi
    done
}

install_systemd_units() {
    info "Rendering developer systemd units"
    if [[ -z "${CONFIG_OUT}" ]]; then
        CONFIG_OUT="$(mktemp -d -t ei-multi-dev-XXXXXX)"
    fi

    python3 "${SCRIPT_DIR}/render_multi_dev_configs.py" \
        --repo "${PROD_APP}" \
        --output "${CONFIG_OUT}"

    info "Installing developer systemd units"
    sudo install -m 0644 "${CONFIG_OUT}"/systemd/*.service /etc/systemd/system/
    sudo systemctl daemon-reload

    if [[ "${START_SERVICES}" == "1" ]]; then
        info "Enabling and starting developer services"
        for service_path in "${CONFIG_OUT}"/systemd/*.service; do
            sudo systemctl enable --now "$(basename "${service_path}")"
        done
    else
        info "Developer services installed but not started. Set START_SERVICES=1 to enable them."
    fi
}

install_nginx_config() {
    local rendered="${CONFIG_OUT}/nginx/agents"
    local nginx_dst="/etc/nginx/sites-available/agents"
    local backup
    backup="/etc/nginx/sites-available/agents.backup.$(date +%Y%m%d%H%M%S)"

    info "Installing Nginx config with /api/{developer}/... routes"
    [[ -f "${rendered}" ]] || die "Rendered Nginx config missing: ${rendered}"

    if [[ -f "${nginx_dst}" ]]; then
        sudo cp "${nginx_dst}" "${backup}"
    else
        info "No existing Nginx config found at ${nginx_dst}; skipping backup."
    fi

    sudo install -m 0644 "${rendered}" "${nginx_dst}"
    if sudo nginx -t; then
        sudo systemctl reload nginx
        info "Nginx reloaded. Backup saved at ${backup}"
    fi
}

copy_folders
prepare_git_branches
install_git_safety_hooks
install_systemd_units
install_nginx_config

info "Done. Production app folder was not modified."
