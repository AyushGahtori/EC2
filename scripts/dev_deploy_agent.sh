#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'USAGE'
Usage:
  scripts/dev_deploy_agent.sh <aaron|agamya|naveen> <agent-name> <branch>

Example:
  scripts/dev_deploy_agent.sh aaron github-agent aaron/fix-github-agent

What it does:
  - works only inside /home/ubuntu/app-<developer>
  - refuses branches that do not start with <developer>/
  - resets the developer EC2 folder to the remote branch
  - reinstalls Git safety hooks
  - restarts only <developer>-<agent-name>.service
  - never restarts production services
USAGE
}

if [[ $# -ne 3 ]]; then
    usage >&2
    exit 2
fi

developer="$1"
agent_name="$2"
branch="$3"

case "${developer}" in
    aaron|agamya|naveen) ;;
    *)
        echo "Invalid developer: ${developer}" >&2
        usage >&2
        exit 2
        ;;
esac

case "${agent_name}" in
    *-agent) ;;
    *)
        echo "Agent name must end with -agent, got: ${agent_name}" >&2
        exit 2
        ;;
esac

case "${branch}" in
    "${developer}"/*) ;;
    *)
        echo "Refusing branch '${branch}'. ${developer} branches must start with '${developer}/'." >&2
        exit 2
        ;;
esac

app_dir="/home/ubuntu/app-${developer}"
service_name="${developer}-${agent_name}.service"

case "${app_dir}" in
    /home/ubuntu/app-aaron|/home/ubuntu/app-agamya|/home/ubuntu/app-naveen) ;;
    *)
        echo "Unsafe app dir: ${app_dir}" >&2
        exit 1
        ;;
esac

if [[ ! -d "${app_dir}/.git" ]]; then
    echo "Missing developer app git folder: ${app_dir}" >&2
    exit 1
fi

if ! systemctl list-unit-files "${service_name}" --no-legend | grep -q "^${service_name}"; then
    echo "Missing developer service: ${service_name}" >&2
    echo "Run scripts/setup_multi_dev_env.sh first." >&2
    exit 1
fi

echo "[dev-deploy] Fetching ${branch}"
git -C "${app_dir}" fetch origin "${branch}"

if ! git -C "${app_dir}" rev-parse --verify --quiet "origin/${branch}" >/dev/null; then
    echo "Remote branch not found: origin/${branch}" >&2
    exit 1
fi

echo "[dev-deploy] Resetting ${app_dir} to origin/${branch}"
git -C "${app_dir}" switch -C "${branch}" "origin/${branch}"
git -C "${app_dir}" reset --hard "origin/${branch}"
git -C "${app_dir}" clean -fd

echo "[dev-deploy] Installing Git safety hook"
git -C "${app_dir}" config core.hooksPath .githooks
chmod +x "${app_dir}/.githooks/pre-commit" "${app_dir}/scripts/check_git_safety.sh"

echo "[dev-deploy] Verifying no unsafe staged content"
git -C "${app_dir}" diff --cached --quiet

echo "[dev-deploy] Restarting ${service_name}"
sudo systemctl restart "${service_name}"
sudo systemctl --no-pager --lines=20 status "${service_name}"

echo "[dev-deploy] Done. Production services were not touched."
