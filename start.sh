#!/usr/bin/env bash
set -euo pipefail

# cspell:ignore BUILDKIT BuildKit gtimeout healthcheck
# === PocketPro:NYL Project Robust Start Script ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/resolve_host_ports.sh
. "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"
# shellcheck source=scripts/verify_windows_ports.sh
# Performs a safe reset and robust startup for development.
# Usage: ./start.sh [--build] [--down] [--reset] [--test]
#   --build  : Force a rebuild of the docker images (--no-cache).
#   --down   : Stop and remove containers, networks, and volumes.
#   --reset  : Wipe volumes and rebuild.
#   --test   : Monitored startup plus production_test.ps1.

# Port resolution is deferred until after initial cleanup.

# --- Helper Functions ---
export DOCKER_BUILDKIT=1
COMPOSE_CMD_ARRAY=()
COMPOSE_CMD=""

choose_compose_command() {
    # Prefer 'docker compose' (v2) but fall back to 'docker-compose' (v1)
    if command docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD_ARRAY=(docker compose -f docker-compose.yml)
    elif command -v docker-compose >/dev/null 2>&1 && command docker-compose version >/dev/null 2>&1; then
        COMPOSE_CMD_ARRAY=(docker-compose -f docker-compose.yml)
    else
        echo "ERROR: Neither 'docker compose' nor 'docker-compose' commands are available." >&2
        echo "Install Docker Compose (v2 is recommended) or ensure it's on PATH." >&2
        exit 1
    fi
    COMPOSE_CMD="${COMPOSE_CMD_ARRAY[*]}"
}

compose_cmd() {
    if [ "${#COMPOSE_CMD_ARRAY[@]}" -eq 0 ]; then
        choose_compose_command
    fi
    "${COMPOSE_CMD_ARRAY[@]}" "$@"
}

is_windows_shell() {
    case "$(uname -s 2>/dev/null || echo unknown)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
        *) return 1 ;;
    esac
}

wait_for_service() {
    local svc_name="$1"
    local container_name="pocketpro_nyl_${svc_name}"
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

        local health
        health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "${container}" 2>/dev/null || true)
        if [ -n "${health}" ]; then
            if [ "${health}" = "healthy" ]; then
                echo "✓ ${svc_name} -> ${container} is healthy."
                return 0
            fi
            if [ "${health}" = "unhealthy" ]; then
                echo "✗ ERROR: ${svc_name} container (${container}) has become unhealthy." >&2
                echo "--- LOGS FOR ${svc_name} ---" >&2
                docker logs "${container}" --tail 100 || true
                echo "--------------------------------" >&2
                return 1
            fi
            echo "  ${svc_name} -> ${container} health is '${health}'..."
            sleep ${interval}
            continue
        fi

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

