#!/usr/bin/env bash
set -euo pipefail

# PocketPro:NYL Optimized Startup Script
# Industry-standard startup with dynamic configuration and comprehensive monitoring
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging
log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Configuration
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
BUILD_CACHE_BUSTER="${BACKEND_CACHE_BUSTER:-stable}"
SKIP_ONNX="${SKIP_ONNX:-1}"

# Parse command line arguments
BUILD=false
DOWN=false
RESET=false
TEST=false
MONITOR_ONLY=false
FORCE_INGEST=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build) BUILD=true; shift ;;
        --down) DOWN=true; shift ;;
        --reset) RESET=true; shift ;;
        --test) TEST=true; shift ;;
        --monitor) MONITOR_ONLY=true; shift ;;
        --force) FORCE_INGEST=true; shift ;;
        -h|--help) 
            echo "Usage: $0 [--build] [--down] [--reset] [--test] [--monitor] [--force]"
            echo "  --build   : Force rebuild of Docker images"
            echo "  --down    : Stop and remove containers"
            echo "  --reset   : Full reset with data cleanup"
            echo "  --test    : Run production regression tests"
            echo "  --monitor : Start monitoring mode only"
            echo "  --force   : Force full data refresh"
            exit 0 
            ;;
        *) log_error "Unknown argument: $1"; exit 2 ;;
    esac
done

# Docker Compose command selection
choose_compose_command() {
    if command docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD="docker compose -f ${COMPOSE_FILE}"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose -f ${COMPOSE_FILE}"
    else
        log_error "Neither 'docker compose' nor 'docker-compose' found"
        exit 1
    fi
    log_info "Using: ${COMPOSE_CMD}"
}

# Service health check with timeout
wait_for_service() {
    local service="$1"
    local max_checks="${2:-30}"
    local interval="${3:-5}"
    local container_name="pocketpro_nyl_${service}"
    
    log_step "Waiting for ${service} to be healthy..."
    
    for i in $(seq 1 ${max_checks}); do
        local status
        status=$($COMPOSE_CMD ps -q "${service}" 2>/dev/null && docker inspect --format '{{.State.Health.Status}}' "${container_name}" 2>/dev/null || echo "unknown")
        
        case "${status}" in
            healthy)
                log_success "${service} is healthy"
                return 0
                ;;
            unhealthy)
                log_error "${service} is unhealthy"
                docker logs "${container_name}" --tail 50
                return 1
                ;;
            starting|unknown)
                echo "  ... ${service} status: ${status} (${i}/${max_checks})"
                sleep "${interval}"
                ;;
            *)
                echo "  ... ${service} not ready yet (${i}/${max_checks})"
                sleep "${interval}"
                ;;
        esac
    done
    
    log_error "Timeout waiting for ${service}"
    return 1
}

# HTTP endpoint check
wait_for_http() {
    local url="$1"
    local name="$2"
    local max_checks="${3:-30}"
    local interval="${4:-3}"
    
    log_step "Waiting for ${name} HTTP endpoint..."
    
    for i in $(seq 1 ${max_checks}); do
        if curl -s -f --max-time "${interval}" "${url}" >/dev/null 2>&1; then
            log_success "${name} HTTP endpoint is responding"
            return 0
        fi
        echo "  ... ${name} not ready (${i}/${max_checks})"
        sleep "${interval}"
    done
    
    log_error "Timeout waiting for ${name} HTTP endpoint"
    return 1
}

# Prerequisites check
check_prerequisites() {
    log_step "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found"
        exit 1
    fi
    
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon not responding"
        exit 1
    fi
    
    log_success "Prerequisites check passed"
}

# Environment setup
setup_environment() {
    log_step "Setting up environment..."
    
    # Source port resolution script if exists
    if [ -f "${SCRIPT_DIR}/scripts/resolve_host_ports.sh" ]; then
        . "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"
        log_info "Port resolution script loaded"
    fi
    
    # Set default values if not set
    export FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-3000}"
    export BACKEND_HOST_PORT="${BACKEND_HOST_PORT:-5001}"
    export CHROMA_HOST_PORT="${CHROMA_HOST_PORT:-8001}"
    export DOCKER_BIND_HOST="${DOCKER_BIND_HOST:-0.0.0.0}"
    
    log_success "Environment configured"
    log_info "  Frontend: ${FRONTEND_HOST_PORT}"
    log_info "  Backend: ${BACKEND_HOST_PORT}"
    log_info "  Chroma: ${CHROMA_HOST_PORT}"
}

# Cleanup operations
cleanup_stack() {
    log_step "Cleaning up existing stack..."
    
    $COMPOSE_CMD down --remove-orphans 2>/dev/null || true
    docker system prune -f 2>/dev/null || true
    
    log_success "Cleanup completed"
}

# Build operations
build_images() {
    log_step "Building Docker images..."
    
    local build_args=(
        --build-arg CACHE_BUSTER="${BUILD_CACHE_BUSTER}"
        --build-arg SKIP_ONNX="${SKIP_ONNX}"
    )
    
    if [ "${BUILD}" = true ]; then
        build_args+=(--no-cache)
        log_info "Building with --no-cache"
    fi
    
    if $COMPOSE_CMD build "${build_args[@]}"; then
        log_success "Build completed successfully"
    else
        log_error "Build failed"
        exit 1
    fi
}

