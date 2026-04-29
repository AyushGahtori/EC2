#!/usr/bin/env bash
set -Eeuo pipefail

MODE="staged"
RANGE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --staged)
            MODE="staged"
            shift
            ;;
        --range)
            MODE="range"
            RANGE="${2:-}"
            [[ -n "${RANGE}" ]] || {
                echo "Missing value for --range" >&2
                exit 2
            }
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if [[ "${MODE}" == "staged" ]]; then
    mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR)
else
    if [[ "${RANGE}" == *"..."* || "${RANGE}" == *".."* ]]; then
        mapfile -t files < <(git diff --name-only --diff-filter=ACMR "${RANGE}")
    else
        mapfile -t files < <(git diff-tree --no-commit-id --name-only -r --diff-filter=ACMR "${RANGE}")
    fi
fi

blocked=()

add_block() {
    local file="$1"
    local reason="$2"
    blocked+=("${file} :: ${reason}")
}

is_blocked_path() {
    local file="$1"

    case "${file}" in
        .env|.env.*|*/.env|*/.env.*)
            [[ "${file}" == ".env.example" || "${file}" == */.env.example ]] || return 0
            ;;
        .secrets/*|*/.secrets/*)
            return 0
            ;;
        serviceAccountKey.json|*/serviceAccountKey.json|*.pem|*.key|*.crt|*.p12|*.pfx)
            return 0
            ;;
        */venv/*|venv/*|*/.venv/*|.venv/*|*/__pycache__/*|__pycache__/*)
            return 0
            ;;
        *.log|*.pid|*.sock|tmp/*|*/tmp/*|.runtime/*|*/.runtime/*)
            return 0
            ;;
        systemd/aaron-*.service|systemd/agamya-*.service|systemd/naveen-*.service)
            return 0
            ;;
        nginx/generated/*|nginx/*generated*)
            return 0
            ;;
    esac

    return 1
}

for file in "${files[@]}"; do
    if is_blocked_path "${file}"; then
        add_block "${file}" "runtime, secret, cache, or developer-only config must not be committed"
        continue
    fi

    [[ -f "${file}" ]] || continue

    if [[ "${file}" == systemd/*.service || "${file}" == nginx/* || "${file}" == nginx/sites-available/* ]]; then
        if grep -Eq '/home/ubuntu/app-(aaron|agamya|naveen)\b|(^|[^[:alnum:]_-])(aaron|agamya|naveen)-[a-z0-9-]+-agent|/api/(aaron|agamya|naveen)/' "${file}"; then
            add_block "${file}" "developer runtime config detected inside production-tracked config"
        fi
    fi
done

if (( ${#blocked[@]} > 0 )); then
    cat >&2 <<'MESSAGE'
Blocked unsafe Git contents.

Only actual reusable code should be committed from developer EC2 folders.
Do not commit secrets, .env files, virtualenvs, logs, generated developer
systemd units, generated Nginx routes, or config pointing at app-aaron,
app-agamya, or app-naveen.

Blocked files:
MESSAGE
    printf '  - %s\n' "${blocked[@]}" >&2
    exit 1
fi

exit 0
