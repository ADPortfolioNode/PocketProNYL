from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
# The functions for startup status and queuing were moved to ingest_state
from state.ingest_state import get_manual_ingest_state, enqueue_manual_ingest, set_manual_ingest_state
from config import GAME_CONFIGS

router = APIRouter()

class StartupInitRequest(BaseModel):
    force: bool = False

# This global flag will be set to True by the lifespan startup event when ready.
APP_IS_READY = False

def get_ingestion_status():
    """
    Aggregates status from all games to produce a global startup status.
    This replaces the old function that was removed from ingestion_worker.
    """
    all_games = list(GAME_CONFIGS.keys())
    total = len(all_games)
    completed_count = 0
    current_game = "N/A"
    current_task = "N/A"
    overall_status = "pending"
    current_game_progress = 0
    current_game_total = 0
    
    game_statuses = {}
    active_game_found = False

    for game in all_games:
        state = get_manual_ingest_state(game)
        game_status = state.get("status", "pending")
        # Store the full state object for richer information in the response
        game_statuses[game] = state
        
        if game_status == "completed":
            completed_count += 1
        elif game_status in ("ingesting", "running", "queued") and not active_game_found:
            overall_status = "ingesting"
            current_game = game
            current_task = state.get("current_task", "fetching")
            # Expose the detailed row-level progress for the active game
            current_game_progress = state.get("rows_fetched", 0)
            current_game_total = state.get("total_rows", 0)
            active_game_found = True

    if not active_game_found and completed_count == total:
        overall_status = "completed"

    return {
        "status": overall_status,
        "progress": completed_count,
        "total": total,
        "current_game": current_game,
        "current_task": current_task,
        "current_game_progress": current_game_progress,
        "current_game_total": current_game_total,
        "games": game_statuses,
    }

@router.get("/api/health", tags=["Health"])
def get_health():
    """
    Checks if the application is healthy and ready to serve requests.
    Returns 503 Service Unavailable until the application startup is complete.
    """
    if not APP_IS_READY:
        raise HTTPException(status_code=503, detail="Service Unavailable: Initializing")
    return {"status": "healthy"}

@router.get("/api/startup_status", tags=["Health"])
def get_startup_status():
    """Returns the current status of the background data ingestion process."""
    return get_ingestion_status()

@router.post("/api/startup_init", tags=["Health"])
def trigger_startup_init(request: StartupInitRequest):
    """
    Initializes the background data ingestion for all configured games.
    This is typically called once by the startup script.
    """

    games_to_ingest = list(GAME_CONFIGS.keys())
    for game_key in games_to_ingest:
        seq = enqueue_manual_ingest(game_key, force=request.force)
        # Set the initial state to 'queued'. This is critical for the startup
        # monitor to detect that work has been scheduled.
        set_manual_ingest_state(game_key, {
            "status": "queued",
            "seq": seq
        })

    return {"status": "started", "message": f"Queued ingestion for {len(games_to_ingest)} games."}