wait_for_http_service() {
    local svc_name="$1"
    local url="$2"
    local max_checks=${3:-30}
    local interval=${4:-3}

    echo "Waiting for ${svc_name} to be accessible at ${url} (timeout ${max_checks}*${interval}s)..."
    for i in $(seq 1 "${max_checks}"); do
        if curl -s -f --max-time ${interval} "${url}" >/dev/null; then
            echo "✓ ${svc_name} is accessible."
            return 0
        fi
        echo "  ... ${svc_name} not ready yet (attempt ${i}/${max_checks}). Retrying in ${interval}s."
        sleep ${interval}
    done

    echo "✗ ERROR: Timed out waiting for ${svc_name} to become accessible at ${url}." >&2
    return 1
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

assign_resolved_ports() {
    FRONTEND_PORT="${FRONTEND_HOST_PORT}"
    BACKEND_PORT="${BACKEND_HOST_PORT}"
    CHROMA_PORT="${CHROMA_HOST_PORT}"
    BIND_HOST_FOR_CURL="127.0.0.1"
}

trigger_startup_init() {
    local backend_url="http://${BIND_HOST_FOR_CURL}:${BACKEND_PORT}"
    local force_payload='{"force": false}'
    local message="==> Triggering backend startup initialization at ${backend_url}/api/startup_init"

    if [ "${BUILD}" = true ]; then
        message="==> Triggering backend startup with FULL DATA REFRESH (force=true)..."
        force_payload='{"force": true}'
    fi
    echo "$message"

    http_code=$(curl -s -X POST -H "Content-Type: application/json" -d "${force_payload}" "${backend_url}/api/startup_init" -w "%{http_code}" -o /dev/null --max-time 15)

    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        echo "  ✓ Backend startup init triggered successfully (HTTP $http_code)"
    else
        echo "  ✗ Failed to trigger backend startup init (HTTP $http_code). This might be expected if ingestion is already running or completed." >&2
        echo "    Check backend logs for details: ${COMPOSE_CMD} logs backend" >&2
    fi
}

wait_for_ingestion_completion() {
    local backend_url="http://${BIND_HOST_FOR_CURL}:${BACKEND_PORT}"
    local max_wait_minutes=45
    local max_wait_seconds=$((max_wait_minutes * 60))
    local elapsed=0
    local interval=5

    echo ""
    echo "==> Waiting for background data ingestion to complete (max ${max_wait_minutes} minutes)..."
    echo "    This one-time process populates the database with lottery history."
    echo "    You can monitor detailed progress at: ${backend_url}/api/startup_status"

    while [ "${elapsed}" -lt "${max_wait_seconds}" ]; do
        status_json=$(curl -s -f "${backend_url}/api/startup_status" || echo "{}")

        parsed_status=$(echo "$status_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(f"{data.get(\"status\", \"pending\")}|{data.get(\"progress\", 0)}|{data.get(\"total\", 0)}|{data.get(\"current_game\", \"N/A\")}")' 2>/dev/null || echo "pending|0|0|N/A")
        IFS='|' read -r status progress total current_game <<< "$parsed_status"

        if [[ "$status" == "completed" ]]; then
            echo "  ✓ All game data ingestion is complete."
            return 0
        fi

        echo "  ... Status: ${status} | Progress: ${progress}/${total} | Current: ${current_game} | Waiting... (${elapsed}s / ${max_wait_seconds}s)"
        sleep "${interval}"
        elapsed=$((elapsed + interval))
    done

    echo "  ✗ WARNING: Timed out waiting for data ingestion to complete after ${max_wait_minutes} minutes." >&2
    echo "    The application is running, but some games may be missing data. You can ingest them manually from the UI." >&2
}

# --- Real-time Monitoring Function ---
monitor_startup_with_regression_tests() {
    local monitor_log="startup_monitor_$(date +%Y%m%d_%H%M%S).log"
    local start_time=$(date +%s)
    
    local GREEN='\033[0;32m'
    local YELLOW='\033[1;33m'
    local RED='\033[0;31m'
    local BLUE='\033[0;34m'
    local CYAN='\033[0;36m'
    local RESET='\033[0m'
    
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${CYAN}REGRESSION TEST MONITORING ENABLED${RESET}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "Monitor log: ${monitor_log}"
    echo ""
    
    echo "=== REGRESSION TEST MONITORING LOG ===" > "$monitor_log"
    echo "Timestamp: $(date)" >> "$monitor_log"
    echo "Command: ./start.sh --build --test" >> "$monitor_log"
    echo "========================================" >> "$monitor_log"
    echo "" >> "$monitor_log"
    
    log_event() {
        local level="$1"
        local message="$2"
        local timestamp=$(date '+%H:%M:%S')
        local elapsed=$(($(date +%s) - start_time))
        
        local color=""
        case "$level" in
            "INFO") color="$CYAN" ;;
            "SUCCESS") color="$GREEN" ;;
            "WARNING") color="$YELLOW" ;;
            "ERROR") color="$RED" ;;
            *) color="$RESET" ;;
        esac
        
        echo -e "${color}[${timestamp}] [${elapsed}s] [${level}]${RESET} ${message}"
        echo "[${timestamp}] [${elapsed}s] [${level}] ${message}" >> "$monitor_log"
    }
    
    log_event "INFO" "Starting Docker environment check..."
    
    if ! command -v docker &> /dev/null; then
        log_event "ERROR" "Docker command not found"
        return 1
    fi
    log_event "SUCCESS" "Docker command available"
    
    if ! docker info >/dev/null 2>&1; then
        log_event "ERROR" "Docker daemon not responding"
        return 1
    fi
    log_event "SUCCESS" "Docker daemon responsive"

    choose_compose_command
    log_event "INFO" "Using compose: ${COMPOSE_CMD}"
    
    log_event "INFO" "Resolving host ports..."
    if ! resolve_compose_host_ports; then
        log_event "ERROR" "Failed to resolve host ports"
        return 1
    fi
    assign_resolved_ports
    log_event "SUCCESS" "Ports resolved - Frontend:${FRONTEND_HOST_PORT} Backend:${BACKEND_HOST_PORT} Chroma:${CHROMA_HOST_PORT}"
    
    log_event "INFO" "Stopping existing containers..."
    compose_cmd down --remove-orphans 2>/dev/null || true
    docker system prune -f 2>/dev/null || true
    log_event "SUCCESS" "Cleanup completed"
    
    if [ "${BUILD}" = true ]; then
        log_event "INFO" "Starting Docker image build with --no-cache..."
        local build_start=$(date +%s)
        if ! compose_cmd build --no-cache 2>&1 | tee -a "$monitor_log"; then
            log_event "ERROR" "Docker build failed"
            return 1
        fi
        local build_duration=$(($(date +%s) - build_start))
        log_event "SUCCESS" "Build completed in ${build_duration}s"
    fi
    
    log_event "INFO" "Starting Chroma service..."
    local chroma_start=$(date +%s)
    if ! compose_cmd up -d chroma 2>&1 | tee -a "$monitor_log"; then
        log_event "ERROR" "Failed to start Chroma"
        return 1
    fi
    if ! wait_for_service chroma; then
        log_event "ERROR" "Chroma health check failed"
        return 1
    fi
    local chroma_duration=$(($(date +%s) - chroma_start))
    log_event "SUCCESS" "Chroma healthy in ${chroma_duration}s"
    
    log_event "INFO" "Starting Backend service..."
    local backend_start=$(date +%s)
    if ! compose_cmd up -d backend 2>&1 | tee -a "$monitor_log"; then
        log_event "ERROR" "Failed to start Backend"
        return 1
    fi
    if ! wait_for_service backend; then
        log_event "ERROR" "Backend health check failed"
        return 1
    fi
    local backend_duration=$(($(date +%s) - backend_start))
    log_event "SUCCESS" "Backend healthy in ${backend_duration}s"
    
    log_event "INFO" "Starting Frontend service..."
    local frontend_start=$(date +%s)
    if ! compose_cmd up -d frontend 2>&1 | tee -a "$monitor_log"; then
        log_event "ERROR" "Failed to start Frontend"
        return 1
    fi
    if ! wait_for_service frontend 90 5; then
        log_event "ERROR" "Frontend health check failed"
        return 1
    fi
    local frontend_duration=$(($(date +%s) - frontend_start))
    log_event "SUCCESS" "Frontend healthy in ${frontend_duration}s"

    trigger_startup_init
    
    log_event "INFO" "Monitoring data ingestion..."
    local ingest_start=$(date +%s)
    local max_wait=2700
    local elapsed=0
    local last_status=""
    
    while [ $elapsed -lt $max_wait ]; do
        status_json=$(curl -s -f "http://${BIND_HOST_FOR_CURL}:${BACKEND_PORT}/api/startup_status" 2>/dev/null || echo "{}")
        status=$(echo "$status_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(data.get("status", "pending"))' 2>/dev/null || echo "pending")
        
        if [ "$status" != "$last_status" ]; then
            log_event "INFO" "Ingestion status: ${status}"
            last_status="$status"
        fi
        
        if [[ "$status" == "completed" ]]; then
            local ingest_duration=$(($(date +%s) - ingest_start))
            log_event "SUCCESS" "Data ingestion completed in ${ingest_duration}s"
            break
        fi
        
        sleep 5
        elapsed=$(($(date +%s) - ingest_start))
    done
    
    if [ $elapsed -ge $max_wait ]; then
        log_event "WARNING" "Data ingestion timeout after ${max_wait}s"
    fi
    
    local total_duration=$(($(date +%s) - start_time))
    log_event "SUCCESS" "Startup completed in ${total_duration}s"
    
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${GREEN}✓ STARTUP MONITORING COMPLETE${RESET}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "Monitor log saved to: ${monitor_log}"
    
    return 0
}

