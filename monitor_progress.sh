#!/bin/bash
# PocketPro:NYL Project - Ingestion Progress Monitor
# Real-time display of game data ingestion progress

API_BASE="http://127.0.0.1:5000"
STATUS_ENDPOINT="$API_BASE/api/startup_status"
PROGRESS_ENDPOINT="$API_BASE/api/ingest_progress"
REFRESH_INTERVAL=2
START_TIME=$(date +%s)

# ANSI color codes
RESET='\033[0m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'

format_time() {
    local seconds=$1
    if (( seconds < 60 )); then
        echo "${seconds}s"
    elif (( seconds < 3600 )); then
        printf "%dm %ds" "$((seconds / 60))" "$((seconds % 60))"
    else
        printf "%dh %dm" "$((seconds / 3600))" "$(((seconds % 3600) / 60))"
    fi
}

print_progress_bar() {
    local progress=$1
    local total=$2
    local label="$3"
    local width=30
    
    if (( total == 0 )); then
        local percentage=0
        local filled=0
    else
        # Use awk for floating point arithmetic
        local percentage
        percentage=$(awk -v p="$progress" -v t="$total" 'BEGIN {if (t>0) printf "%.0f", (p/t)*100; else print 0}')
        local filled
        filled=$(awk -v p="$progress" -v w="$width" -v t="$total" 'BEGIN {if (t>0) printf "%.0f", (p/t)*w; else print 0}')
    fi
    
    local empty=$((width - filled))
    
    # Build the bar string
    local bar=""
    for ((i=0; i<filled; i++)); do bar+="█"; done
    for ((i=0; i<empty; i++)); do bar+="░"; done

    printf "${label}%-32s ${YELLOW}[%s]${RESET} %'d/%'d (${percentage}%%)\n" "" "${bar}" "${progress}" "${total}"
}

print_status() {
    local json=$1
    local draw_json=$2
    local current_time=$(date +%s)
    local elapsed=$((current_time - START_TIME))
    local elapsed_str=$(format_time $elapsed)
    
    clear
    echo ""
    echo -e "${CYAN}========================================================================"
    echo -e "          POCKETPRO:NYL - REAL-TIME INGESTION MONITOR"
    echo -e "========================================================================${RESET}"
    echo ""
    
    # Use python for robust parsing
    local parsed_status
    parsed_status=$(echo "$json" | python -c 'import sys, json; data=json.load(sys.stdin); print(f"{data.get(\"status\", \"pending\")}|{data.get(\"progress\", 0)}|{data.get(\"total\", 0)}|{data.get(\"current_game\", \"N/A\")}|{data.get(\"current_task\", \"N/A\")}")' 2>/dev/null || echo "pending|0|0|N/A|N/A")
    IFS='|' read -r status progress total current_game current_task <<< "$parsed_status"

    local games_json
    games_json=$(echo "$json" | python -c 'import sys, json; print(json.dumps(json.load(sys.stdin).get("games", {})))' 2>/dev/null || echo '{}')
    
    # Overall Status
    echo -e "  Overall Status: ${YELLOW}${status}${RESET}"
    echo -e "  Elapsed Time:   ${elapsed_str}"
    echo ""
    
    # Overall Progress Bar (Games)
    print_progress_bar "$progress" "$total" "  Games Progress: "
    echo ""
    
    # Current Game and Draw Progress
    if [[ -n "$current_game" && "$current_game" != "N/A" ]]; then
        echo -e "  ${CYAN}Currently Processing: ${YELLOW}$(echo "$current_game" | tr 'a-z' 'A-Z')${RESET} ${GRAY}(${current_task:-task unknown})${RESET}"
        
        local parsed_draw_progress
        parsed_draw_progress=$(echo "$draw_json" | python -c 'import sys, json; data=json.load(sys.stdin); print(f"{data.get(\"rows_fetched\", 0)}|{data.get(\"total_rows\", 0)}")' 2>/dev/null || echo "0|0")
        IFS='|' read -r rows_fetched total_rows <<< "$parsed_draw_progress"

        if (( total_rows > 0 )); then
            print_progress_bar "$rows_fetched" "$total_rows" "    Draws Fetched:"
        else
            echo -e "    ${GRAY}Draw progress not available...${RESET}"
        fi
        echo ""
    fi
    
    # Game Status Details
    echo -e "  ${CYAN}Game-by-Game Status:${RESET}"
    # Use python to iterate and format, much more robust than bash loops + grep
    echo "$games_json" | python -c '
import sys, json
games = json.load(sys.stdin)
if not games:
    print("    (no game status available yet)")
else:
    for game, g_status in sorted(games.items()):
        symbol, color = "？", "\033[0;90m" # Gray
        if g_status == "completed": symbol, color = "✓", "\033[0;32m" # Green
        elif g_status == "pending": symbol, color = "⟳", "\033[1;33m" # Yellow
        elif "fail" in g_status:    symbol, color = "✗", "\033[0;31m" # Red
        
        print(f"    {color}{symbol} {game.upper():<15}{g_status}{color}\033[0m")
'
    echo ""
}

# Main loop
echo -e "${CYAN}Connecting to API: ${API_BASE}${RESET}"
echo ""

retry_count=0
max_retries=30

while true; do
    # Fetch overall status
    response=$(curl -s -f "$STATUS_ENDPOINT" 2>&1)
    curl_exit_code=$?
    
    if [[ $curl_exit_code -ne 0 ]]; then
        retry_count=$((retry_count + 1))
        clear
        echo ""
        echo -e "${YELLOW}========================================================================"
        echo -e "                          WAITING FOR API..."
        echo -e "========================================================================${RESET}"
        echo ""
        echo -e "${YELLOW}⚠ Waiting for API to be ready ($retry_count/$max_retries)...${RESET}"
        echo ""
        echo -e "  Make sure services are running:"
        echo -e "  ${GRAY}./start.sh${RESET}"
        
        if (( retry_count >= max_retries )); then
            echo ""
            echo -e "${RED}✗ Could not connect to API after $((max_retries * REFRESH_INTERVAL)) seconds.${RESET}"
            exit 1
        fi
        
        sleep $REFRESH_INTERVAL
        continue
    fi
    
    retry_count=0
    
    # Fetch draw-level progress for the current game
    current_game_for_url=$(echo "$response" | python -c 'import sys, json; print(json.load(sys.stdin).get("current_game", ""))' 2>/dev/null)
    draw_response="{}"
    if [[ -n "$current_game_for_url" && "$current_game_for_url" != "N/A" ]]; then
        draw_response=$(curl -s -f "${PROGRESS_ENDPOINT}?game=${current_game_for_url}" 2>/dev/null || echo "{}")
    fi

    print_status "$response" "$draw_response"
    
    # Check if complete
    if echo "$response" | grep -q '"status":"completed"'; then
        current_time=$(date +%s)
        elapsed=$((current_time - START_TIME))
        elapsed_str=$(format_time $elapsed)
        echo ""
        echo -e "${GREEN}✓ Ingestion Complete!${RESET}"
        echo -e "  Total time: ${elapsed_str}"
        exit 0
    fi
    
    sleep $REFRESH_INTERVAL
done
