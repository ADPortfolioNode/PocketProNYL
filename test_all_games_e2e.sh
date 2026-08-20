#!/usr/bin/env bash
set -euo pipefail

# === PocketPro:NYL Full End-to-End Workflow Test for All Games ===
# This script will:
# 1. Start the entire application stack using start.sh.
# 2. Wait for the initial data ingestion to complete.
# 3. Loop through all available games.
# 4. For each game, run the test_all_workflows.py script to:
#    - Ingest data (if not already present).
#    - Train a model.
#    - Generate a prediction.
# 5. Report a final summary of which games passed or failed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/resolve_host_ports.sh
. "${SCRIPT_DIR}/scripts/resolve_host_ports.sh"

# --- Configuration ---
if [ -z "${POCKETPRO_API_BASE:-}" ]; then
    echo "INFO: POCKETPRO_API_BASE not set, resolving from local Docker setup..."
    resolve_compose_host_ports || exit 1
    BACKEND_PORT="${BACKEND_HOST_PORT:-5000}"
    BIND_HOST="${DOCKER_BIND_HOST:-127.0.0.1}"
    # Export the variable so it's available to child processes (like the python script)
    export POCKETPRO_API_BASE="http://${BIND_HOST}:${BACKEND_PORT}"
fi
echo "INFO: Using API base: ${POCKETPRO_API_BASE}"
TEST_SCRIPT_PATH="${SCRIPT_DIR}/test_all_workflows.py"
declare -A ALL_GAME_RESULTS

# --- Helper Functions ---
RESET='\033[0m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'

step() {
    echo -e "\n${BLUE}==> $1${RESET}"
}

# --- Main Execution ---

# 1. Start the application stack
step "Starting application stack with start.sh..."
if ! "${SCRIPT_DIR}/start.sh" --build; then
    echo -e "${RED}ERROR: start.sh failed to bring up the application stack. Aborting test.${RESET}" >&2
    exit 1
fi
echo -e "${GREEN}✓ Application stack is up and running.${RESET}"

# 2. Wait for initial startup ingestion to complete
step "Waiting for initial background data ingestion to complete..."
max_wait=1200 # 20 minutes
elapsed=0
while [ $elapsed -lt $max_wait ]; do
    status_json=$(curl -s "${POCKETPRO_API_BASE}/api/startup_status" || echo "{}")

    # Use Python for robust JSON parsing to avoid issues with multiple 'status' keys
    parsed_status=$(echo "$status_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(f"{data.get(\"status\", \"unknown\")}|{data.get(\"progress\", 0)}|{data.get(\"total\", 0)}|{data.get(\"current_game\", \"none\")}")' 2>/dev/null || echo "error|0|0|none")
    IFS='|' read -r status progress total current_game <<< "$parsed_status"

    if [[ "$status" == "completed" ]]; then
        echo -e "${GREEN}✓ Initial ingestion complete.${RESET}"
        break
    fi
    
    # Display a clean, single line of progress
    echo -e "  ... Ingestion status: ${status} (${progress}/${total}). Current game: ${current_game}. Waiting... (${elapsed}s)"
    sleep 5
    elapsed=$((elapsed + 5))
done

if [[ "$status" != "completed" ]]; then
    echo -e "${YELLOW}WARNING: Initial ingestion did not complete within the timeout. Tests may be slow or fail.${RESET}" >&2
fi

# 3. Get the list of games
step "Fetching list of all games from the API..."
games_json=$(curl -s "${POCKETPRO_API_BASE}/api/games")
if ! echo "$games_json" | grep -q '"games":'; then
    echo -e "${RED}ERROR: Could not fetch the list of games from the API. Aborting.${RESET}" >&2
    echo "Response: $games_json"
    exit 1
fi
GAMES=($(echo "$games_json" | python -c 'import sys, json; print(" ".join(json.load(sys.stdin).get("games", [])))' 2>/dev/null || echo ""))
echo "Found games: ${GAMES[*]}"

# 4. Loop and test each game
step "Running end-to-end workflow test for each game..."
for game in "${GAMES[@]}"; do
    echo -e "\n${CYAN}--- Testing Game: ${game} ---${RESET}"
    
    # Run the Python test script for the specific game
    # The script returns 0 on success (no FAILs), 1 on failure
    if WORKFLOW_GAME="$game" python "$TEST_SCRIPT_PATH"; then
        echo -e "${GREEN}✓ Workflow for ${game} PASSED.${RESET}"
        ALL_GAME_RESULTS["$game"]="PASS"
    else
        echo -e "${RED}✗ Workflow for ${game} FAILED.${RESET}"
        ALL_GAME_RESULTS["$game"]="FAIL"
    fi
    echo -e "${CYAN}--------------------------${RESET}"
done

# 5. Final Summary
step "Final Test Summary"
echo "========================================"
PASSED_COUNT=0
FAILED_COUNT=0
for game in "${!ALL_GAME_RESULTS[@]}"; do
    result="${ALL_GAME_RESULTS[$game]}"
    if [[ "$result" == "PASS" ]]; then
        echo -e "- ${GREEN}[PASS]${RESET} ${game}"
        ((PASSED_COUNT++))
    else
        echo -e "- ${RED}[FAIL]${RESET} ${game}"
        ((FAILED_COUNT++))
    fi
done
echo "========================================"
echo -e "Total: ${GREEN}${PASSED_COUNT} passed${RESET}, ${RED}${FAILED_COUNT} failed${RESET}."

if [[ $FAILED_COUNT -gt 0 ]]; then
    echo -e "\n${RED}One or more game workflows failed. Please review the logs above.${RESET}"
    exit 1
else
    echo -e "\n${GREEN}All game workflows completed successfully!${RESET}"
    exit 0
fi