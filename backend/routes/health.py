from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from state.ingest_state import (
    get_manual_ingest_state,
    enqueue_manual_ingest,
    set_manual_ingest_state,
    get_startup_state,
)
from config import GAME_CONFIGS

router = APIRouter()

class StartupInitRequest(BaseModel):
    force: bool = False

APP_IS_READY = False


def get_ingestion_status():
    """Merge background startup_state with per-game manual queue state."""
    all_games = list(GAME_CONFIGS.keys())
    ss = get_startup_state() or {}
    startup_games = ss.get("games") or {}

    game_statuses = {}
    completed_count = 0
    current_game = ss.get("current_game") or None
    current_task = ss.get("current_task") or None
    current_game_progress = int(ss.get("current_game_rows_fetched") or 0)
    current_game_total = int(ss.get("current_game_rows_total") or 0)
    overall_status = str(ss.get("status") or "pending").lower()
    active_game_found = False

    for game in all_games:
        state = dict(startup_games.get(game) or {})
        manual = get_manual_ingest_state(game) or {}
        if manual:
            for key, value in manual.items():
                if value is not None:
                    state[key] = value
        if not state.get("status"):
            state["status"] = "pending"
        game_statuses[game] = state

        game_status = str(state.get("status") or "pending").lower()
        if game_status in ("completed",):
            completed_count += 1
        elif game_status in ("ingesting", "running", "queued", "fetching") and not active_game_found:
            if overall_status in ("pending", "ready", "unknown", ""):
                overall_status = "ingesting"
            current_game = current_game or game
            current_task = current_task or state.get("current_task") or game_status
            current_game_progress = int(state.get("rows_fetched") or current_game_progress or 0)
            current_game_total = int(state.get("total_rows") or current_game_total or 0)
            active_game_found = True

    if completed_count == len(all_games) and len(all_games) > 0:
        overall_status = "completed"
    elif active_game_found and overall_status in ("pending", "ready"):
        overall_status = "ingesting"

    progress = ss.get("progress")
    try:
        progress_val = float(progress) if progress is not None else float(completed_count)
    except (TypeError, ValueError):
        progress_val = float(completed_count)

    return {
        "status": overall_status,
        "progress": progress_val,
        "total": len(all_games),
        "current_game": current_game,
        "current_task": current_task,
        "current_game_progress": current_game_progress,
        "current_game_total": current_game_total,
        "current_game_rows_fetched": current_game_progress,
        "current_game_rows_total": current_game_total,
        "games": game_statuses,
        "available_games": all_games,
        "elapsed_s": ss.get("elapsed_s") or 0,
    }


@router.get("/api/health", tags=["Health"])
def get_health():
    if not APP_IS_READY:
        raise HTTPException(status_code=503, detail="Service Unavailable: Initializing")
    return {"status": "healthy"}


@router.get("/api/startup_status", tags=["Health"])
def get_startup_status():
    return get_ingestion_status()


@router.post("/api/startup_init", tags=["Health"])
def trigger_startup_init(request: StartupInitRequest):
    from state.manual_ingest_worker import _start_manual_ingest_worker_if_needed

    games_to_ingest = list(GAME_CONFIGS.keys())
    for game_key in games_to_ingest:
        seq = enqueue_manual_ingest(game_key, force=request.force)
        set_manual_ingest_state(game_key, {
            "status": "queued",
            "seq": seq,
        })
    _start_manual_ingest_worker_if_needed()
    return {"status": "started", "message": f"Queued ingestion for {len(games_to_ingest)} games."}
