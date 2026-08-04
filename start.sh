#!/usr/bin/env bash
set -euo pipefail

# cspell:ignore BUILDKIT BuildKit gtimeout healthcheck
# === Mensa Project Robust Start Script ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/resolve_host_ports.sh
. "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"
# shellcheck source=scripts/verify_windows_ports.sh
# Performs a safe reset and robust startup for development.
# Usage: ./start.sh [--build] [--down]
#   --build  : Force a rebuild of the docker images (--no-cache).
#   --down   : Stop and remove containers, networks, and volumes.

# Keep the bash launcher aligned with the compose defaults and any .env overrides.
resolve_compose_host_ports || {
    echo "ERROR: Unable to resolve Docker host ports. Check your environment values." >&2
    exit 1
}

# --- Helper Functions ---
export DOCKER_BUILDKIT=1

choose_compose_command() {
    # Prefer 'docker compose' (v2) but fall back to 'docker-compose' (v1)
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    else
        echo "ERROR: Neither 'docker compose' nor 'docker-compose' commands are available." >&2
        echo "Install Docker Compose (v2 is recommended) or ensure it's on PATH." >&2
        exit 1
    fi
}

compose_cmd() {
    if [ "${COMPOSE_CMD}" = "docker compose" ]; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

is_windows_shell() {
    case "$(uname -s 2>/dev/null || echo unknown)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
        *) return 1 ;;
    esac
}

check_docker_version() {
    echo "Checking Docker environment..."
    
    if ! command -v docker &> /dev/null; then
        echo "ERROR: 'docker' command not found." >&2
        echo "Please ensure Docker is installed and that the 'docker' command is in your system's PATH." >&2
        exit 1
    fi

    echo "Pinging Docker daemon..."
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Could not connect to the Docker daemon." >&2
        echo "Please ensure the Docker Desktop application is running." >&2
        exit 1
    fi
    echo "✓ Docker daemon is responsive."

    local client_ver server_ver
    client_ver=$(docker version --format '{{.Client.APIVersion}}' 2>/dev/null)
    server_ver=$(docker version --format '{{.Server.APIVersion}}' 2>/dev/null)

    if [ -z "${client_ver}" ] || [ -z "${server_ver}" ]; then
        echo "WARNING: Could not determine Docker client/server API version."
        echo "This can happen if Docker is not running or is not installed correctly."
        echo "The script will attempt to continue, but may fail."
        echo ""
        return
    fi

    local client_major server_major
    client_major=$(echo "$client_ver" | cut -d. -f1-2)
    server_major=$(echo "$server_ver" | cut -d. -f1-2)

    if [ "$client_major" != "$server_major" ]; then
        echo "ERROR: Docker client and server API versions are mismatched."
        echo "  Client API: $client_ver"
        echo "  Server API: $server_ver"
        echo "This can cause unexpected errors. Please see TROUBLESHOOTING_DOCKER_ERROR.txt for instructions on how to resolve this."
        exit 1
    fi
    echo "✓ Docker version check passed (Client: $client_ver, Server: $server_ver)."
}

# --- Main Script ---

BUILD=false
DOWN=false

while [[ ${#} -gt 0 ]]; do
    case "$1" in
        --build) BUILD=true; shift ;;
        --down) DOWN=true; shift ;;
        -h|--help) echo "Usage: $0 [--build] [--down]"; exit 0 ;;
        *) echo "Unknown arg: $1"; echo "Usage: $0 [--build] [--down]"; exit 2 ;;
    esac
done

# --- Execution Flow ---

echo "Starting Mensa Project..."
echo ""

# 1. Check Docker environment first
check_docker_version
choose_compose_command
echo ""

# 2. Stop if requested
if [ "${DOWN}" = true ]; then
    echo "Stopping and removing containers, networks, and volumes..."
    compose_cmd down -v
    echo "✓ Stack is down."
    exit 0
fi

# 3. Stop existing containers before starting
echo "Stopping any running services..."
compose_cmd down
    echo ""

# 4. Build and start services
UP_ARGS=("-d" "--wait")

