#!/usr/bin/env bash
# Quick recovery when diag/build succeeded but compose up failed (Docker engine drop).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/resolve_host_ports.sh
. "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"

echo "Waiting for Docker daemon..."
for i in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    echo "Docker ready (${i}s)."
    break
  fi
  sleep 2
  if [ "$i" -eq 60 ]; then
    echo "ERROR: Docker daemon not reachable. Start Docker Desktop first." >&2
    exit 1
  fi
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
else
  COMPOSE="docker-compose"
fi

if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "${SCRIPT_DIR}/.env"
    set +a
fi
resolve_compose_host_ports
echo "Using host ports: backend=${BACKEND_HOST_PORT:-5000}, chroma=${CHROMA_HOST_PORT:-8000}, frontend=${FRONTEND_HOST_PORT:-3000}"

echo "Starting chroma..."
$COMPOSE up -d --force-recreate chroma
for i in $(seq 1 40); do
  status=$(docker inspect --format='{{.State.Health.Status}}' pocketpro_nyl_chroma 2>/dev/null || echo "missing")
  if [ "$status" = "healthy" ]; then
    echo "chroma healthy"
    break
  fi
  sleep 3
done

echo "Starting backend..."
$COMPOSE up -d --force-recreate backend
for i in $(seq 1 48); do
  status=$(docker inspect --format='{{.State.Health.Status}}' pocketpro_nyl_backend 2>/dev/null || echo "missing")
  if [ "$status" = "healthy" ]; then
    echo "backend healthy"
    break
  fi
  sleep 3
done

echo "Starting frontend..."
$COMPOSE up -d --force-recreate frontend

echo ""
docker ps --filter "name=pocketpro_nyl" --format "table {{.Names}}\t{{.Status}}"
echo ""
BIND_HOST="${DOCKER_BIND_HOST:-127.0.0.1}"
FRONT_PORT="${FRONTEND_HOST_PORT:-3000}"
curl -fsS "http://${BIND_HOST}:${FRONT_PORT}/api/health" && echo "" || echo "health check pending — wait 30s and retry http://${BIND_HOST}:${FRONT_PORT}/"