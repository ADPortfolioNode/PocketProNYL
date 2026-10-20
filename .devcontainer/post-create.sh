#!/usr/bin/env bash
# Codespaces / dev container setup for PocketPro:NYL.
# Installs backend + frontend deps so tests run right away.
# Run the full stack with:  docker compose up --build   (dashboard on port 3000)
set -euo pipefail
python -m pip install --upgrade pip
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
(cd frontend && npm ci)
[ -f .env ] || cp .env.example .env
echo "Ready. Tests: DATA_DIR=/tmp/pocketpro pytest tests | Frontend: cd frontend && npm test | Stack: docker compose up --build"
