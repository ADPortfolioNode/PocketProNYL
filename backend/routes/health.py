from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import time
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

_STATUS_RANK = {
    "pending": 0,
    "ready": 1,
    "queued": 1,
    "error": 2,
    "failed": 2,
    "fetching": 3,
    "ingesting": 3,
    "running": 3,
    "completed": 4,
}


def _status_rank(value) -> int:
    return _STATUS_RANK.get(str(value or "pending").lower(), 0)


def get_ingestion_status():
    """Merge background startup_state with per-game manual queue state."""
    from state.draw_counts import get_all_draw_counts

    all_games = list(GAME_CONFIGS.keys())
    ss = get_startup_state() or {}
    startup_games = ss.get("games") or {}
    draw_counts = get_all_draw_counts(all_games)

    game_statuses = {}
    completed_count = 0
    populated_count = 0
    current_game = ss.get("current_game") or None
    current_task = ss.get("current_task") or None
    current_game_progress = int(ss.get("current_game_rows_fetched") or 0)
    current_game_total = int(ss.get("current_game_rows_total") or 0)
    overall_status = str(ss.get("status") or "pending").lower()
    active_game_found = False

    for game in all_games:
        state = dict(startup_games.get(game) or {})
        draw_count = int(draw_counts.get(game, 0) or 0)
        state["draw_count"] = draw_count
        if draw_count > 0:
            populated_count += 1
            # Surface persisted history even if this process never ran ingest.
            if not state.get("status") or str(state.get("status")).lower() in ("pending", "ready"):
                state["status"] = "completed"
                state["current_task"] = state.get("current_task") or "existing_data"
            if int(state.get("rows_fetched") or 0) <= 0:
                state["rows_fetched"] = draw_count
            if int(state.get("total_rows") or 0) <= 0:
                state["total_rows"] = draw_count
        manual = get_manual_ingest_state(game) or {}
        if manual:
            man_status = str(manual.get("status") or "").lower()
            start_status = str(state.get("status") or "").lower()
            if _status_rank(man_status) > _status_rank(start_status):
                state["status"] = man_status
            for key, value in manual.items():
                if key == "status" or value is None:
                    continue
                if key in ("rows_fetched", "total_rows") and state.get(key):
                    try:
                        state[key] = max(int(state.get(key) or 0), int(value or 0))
                        continue
                    except (TypeError, ValueError):
                        pass
                if key not in state or state.get(key) in (None, "", 0):
                    state[key] = value
        if not state.get("status"):
            state["status"] = "pending"

        rows_fetched = int(state.get("rows_fetched") or state.get("current_game_rows_fetched") or 0)
        total_rows = int(state.get("total_rows") or state.get("current_game_rows_total") or 0)
        if rows_fetched > total_rows:
            total_rows = rows_fetched
        state["rows_fetched"] = rows_fetched
        state["total_rows"] = total_rows
        if total_rows > 0:
            state["percent"] = round(min(100.0, (rows_fetched / total_rows) * 100.0), 1)
        elif str(state.get("status")).lower() == "completed":
            state["percent"] = 100.0
        else:
            state["percent"] = 0.0
        game_statuses[game] = state

        game_status = str(state.get("status") or "pending").lower()
        if game_status == "completed":
            completed_count += 1
        elif game_status in ("ingesting", "running", "queued", "fetching") and not active_game_found:
            if overall_status in ("pending", "ready", "unknown", ""):
                overall_status = "ingesting"
            if not current_game:
                current_game = game
            current_task = current_task or state.get("current_task") or game_status
            if game == current_game:
                current_game_progress = max(current_game_progress, rows_fetched)
                current_game_total = max(current_game_total, total_rows)
            active_game_found = True

    if current_game and current_game in game_statuses:
        cur = game_statuses[current_game]
        if _status_rank(cur.get("status")) < _status_rank("ingesting"):
            cur["status"] = "ingesting"
        current_game_progress = max(current_game_progress, int(cur.get("rows_fetched") or 0))
        current_game_total = max(current_game_total, int(cur.get("total_rows") or 0))
        if current_game_progress > current_game_total:
            current_game_total = current_game_progress
            cur["total_rows"] = current_game_total
        cur["rows_fetched"] = max(int(cur.get("rows_fetched") or 0), current_game_progress)

    if completed_count == len(all_games) and len(all_games) > 0:
        overall_status = "completed"
    elif populated_count == len(all_games) and len(all_games) > 0 and not active_game_found:
        # All games already have persisted Chroma history.
        overall_status = "completed"
        completed_count = populated_count
    elif active_game_found and overall_status in ("pending", "ready"):
        overall_status = "ingesting"

    progress = ss.get("progress")
    try:
        progress_val = float(progress) if progress is not None else float(completed_count)
    except (TypeError, ValueError):
        progress_val = float(completed_count)

    started_at = ss.get("started_at")
    elapsed_s = ss.get("elapsed_s")
    if started_at and not elapsed_s:
        try:
            elapsed_s = max(0.0, time.time() - float(started_at))
        except (TypeError, ValueError):
            elapsed_s = 0
    elapsed_s = float(elapsed_s or 0)

    total = len(all_games) or 1
    denom = current_game_total if current_game_total > 0 else 0
    row_frac = (current_game_progress / denom) if denom else 0.0
    row_frac = max(0.0, min(1.0, row_frac))
    if progress_val > completed_count:
        percent_complete = max(0.0, min(100.0, (progress_val / total) * 100.0))
    else:
        percent_complete = max(0.0, min(100.0, ((completed_count + row_frac) / total) * 100.0))

    return {
        "status": overall_status,
        "progress": progress_val,
        "percent_complete": round(percent_complete, 1),
        "total": len(all_games),
        "current_game": current_game,
        "current_task": current_task,
        "current_game_progress": current_game_progress,
        "current_game_total": current_game_total,
        "current_game_rows_fetched": current_game_progress,
        "current_game_rows_total": current_game_total,
        "games": game_statuses,
        "available_games": all_games,
        "elapsed_s": elapsed_s,
        "completed_games": completed_count,
        "populated_games": populated_count,
        "draw_counts": draw_counts,
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
    from state.draw_counts import set_draw_count
    from services.chroma_client import chroma_client

    ss = get_startup_state() or {}
    if str(ss.get("status") or "").lower() == "ingesting" and not request.force:
        return {"status": "already_running", "message": "Ingestion already in progress."}

    games_to_ingest = list(GAME_CONFIGS.keys())
    skipped: list[dict] = []
    queued: list[str] = []

    # Prefer live Chroma totals so rebuilds keep history and FE can verify population.
    live_counts: dict[str, int] = {}
    try:
        snapshots = chroma_client.get_collections_snapshot(
            games_to_ingest,
            timeout_seconds=10.0,
            refresh=True,
        )
        for snap in snapshots or []:
            name = snap.get("name")
            count = int(snap.get("count") or 0)
            if name:
                live_counts[name] = count
                if count > 0:
                    set_draw_count(name, count)
    except Exception as exc:
        print(f"⚠ startup_init could not refresh Chroma counts: {exc}")

    for game_key in games_to_ingest:
        existing_status = str(
            (get_startup_state() or {}).get("games", {}).get(game_key, {}).get("status") or ""
        ).lower()
        existing_draws = int(live_counts.get(game_key, 0) or 0)

        if not request.force and existing_draws > 0:
            try:
                from state.ingest_state import set_game_status, get_startup_state as _gss
                set_game_status(game_key, "completed")
                games_map = (_gss() or {}).setdefault("games", {})
                game_state = games_map.setdefault(game_key, {})
                game_state["status"] = "completed"
                game_state["rows_fetched"] = existing_draws
                game_state["total_rows"] = existing_draws
                game_state["current_task"] = "skipped_existing"
                game_state["error"] = None
            except Exception as exc:
                print(f"⚠ Could not mark {game_key} as populated: {exc}")
            skipped.append({"game": game_key, "draw_count": existing_draws, "reason": "already_populated"})
            continue

        if existing_status in ("ingesting", "fetching", "running", "completed") and not request.force:
            skipped.append({"game": game_key, "draw_count": existing_draws, "reason": f"status_{existing_status}"})
            continue

        seq = enqueue_manual_ingest(game_key, force=request.force)
        set_manual_ingest_state(game_key, {
            "status": "queued",
            "seq": seq,
        })
        queued.append(game_key)

    if queued:
        _start_manual_ingest_worker_if_needed()

    if not queued and skipped:
        return {
            "status": "already_populated",
            "message": f"All {len(skipped)} games already have draw history in Chroma; skipped re-ingest.",
            "queued": [],
            "skipped": skipped,
            "force": bool(request.force),
        }

    return {
        "status": "started" if queued else "noop",
        "message": f"Queued ingestion for {len(queued)} games; skipped {len(skipped)} already populated.",
        "queued": queued,
        "skipped": skipped,
        "force": bool(request.force),
    }