# --- Main Script ---
BUILD=false
DOWN=false
RESET_REQUESTED=false
TEST_REQUESTED=false

while [[ ${#} -gt 0 ]]; do
    case "$1" in
        --build) BUILD=true; shift ;;
        --down) DOWN=true; shift ;;
        --reset) RESET_REQUESTED=true; shift ;;
        --test) TEST_REQUESTED=true; shift ;;
        -h|--help) echo "Usage: $0 [--build] [--down] [--reset] [--test]"; exit 0 ;;
        *) echo "Unknown arg: $1"; echo "Usage: $0 [--build] [--down] [--reset] [--test]"; exit 2 ;;
    esac
done

if [ "${RESET_REQUESTED}" = true ]; then
    echo "Performing full reset and rebuild. This will clear all data."
    "${SCRIPT_DIR}/reset.sh" --yes
    exit 0
fi

check_docker_version
choose_compose_command
echo ""

if [ "${TEST_REQUESTED}" = true ]; then
    monitor_startup_with_regression_tests || exit 1
    
    echo ""
    echo "==> Running production regression tests..."
    timestamp=$(date +%Y-%m-%d_%H-%M-%S)
    report_file="regression_test_report_${timestamp}.txt"
    
    if command -v powershell.exe &> /dev/null; then
        powershell.exe -ExecutionPolicy Bypass -File "${SCRIPT_DIR}/production_test.ps1" -WriteFile -Verbose 2>&1 | tee "$report_file"
    elif command -v pwsh &> /dev/null; then
        pwsh -ExecutionPolicy Bypass -File "${SCRIPT_DIR}/production_test.ps1" -WriteFile -Verbose 2>&1 | tee "$report_file"
    else
        echo "ERROR: PowerShell not found. Cannot run production tests." >&2
        exit 1
    fi
    
    echo ""
    echo "==> Regression test report saved to: $report_file"
    exit 0
