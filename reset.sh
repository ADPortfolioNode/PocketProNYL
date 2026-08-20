#!/usr/bin/env bash
set -euo pipefail

# DANGER: This script will completely wipe all application data.
# It stops and removes all containers, networks, and Docker volumes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Only prompt if not called with a force flag like --yes or -y
if [[ "$*" != *"--yes"* && "$*" != *"-y"* ]]; then
    echo "WARNING: This will permanently delete all data for the PocketPro:NYL project."
    echo "This includes the ChromaDB vector store and any cached ingestion files."
    read -p "Are you sure you want to continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

echo "Stopping and removing containers, networks, and volumes..."
if command -v docker-compose &> /dev/null; then
    docker-compose down -v
elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
    docker compose down -v
fi

echo "Pruning unused Docker resources..."
docker system prune -f

echo "✓ All data has been cleared. You can now run './start.sh --build' for a completely fresh start."
echo "Initiating full build and data refresh..."
exec "${SCRIPT_DIR}/start.sh" --build