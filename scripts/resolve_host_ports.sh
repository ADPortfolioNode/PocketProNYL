#!/usr/bin/env bash
# Resolve Docker Compose host port mappings when defaults are already taken.

host_port_in_use() {
    local port="$1"

    if command -v docker >/dev/null 2>&1; then
        # `docker ps -a` (not just `docker ps`): a created-but-not-running
        # container still holds its published port mapping, and Docker's
        # allocator refuses the bind even though nothing listens on it.
        # `docker ps -a` includes running containers too, so one call
        # covers both. The `:${port}->` match is host-side only (the
        # container port is followed by /tcp, not ->), so any host IP
        # counts — including a LAN-IP binding like
        # 192.168.1.5:5001->5000/tcp.
        if docker ps -a --format '{{.Ports}}' 2>/dev/null | grep -qE ":${port}->"; then
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

    # A preset value is honoured only when it is actually free. When it
    # is busy we bump +1 from it instead of trusting it blindly — a
    # preset BACKEND_HOST_PORT=5003 used to sail straight into a
    # "Bind for 0.0.0.0:5003 failed: port is already allocated" failure
    # because this function returned early without checking it.
    if [ -n "${current}" ]; then
        if ! host_port_in_use "${current}"; then
            export "${var_name}=${current}"
            return 0
        fi
        local free_port
        if ! free_port=$(find_free_host_port "${current}" 20); then
            echo "ERROR: Could not find a free host port near ${current} for ${var_name}." >&2
            return 1
        fi
        export "${var_name}=${free_port}"
        echo "Host port ${current} is busy; using ${free_port} for ${var_name}." >&2
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
