"""Pattern-search report: each signal's lift vs chance, p, BH-FDR q, drift, holdout verdict.

The scan runs in the background (``services.pattern_scheduler``): after ingests that
add draws, on first view, or on ``?refresh=1``. This endpoint never blocks on a scan;
it returns the latest cached result plus ``scan_status`` (fresh / running / stale).
"""

from fastapi import APIRouter, HTTPException, Query

from utils.validation import _require_game_key

router = APIRouter()


def build_report(game: str, limit: int | None = None, refresh: bool = False, history=None) -> dict:
    """Synchronous scan (CLI / tests). Reuses the cache unless the draws changed or refresh is set."""
    from services.pattern_scheduler import _fingerprint, load_cached, run_scan

    if history is not None and not refresh:
        cached = load_cached(game)
        if cached and cached.get("fingerprint") == _fingerprint(history):
            return {**cached, "cached": True}
    return {**run_scan(game, history=history, limit=limit), "cached": False}


def report_payload(game: str, refresh: bool = False, limit: int | None = None) -> dict:
    from prediction.patterns.auto import load_promoted, read_events
    from prediction.patterns.settings import load_settings
    from services.pattern_scheduler import get_scheduler, load_cached

    scheduler = get_scheduler()
    if refresh:
        scheduler.request(game, delay=0, trigger="manual_refresh", limit=limit)
    cached = load_cached(game)
    status = scheduler.status(game, cached)
    if cached is None and status["scan_status"] != "running" and not status.get("last_error"):
        scheduler.request(game, delay=0, trigger="first_view", limit=limit)
        status = scheduler.status(game, cached)
    body = cached or {"game": game, "status": "pending", "signals": [], "feature_signals": [], "passed_holdout": []}
    promoted = load_promoted(game).get("signals", {})
    return {
        **body,
        "cached": cached is not None,
        "scan_status": status["scan_status"],
        "scan": {k: status.get(k) for k in ("phase", "pending", "trigger", "runs", "last_error", "scheduled_for",
                                             "last_started_at", "last_finished_at")},
        "last_run_at": body.get("last_run_at"),
        "promoted_signals": sorted(promoted.keys()),
        "history": read_events(game, 50),
        "config": load_settings(game).to_dict(),
    }


@router.get("/api/pattern_report")
async def pattern_report(
    game: str = Query(...),
    refresh: bool = Query(False),
    limit: int | None = Query(None, ge=100, le=5000),
):
    """Latest per-signal report for one game plus its scan status; ``refresh=1`` forces a rerun."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return report_payload(game_key, refresh=refresh, limit=limit)
    except Exception as exc:  # never surface as a crash; the panel keeps polling
        return {"status": "error", "game": game_key, "message": str(exc), "signals": [], "scan_status": "stale"}
