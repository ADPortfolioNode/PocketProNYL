#!/bin/bash
# Mensa Project - Ingestion Progress Monitor
# Real-time display of game data ingestion progress

API_BASE="http://127.0.0.1:5000"
STATUS_ENDPOINT="$API_BASE/api/startup_status"
REFRESH_INTERVAL=2
START_TIME=$(date +%s)

format_time() {
    local seconds=$1
    if (( seconds < 60 )); then
        echo "${seconds}s"
    elif (( seconds < 3600 )); then
        echo "$((seconds / 60))m $((seconds % 60))s"
    else
        echo "$((seconds / 3600))h $((( seconds % 3600 ) / 60))m"
    fi
}

print_progress_bar() {
    local progress=$1
    local total=$2
    local width=40
    
    if (( total == 0 )); then
        local percentage=0
        local filled=0
    else
        local percentage=$((progress * 100 / total))
        local filled=$((progress * width / total))
    fi
    
    local empty=$((width - filled))
    printf "["
    printf "%.0s█" $(seq 1 $filled)
    printf "%.0s░" $(seq 1 $empty)
    printf "] %d/%d (%d%%)\n" "$progress" "$total" "$percentage"
}

print_status() {
    local json=$1
    local current_time=$(date +%s)
    local elapsed=$((current_time - START_TIME))
    local elapsed_str=$(format_time $elapsed)
    
    clear
    echo ""
    echo "========================================================================"
    echo "POCKETPRO:NYL PROJECT - INGESTION PROGRESS MONITOR"
    echo "========================================================================"
    echo ""
    
    # Extract fields
    local status=$(echo "$json" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    local progress=$(echo "$json" | grep -o '"progress":[0-9]*' | cut -d':' -f2)
    local total=$(echo "$json" | grep -o '"total":[0-9]*' | cut -d':' -f2)
    local current_game=$(echo "$json" | grep -o '"current_game":"[^"]*"' | cut -d'"' -f4)
    local current_task=$(echo "$json" | grep -o '"current_task":"[^"]*"' | cut -d'"' -f4)
    
    # Status
    echo "Status: $status"
    echo "Elapsed: $elapsed_str"
    echo ""
    
    # Progress bar
    echo "Overall Progress:"
    print_progress_bar "$progress" "$total"
    echo ""
    
    # Current game
    if [ -n "$current_game" ]; then
        echo "Currently Processing:"
        echo "  Game: $(echo $current_game | tr a-z A-Z)"
        if [ -n "$current_task" ]; then
            echo "  Task: $current_task"
        fi
        echo ""
    fi
    
    # Game status details
    echo "Game Status:"
    echo "$json" | grep -o '"[a-z0-9]*":"[^"]*"' | while read -r item; do
        local key=$(echo "$item" | cut -d'"' -f2)
        local value=$(echo "$item" | cut -d'"' -f4)
        
        # Skip system fields
        case "$key" in
            status|progress|total|current_game|current_task)
                continue
                ;;
            *)
                if [ -n "$value" ]; then
                    local symbol="✓"
                    [ "$value" = "pending" ] && symbol="⟳"
                    [ "$value" = "failed" ] && symbol="✗"
                    echo "  $symbol $key: $value"
                fi
                ;;
        esac
    done
    
    echo ""
}

# Main loop
echo "Connecting to API: $STATUS_ENDPOINT"
echo ""

retry_count=0
max_retries=30

while true; do
    response=$(curl -s "$STATUS_ENDPOINT" 2>&1)
    
    if [[ $response == *"error"* ]] || [[ -z "$response" ]]; then
        retry_count=$((retry_count + 1))
        clear
        echo ""
        echo "========================================================================"
        echo "WAITING FOR API..."
        echo "========================================================================"
        echo ""
        echo "⚠ Waiting for API to be ready ($retry_count/$max_retries)..."
        echo ""
        echo "Make sure services are running:"
        echo "  docker-compose ps"
        echo "  docker-compose up -d"
        
        if (( retry_count >= max_retries )); then
            echo ""
            echo "✗ Could not connect to API after $((max_retries * 2)) seconds"
            exit 1
        fi
        
        sleep $REFRESH_INTERVAL
        continue
    fi
    
    retry_count=0
    print_status "$response"
    
    # Check if complete
    if echo "$response" | grep -q '"status":"completed"'; then
        current_time=$(date +%s)
        elapsed=$((current_time - START_TIME))
        elapsed_str=$(format_time $elapsed)
        
        echo "✓ Ingestion Complete!"
        echo "  Total time: $elapsed_str"
        exit 0
    fi
    
    sleep $REFRESH_INTERVAL
done
