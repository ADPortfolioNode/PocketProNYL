#!/usr/bin/env bash
set -euo pipefail

# === Mensa Project Startup Monitor ===
# === PocketPro:NYL Project Startup Monitor ===
# Includes progress tracking for background ingestion

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" # PocketPro:NYL Project
# shellcheck source=scripts/resolve_host_ports.sh
. "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"

RESET='\033[0m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'

resolve_compose_host_ports || {
    echo "ERROR: Unable to resolve Docker host ports. Check your environment values." >&2
    exit 1
}

FRONTEND_PORT="$(resolve_host_port FRONTEND_HOST_PORT 3000)"
BACKEND_PORT="$(resolve_host_port BACKEND_HOST_PORT 5000)"
BIND_HOST="${DOCKER_BIND_HOST:-127.0.0.1}"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${CYAN}Mensa Project - Startup Monitor${RESET}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""

echo -e "${CYAN}[startup]${RESET} Frontend=${BIND_HOST}:${FRONTEND_PORT} Backend=${BIND_HOST}:${BACKEND_PORT}"

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "ERROR: Neither 'docker compose' nor 'docker-compose' is available."
    exit 1
fi

# Phase 1: Stop and clean
echo -e "${YELLOW}[Phase 1]${RESET} Stopping and removing existing containers..."
start_time=$(date +%s)
${COMPOSE_CMD} down --remove-orphans 2>/dev/null || true
docker system prune -f 2>/dev/null || true
phase1_duration=$(($(date +%s) - start_time))
echo -e "${GREEN}✓${RESET} Cleaned in ${phase1_duration}s"
echo ""

# Phase 2: Build
echo -e "${YELLOW}[Phase 2]${RESET} Building and starting services..."
start_time=$(date +%s)
${COMPOSE_CMD} up -d --build
phase2_duration=$(($(date +%s) - start_time))
echo -e "${GREEN}✓${RESET} Build and start completed in ${phase2_duration}s"
echo ""

# Phase 3: Wait for health checks
echo -e "${YELLOW}[Phase 3]${RESET} Waiting for services to be healthy..."
start_time=$(date +%s)
max_wait=120
elapsed=0

check_service_health() {
    local service=$1
    local max_retries=30
    local retry=0
    local container_id
    
    while [ $retry -lt $max_retries ]; do
        container_id=$(${COMPOSE_CMD} ps -q "$service" 2>/dev/null || true)
        if [ -n "$container_id" ] && docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null | grep -Eq 'healthy|running'; then
            echo -e "${GREEN}✓${RESET} $service is healthy"
            return 0
        fi
        sleep 2
        ((retry++))
        elapsed=$(($(date +%s) - start_time))
    done
    
    echo -e "${YELLOW}⚠${RESET} $service not healthy after ${elapsed}s (continuing anyway)"
    return 1
}

check_service_health "chroma"
check_service_health "backend"
check_service_health "frontend"

phase3_duration=$(($(date +%s) - start_time))
echo -e "${GREEN}✓${RESET} Services ready in ${phase3_duration}s"
echo ""

# Phase 4: Monitor ingestion progress
echo -e "${YELLOW}[Phase 4]${RESET} Monitoring background ingestion..."
start_time=$(date +%s)
last_game=""
max_wait=300
elapsed=0

while [ $elapsed -lt $max_wait ]; do
    status=$(curl -s "http://${BIND_HOST}:${BACKEND_PORT}/api/startup_status" 2>/dev/null || echo "{}")

    if echo "$status" | grep -q '"status":"completed"'; then
        phase4_duration=$(($(date +%s) - start_time))
        echo -e "${GREEN}✓${RESET} Ingestion completed in ${phase4_duration}s"
        break
    fi
    
    current_game=$(echo "$status" | grep -o '"current_game":"[^"]*"' | cut -d'"' -f4)
    progress=$(echo "$status" | grep -o '"progress":[0-9]*' | cut -d':' -f2)
    total=$(echo "$status" | grep -o '"total":[0-9]*' | cut -d':' -f2)
    
    if [ -n "$current_game" ] && [ "$current_game" != "$last_game" ]; then
        elapsed=$(($(date +%s) - start_time))
        echo -e "  ${CYAN}[${elapsed}s]${RESET} Ingesting: ${current_game} (${progress}/${total})"
        last_game="$current_game"
    fi
    
    elapsed=$(($(date +%s) - start_time))
    sleep 1
done

echo ""

# Phase 5: Final status
echo -e "${YELLOW}[Phase 5]${RESET} Container status:"
${COMPOSE_CMD} ps
echo ""

total_duration=$(($(date +%s) - start_time))
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}✓ Startup Complete${RESET}"
echo ""
echo -e "Timeline:"
echo -e "  Phase 1 (Cleanup):    ${phase1_duration}s"
echo -e "  Phase 2 (Build):      ${phase2_duration}s"
echo -e "  Phase 3 (Health):     ${phase3_duration}s"
echo -e "  ${CYAN}────────────────${RESET}"
echo -e "  Total elapsed:        ${total_duration}s"
echo ""
echo -e "Access your application:"
echo -e "  Frontend: ${CYAN}http://${BIND_HOST}:${FRONTEND_PORT}${RESET}"
echo -e "  Backend:  ${CYAN}http://${BIND_HOST}:${BACKEND_PORT}/api${RESET}"
echo -e "  Chroma:   ${CYAN}http://${BIND_HOST}:${CHROMA_HOST_PORT:-8000}/api/v1/heartbeat${RESET}"
echo ""
echo -e "View logs:"
echo -e "  Backend:  ${CYAN}${COMPOSE_CMD} logs -f backend${RESET}"
echo -e "  Frontend: ${CYAN}${COMPOSE_CMD} logs -f frontend${RESET}"
echo -e "  All:      ${CYAN}${COMPOSE_CMD} logs -f${RESET}"
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