# Start services in dependency order
start_services() {
    log_step "Starting services in dependency order..."
    
    # Start Chroma
    log_info "Starting Chroma..."
    $COMPOSE_CMD up -d chroma
    wait_for_service chroma
    
    # Start Backend
    log_info "Starting Backend..."
    $COMPOSE_CMD up -d backend
    wait_for_service backend
    
    # Start Frontend
    log_info "Starting Frontend..."
    $COMPOSE_CMD up -d frontend
    wait_for_service frontend 90 5
    
    log_success "All services started successfully"
}

# Trigger ingestion
trigger_ingestion() {
    local backend_url="http://127.0.0.1:${BACKEND_HOST_PORT}"
    local force_flag="${FORCE_INGEST}"
    
    log_step "Triggering data ingestion..."
    
    local payload="{\"force\": ${force_flag}}"
    local response=$(curl -s -X POST \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${backend_url}/api/startup_init" \
        -w "%{http_code}" \
        -o /dev/null \
        --max-time 15)
    
    if [[ "$response" -ge 200 && "$response" -lt 300 ]]; then
        log_success "Ingestion triggered successfully (HTTP $response)"
    else
        log_warning "Ingestion trigger returned HTTP $response (may be already running)"
    fi
}

# Monitor ingestion progress
monitor_ingestion() {
    local backend_url="http://127.0.0.1:${BACKEND_HOST_PORT}"
    local max_wait_minutes=45
    local max_wait_seconds=$((max_wait_minutes * 60))
    local elapsed=0
    local interval=5
    
    log_step "Monitoring ingestion progress (max ${max_wait_minutes} minutes)..."
    
    while [ ${elapsed} -lt ${max_wait_seconds} ]; do
        local status_json
        status_json=$(curl -s -f "${backend_url}/api/startup_status" 2>/dev/null || echo "{}")
        
        local status
        status=$(echo "$status_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(data.get("status", "pending"))' 2>/dev/null || echo "pending")
        
        case "${status}" in
            completed)
                log_success "Ingestion completed successfully"
                return 0
                ;;
            error)
                log_error "Ingestion failed"
                return 1
                ;;
            *)
                local progress
                progress=$(echo "$status_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(data.get("percent_complete", 0))' 2>/dev/null || echo "0")
                echo "  ... Progress: ${progress}% | Elapsed: ${elapsed}s"
                ;;
        esac
        
        sleep "${interval}"
        elapsed=$((elapsed + interval))
    done
    
    log_warning "Ingestion monitoring timeout after ${max_wait_minutes} minutes"
    return 0
}

# Run production tests
run_production_tests() {
    log_step "Running production regression tests..."
    
    local test_script="${SCRIPT_DIR}/production_test.ps1"
    local timestamp=$(date +%Y-%m-%d_%H-%M-%S)
    local report_file="regression_test_report_${timestamp}.txt"
    
    if command -v powershell.exe &> /dev/null; then
        powershell.exe -ExecutionPolicy Bypass -File "${test_script}" -WriteFile -Verbose 2>&1 | tee "${report_file}"
    elif command -v pwsh &> /dev/null; then
        pwsh -ExecutionPolicy Bypass -File "${test_script}" -WriteFile -Verbose 2>&1 | tee "${report_file}"
    else
        log_error "PowerShell not found, cannot run tests"
        return 1
    fi
    
    log_success "Test report saved to: ${report_file}"
}

# Display service status
display_status() {
    log_step "Service Status:"
    $COMPOSE_CMD ps
    
    echo ""
    log_info "Access URLs:"
    echo "  Frontend: http://127.0.0.1:${FRONTEND_HOST_PORT}"
    echo "  Backend:  http://127.0.0.1:${BACKEND_HOST_PORT}/api"
    echo "  Chroma:   http://127.0.0.1:${CHROMA_HOST_PORT}"
}

# Main execution
main() {
    log_info "PocketPro:NYL Optimized Startup"
    echo ""
    
    # Handle reset
    if [ "${RESET}" = true ]; then
        log_warning "Performing full reset - this will clear all data"
        "${SCRIPT_DIR}/reset.sh" --yes
        exit 0
    fi
    
    # Handle down
    if [ "${DOWN}" = true ]; then
        log_info "Stopping services..."
        $COMPOSE_CMD down -v
        log_success "Services stopped"
        exit 0
    fi
    
    # Handle monitor only
    if [ "${MONITOR_ONLY}" = true ]; then
        monitor_ingestion
        exit 0
    fi
    
    # Normal startup sequence
    check_prerequisites
    choose_compose_command
    setup_environment
    cleanup_stack
    
    if [ "${BUILD}" = true ]; then
        build_images
    fi
    
    start_services
    trigger_ingestion
    
    if [ "${TEST}" = true ]; then
        monitor_ingestion
        run_production_tests
    else
        monitor_ingestion
    fi
    
    display_status
    
    log_success "Startup completed successfully"
}

# Run main function
main "$@"