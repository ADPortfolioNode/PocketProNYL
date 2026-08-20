#!/usr/bin/env bash
set -euo pipefail

# Script to force a full re-ingestion of all game data by calling the
# /api/ingest endpoint with `force: true` for each game. This bypasses all
# local caches and downloads fresh data from the source APIs. It is the
# recommended way to ensure all draws for all games are fully updated.
API_BASE="${POCKETPRO_API_BASE:-http://127.0.0.1:5000}"
GAMES=(
    "take5" "pick3" "powerball" "megamillions"
    "pick10" "cash4life" "quickdraw" "nylotto"
)

echo "Waiting for API to be ready at ${API_BASE}/api/health..."
for i in {1..30}; do
    if curl -s -f "${API_BASE}/api/health" > /dev/null; then
        echo "✓ API is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "✗ ERROR: API did not become healthy after 60 seconds." >&2
        exit 1
    fi
    echo "  ... waiting (attempt $i/30)"
    sleep 2
done

echo "Forcing re-ingestion for all games against API base: $API_BASE"

for game in "${GAMES[@]}"; do
    echo "--- Triggering re-ingestion for ${game} ---"
    curl -s -X POST -H "Content-Type: application/json" \
         -d "{\"game\": \"${game}\", \"force\": true}" \
         "${API_BASE}/api/ingest"
    echo "" # for newline
    sleep 1 # Small delay between requests to not overwhelm the server
done

echo ""
echo "All re-ingestion tasks have been queued. Monitoring progress..."
echo "(This may take several minutes. Press Ctrl+C to stop monitoring)."

max_wait_minutes=45
max_wait_seconds=$((max_wait_minutes * 60))
elapsed=0
interval=5

while [ "${elapsed}" -lt "${max_wait_seconds}" ]; do
    # Use curl to fetch status, fail silently if server not ready
    status_json=$(curl -s -f "${API_BASE}/api/startup_status" || echo "{}")

    # Use python for robust JSON parsing to get detailed status
    parsed_status=$(echo "$status_json" | python -c '
import sys, json
try:
    data=json.load(sys.stdin)
    print(f"{data.get(\"status\", \"pending\")}|{data.get(\"progress\", 0)}|{data.get(\"total\", 0)}|{data.get(\"current_game\", \"N/A\")}|{data.get(\"current_game_progress\", 0)}|{data.get(\"current_game_total\", 0)}")
except (json.JSONDecodeError, IndexError):
    print("pending|0|0|N/A|0|0")' 2>/dev/null || echo "pending|0|0|N/A|0|0")
    
    IFS='|' read -r status progress total current_game current_game_progress current_game_total <<< "$parsed_status"

    if [[ "$status" == "completed" ]]; then
        echo "✓ All games re-ingested successfully in ${elapsed}s."
        exit 0
    fi

    echo "  ... Status: ${status} | Games: ${progress}/${total} | Current: ${current_game} (${current_game_progress}/${current_game_total}) | Elapsed: ${elapsed}s"
    sleep "${interval}"
    elapsed=$((elapsed + interval))
done

echo "✗ WARNING: Timed out waiting for re-ingestion to complete after ${max_wait_minutes} minutes." >&2
exit 1