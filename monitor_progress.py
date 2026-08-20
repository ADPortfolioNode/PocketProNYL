#!/usr/bin/env python3
"""
 PocketPro:NYL Project - Ingestion Progress Monitor
Real-time display of game data ingestion with expected vs actual progress
"""

import requests
import time
import sys
from datetime import datetime, timedelta

API_BASE = "http://127.0.0.1:5000"
STATUS_ENDPOINT = f"{API_BASE}/api/startup_status"

# ANSI color codes
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RED = '\033[91m'
RESET = '\033[0m'
BOLD = '\033[1m'

def format_time(seconds):
    """Convert seconds to readable format"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"

def print_header():
    """Print the monitor header"""
    print(f"\n{BOLD}{BLUE}{'='*70}{RESET}")
    print(f"{BOLD}{BLUE}PocketPro:NYL - Ingestion Progress Monitor{RESET}")
    print(f"{BOLD}{BLUE}{'='*70}{RESET}\n")

def print_progress_bar(progress, total, width=40):
    """Print a visual progress bar"""
    if total == 0:
        percentage = 0
        filled = 0
    else:
        percentage = (progress / total) * 100
        filled = int((progress / total) * width)
    
    bar = '█' * filled + '░' * (width - filled)
    return f"[{bar}] {progress}/{total} ({percentage:5.1f}%)"

def print_status(status_data, start_time):
    """Print formatted status"""
    elapsed = time.time() - start_time
    elapsed_str = format_time(elapsed)
    
    total_games = status_data.get('total', 0)
    progress = status_data.get('progress', 0)
    current_game = status_data.get('current_game')
    current_task = status_data.get('current_task')
    overall_status = status_data.get('status', 'unknown')
    games = status_data.get('games', {})
    # Get detailed progress for the currently ingesting game
    current_game_progress = status_data.get('current_game_progress', 0)
    current_game_total = status_data.get('current_game_total', 0)
    
    # Calculate stats
    game_values = games.values()
    completed = sum(1 for v in game_values if isinstance(v, dict) and v.get('status') == 'completed')
    failed = sum(1 for v in game_values if isinstance(v, dict) and 'failed' in str(v.get('status')))
    pending = total_games - completed - failed
    
    # Print main status
    if overall_status == 'completed':
        status_color = GREEN
        status_text = 'COMPLETE ✓'
    elif overall_status == 'ingesting':
        status_color = YELLOW
        status_text = 'INGESTING...'
    else:
        status_color = BLUE
        status_text = overall_status.upper()
    
    print(f"{status_color}{BOLD}Status:{RESET} {status_color}{status_text}{RESET}")
    print(f"{BOLD}Elapsed:{RESET} {elapsed_str}")
    print()
    
    # Print progress bar
    print(f"{BOLD}Overall Progress (Games):{RESET}")
    print(print_progress_bar(progress, total_games))
    print()
    
    # Print stats
    print(f"{BOLD}Game Stats:{RESET}")
    print(f"  {GREEN}✓ Completed:{RESET} {completed}/{total_games}")
    print(f"  {YELLOW}⟳ Pending:{RESET}   {pending}/{total_games}")
    if failed > 0:
        print(f"  {RED}✗ Failed:{RESET}    {failed}/{total_games}")
    print()
    
    # Print current game
    if current_game and current_game != "N/A":
        print(f"{BOLD}Currently Processing:{RESET}")
        print(f"  Game: {YELLOW}{current_game.upper()}{RESET}")
        if current_task:
            print(f"  Task: {current_task}")
        # Add a progress bar for the current game's rows
        if current_game_total > 0:
            print(f"  Rows: {print_progress_bar(current_game_progress, current_game_total)}")
    print()
    
    # Print game-by-game status
    if games:
        print(f"{BOLD}Game Status Details:{RESET}")
        for game_name in sorted(games.keys()):
            game_state = games.get(game_name, {})
            game_status = game_state.get('status', 'unknown') if isinstance(game_state, dict) else game_state

            if game_status == 'completed':
                symbol = f"{GREEN}✓{RESET}"
            elif game_status in ('pending', 'queued'):
                symbol = f"{YELLOW}⟳{RESET}"
            elif 'failed' in str(game_status):
                symbol = f"{RED}✗{RESET}"
            elif game_status == 'ingesting':
                symbol = f"{YELLOW}→{RESET}"
            else:
                symbol = "?"
            
            # Highlight current game
            if game_name == current_game and overall_status == 'ingesting':
                print(f"  {symbol} {BOLD}{game_name.upper()}{RESET} ({game_status})")
            else:
                print(f"  {symbol} {game_name.upper()}")

def monitor():
    """Main monitoring loop"""
    print_header()
    print(f"{BLUE}Connecting to API: {STATUS_ENDPOINT}{RESET}\n")
    
    start_time = time.time()
    last_progress = -1
    retry_count = 0
    max_retries = 30  # 30 retries * 2 sec = 60 seconds max wait
    
    while True:
        try:
            response = requests.get(STATUS_ENDPOINT, timeout=5)
            response.raise_for_status()
            status_data = response.json()
            retry_count = 0
            
            # Clear screen and print status
            sys.stdout.write('\033[2J\033[H')  # Clear screen
            print_header()
            print_status(status_data, start_time)
            
            # Check if complete
            if status_data.get('status') == 'completed':
                elapsed = time.time() - start_time
                print(f"\n{GREEN}{BOLD}✓ Ingestion Complete!{RESET}")
                print(f"  Total time: {format_time(elapsed)}")
                print(f"  Games ingested: {status_data.get('progress', 0)}/{status_data.get('total', 0)}")
                break
            
            # Update last progress
            current_progress = status_data.get('progress', 0)
            if current_progress != last_progress:
                last_progress = current_progress
            
            # Sleep before next poll
            time.sleep(2)
            
        except requests.exceptions.ConnectionError:
            retry_count += 1
            if retry_count == 1:
                sys.stdout.write('\033[2J\033[H')  # Clear screen
                print_header()
            print(f"{YELLOW}⚠ Waiting for API to be ready ({retry_count}/{max_retries})...{RESET}")
            if retry_count >= max_retries:
                print(f"{RED}✗ Could not connect to API after {max_retries * 2} seconds{RESET}")
                print(f"  Make sure: docker-compose up -d --build")
                break
            time.sleep(2)
        except Exception as e:
            sys.stdout.write('\033[2J\033[H')  # Clear screen
            print_header()
            print(f"{RED}✗ Error: {str(e)}{RESET}")
            time.sleep(2)

if __name__ == '__main__':
    try:
        monitor()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Monitoring stopped by user{RESET}\n")
        sys.exit(0)
