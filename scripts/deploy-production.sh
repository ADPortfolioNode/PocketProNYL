#!/usr/bin/env bash
# Deploy PocketPro:NYL on a Linux web server for subscribing customers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="docker-compose.distribution.yml"
ENV_FILE=".env"

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.production.example "$ENV_FILE"
  echo "Created $ENV_FILE from .env.production.example — edit DOMAIN, ACME_EMAIL, and API keys, then re-run."
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

CADDY_PROFILE="${CADDY_PROFILE:-tls}"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")

if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  COMPOSE_ARGS+=(-f docker-compose.distribution.build.yml)
  echo "Local build mode (images built on this server)"
fi

if [[ "${DEPLOY_MODE:-tls}" == "direct" ]]; then
  COMPOSE_ARGS+=(-f docker-compose.direct.yml)
  echo "Direct HTTP mode (no Caddy). Frontend: http://${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_PORT:-3000}"
else
  COMPOSE_ARGS+=(--profile tls)
  if [[ -z "${DOMAIN:-}" ]]; then
    echo "ERROR: DOMAIN is required for TLS mode. Set it in .env"
    exit 1
  fi
  if [[ "$CADDY_PROFILE" == "subscribers" && -z "${BASIC_AUTH_HASH:-}" ]]; then
    echo "ERROR: CADDY_PROFILE=subscribers requires BASIC_AUTH_HASH in .env"
    echo "Generate: docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-password'"
    exit 1
  fi
  if [[ ! -f "deploy/caddy/Caddyfile.${CADDY_PROFILE}" ]]; then
    echo "ERROR: deploy/caddy/Caddyfile.${CADDY_PROFILE} not found"
    exit 1
  fi
  echo "TLS mode — https://${DOMAIN} (Caddy profile: ${CADDY_PROFILE})"
fi

if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  echo "Building images locally..."
  docker compose "${COMPOSE_ARGS[@]}" build
else
  echo "Pulling images (registry=${POCKETPRO_NYL_REGISTRY:-ghcr.io/adportfolionode}, version=${POCKETPRO_NYL_VERSION:-latest})..."
  if ! docker compose "${COMPOSE_ARGS[@]}" pull; then
    echo ""
    echo "WARN: Registry pull failed (private GHCR or no release yet)."
    echo "      Retry with: BUILD_LOCAL=1 ./scripts/deploy-production.sh"
    exit 1
  fi
fi

echo "Starting stack..."
docker compose "${COMPOSE_ARGS[@]}" up -d

echo ""
docker compose "${COMPOSE_ARGS[@]}" ps
echo ""
echo "Health: curl -fsS https://${DOMAIN:-127.0.0.1}/api/health || curl -fsS http://127.0.0.1:${FRONTEND_PORT:-3000}/api/health"