if [ "${BUILD}" = true ]; then
    echo "Building images with --no-cache..."
    if ! compose_cmd build --no-cache; then
        echo "ERROR: Build failed." >&2
        exit 1
    fi
fi

echo "Starting services..."
if ! compose_cmd up "${UP_ARGS[@]}"; then
    echo "ERROR: 'docker compose up' failed." >&2
    echo "---" >&2
    compose_cmd ps >&2
    echo "---" >&2
    compose_cmd logs --tail 50 >&2
    echo "---" >&2
    exit 1
fi

# Helper to check a service readiness
wait_for_service() {
    local svc_name="$1"
    local container_name="mensa_${svc_name}" # Keep this for fallback search
    local max_checks=${2:-30}
    local interval=${3:-5}
    echo "Waiting for ${svc_name} to be running/healthy (timeout ${max_checks}*${interval}s)..."
    
    for i in $(seq 1 ${max_checks}); do
        local container_id container
        container_id=$(compose_cmd ps -q "${svc_name}" 2>/dev/null || true)
        container=""

        if [ -n "${container_id}" ]; then
            container=$(docker inspect --format '{{.Name}}' "${container_id}" 2>/dev/null | sed 's#^/##' || true)
        fi

        if [ -z "${container}" ]; then
            container=$(docker ps -a --filter "name=${container_name}" --format "{{.Names}}" | head -n1 || true)
        fi

        if [ -z "${container}" ]; then
            echo "  ${svc_name} -> container not found yet..."
            sleep ${interval}
            continue
        fi

        local status
        status=$(docker ps -a --filter "name=${container}" --format "{{.Status}}" | head -n1 || true)

        if echo "${status}" | grep -qE "Exited|Dead"; then
            echo "✗ ERROR: ${svc_name} container (${container}) has exited unexpectedly." >&2
            echo "--- LOGS FOR ${container} ---" >&2
            docker logs "${container}" --tail 100 || true
            echo "--------------------------------" >&2
            return 1
        fi

        # Check health if present
        local health
        health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${container}" 2>/dev/null || true)
        if [ -n "${health}" ]; then
            if [ "${health}" = "healthy" ]; then
                echo "✓ ${svc_name} -> ${container} is healthy."
                return 0
            fi
            if [ "${health}" = "unhealthy" ]; then
                echo "✗ ERROR: ${svc_name} container (${container}) has become unhealthy." >&2
                echo "--- LOGS FOR ${container} ---" >&2
                docker logs "${container}" --tail 100 || true
                echo "--------------------------------" >&2
                return 1
            fi
            echo "  ${svc_name} -> ${container} health is '${health}'..."
            sleep ${interval}
            continue
        fi

        # Fallback: consider 'Up' status as ready if no healthcheck
        if echo "${status}" | grep -iq "Up"; then
            echo "✓ ${svc_name} -> ${container} is Up (no healthcheck)."
            return 0
        fi

        echo "  ${svc_name} -> ${container} status is '${status}'..."
        sleep ${interval}
    done

    echo "✗ ERROR: Timed out waiting for ${svc_name} to become ready." >&2
    echo "--- STATUS OF ${svc_name} ---" >&2
    compose_cmd ps "${svc_name}" >&2
    echo "--- LOGS FOR ${svc_name} ---" >&2
    compose_cmd logs --tail 100 "${svc_name}" >&2
    echo "------------------------------------" >&2
    return 1
}

echo ""
echo "================================================="
echo "✓ Mensa Project Started Successfully"
echo "================================================="
echo ""
echo "Container Status:"
compose_cmd ps

FRONTEND_PORT="$(resolve_host_port FRONTEND_HOST_PORT 3000)"
BACKEND_PORT="$(resolve_host_port BACKEND_HOST_PORT 5000)"

# Keep the final startup banner consistent with the same values compose is using.
echo ""
echo "Access your application:"
echo "  Frontend: http://localhost:${FRONTEND_PORT}"
echo "  Backend API: http://localhost:${BACKEND_PORT}/api"
echo ""
echo "To view live logs from all services, run:"
echo "  ${COMPOSE_CMD} logs -f"