fi

echo "Starting PocketPro:NYL Project..."

if [ "${DOWN}" = true ]; then
    echo "Stopping and removing containers, networks, and volumes..."
    compose_cmd down -v
    echo "✓ Stack is down."
    exit 0
fi

echo "Stopping any running Docker services to clear ports..."
compose_cmd down --remove-orphans 2>/dev/null || true

sleep 2

resolve_compose_host_ports || {
    echo "ERROR: Unable to resolve Docker host ports. Check your environment values." >&2
    exit 1
}
assign_resolved_ports

echo "Targeting ports for cleanup: Frontend=${FRONTEND_PORT}, Backend=${BACKEND_PORT}, Chroma=${CHROMA_PORT}"

echo "Pruning dangling Docker resources to ensure ports are fully released..."
docker system prune -f 2>/dev/null || true

if [ "${BUILD}" = true ]; then
    echo "Building images with --no-cache..."
    if ! compose_cmd build --no-cache; then
        echo "ERROR: Build failed." >&2
        exit 1
    fi
fi

echo "Starting services in dependency order..."

echo "  Starting chroma..."
if ! compose_cmd up -d chroma; then
    echo "ERROR: 'docker compose up chroma' failed." >&2
    exit 1
fi
wait_for_service chroma || exit 1

echo "  Starting backend..."
if ! compose_cmd up -d backend; then
    echo "ERROR: 'docker compose up backend' failed." >&2
    exit 1
fi
wait_for_service backend || exit 1

echo "  Starting frontend..."
if ! compose_cmd up -d frontend; then
    echo "ERROR: 'docker compose up frontend' failed." >&2
    exit 1
fi
wait_for_service frontend 90 5 || exit 1

if ! compose_cmd ps >/dev/null; then
    echo "ERROR: 'docker compose up' failed." >&2
    echo "---" >&2
    compose_cmd ps >&2
    echo "---" >&2
    compose_cmd logs --tail 50 >&2
    echo "---" >&2
    exit 1
fi

echo ""
echo "================================================="
echo "✓ PocketPro:NYL Project Started Successfully"
echo "================================================="
echo ""
echo "Container Status:"
compose_cmd ps

trigger_startup_init

echo ""
echo "==> Running end-to-end workflow verification..."
if [ ! -f "test_all_workflows.py" ]; then
    echo "  ... skipping, test_all_workflows.py not found."
else
    wait_for_ingestion_completion

    backend_url="http://${BIND_HOST_FOR_CURL}:${BACKEND_PORT}"
    if ! POCKETPRO_API_BASE="$backend_url" python test_all_workflows.py; then
        echo "✗ WARNING: End-to-end workflow verification failed." >&2
        echo "  A core workflow (like training or prediction) has an issue." >&2
        echo "  The application stack is still running. You can proceed with manual testing." >&2
        echo "  Review the test output above for details. To see logs, run: ${COMPOSE_CMD} logs -f" >&2
    else
        echo "✓ End-to-end workflow verification passed."
    fi
fi

echo ""
echo "Access your application:"
echo "  Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "  Backend API: http://127.0.0.1:${BACKEND_PORT}/api"
echo ""
echo "To view live logs from all services, run:"
echo "  ${COMPOSE_CMD} logs -f"
