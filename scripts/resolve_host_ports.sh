#!/usr/bin/env bash
# Resolve Docker Compose host port mappings when defaults are already taken.

host_port_in_use() {
    local port="$1"

    if command -v docker >/dev/null 2>&1; then
        if docker ps --format '{{.Ports}}' 2>/dev/null | grep -qE "(0\\.0\\.0\\.0|127\\.0\\.0\\.1|:::|\\[::\\]):${port}->"; then
            return 0
        fi
    fi

    case "$(uname -s 2>/dev/null || echo unknown)" in
        MINGW*|MSYS*|CYGWIN*)
            if command -v powershell.exe >/dev/null 2>&1; then
                powershell.exe -NoProfile -Command \
                    "if (Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1) { exit 0 } else { exit 1 }" \
                    >/dev/null 2>&1
                return $?
            fi
            ;;
    esac

    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"
        return $?
    fi

    if command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"
        return $?
    fi

    return 1
}

find_free_host_port() {
    local preferred="$1"
    local max_tries="${2:-20}"
    local port="${preferred}"
    local tries=0

    while [ "${tries}" -lt "${max_tries}" ]; do
        if ! host_port_in_use "${port}"; then
            echo "${port}"
            return 0
        fi
        port=$((port + 1))
        tries=$((tries + 1))
    done

    return 1
}

resolve_compose_host_port() {
    local var_name="$1"
    local preferred="$2"
    local current="${!var_name:-}"

    if [ -n "${current}" ]; then
        return 0
    fi

    if ! host_port_in_use "${preferred}"; then
        export "${var_name}=${preferred}"
        return 0
    fi

    local free_port
    if ! free_port=$(find_free_host_port "${preferred}" 20); then
        echo "ERROR: Could not find a free host port near ${preferred} for ${var_name}." >&2
        return 1
    fi

    export "${var_name}=${free_port}"
    echo "Host port ${preferred} is busy; using ${free_port} for ${var_name}." >&2
    return 0
}

resolve_host_port() {
    local var_name="$1"
    local preferred="${2:-}"
    local value="${!var_name:-}"

    if [ -n "${value}" ]; then
        echo "${value}"
        return 0
    fi

    if [ -f ".env" ]; then
        while IFS= read -r line; do
            case "${line}" in
                "${var_name}="*)
                    value="${line#*=}"
                    value="${value%\"}"
                    value="${value#\"}"
                    value="${value%\'}"
                    value="${value#\'}"
                    echo "${value}"
                    return 0
                    ;;
            esac
        done < .env
    fi

    if [ -n "${preferred}" ]; then
        echo "${preferred}"
        return 0
    fi

    return 1
}

resolve_compose_host_ports() {
    resolve_compose_host_port FRONTEND_HOST_PORT 3000 || return 1
    resolve_compose_host_port BACKEND_HOST_PORT 5001 || return 1
    resolve_compose_host_port CHROMA_HOST_PORT 8001 || return 1
    return 0
}
