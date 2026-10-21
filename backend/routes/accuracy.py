"""Real-world accuracy routes: grade served suggestions against real draws."""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from services.accuracy_report import build_accuracy_report
from services.suggestion_ledger import suggestion_ledger
from utils.validation import _require_game_key

router = APIRouter()


@router.get("/api/accuracy_report")
async def accuracy_report(
    game: str = Query(..., description="Game key, e.g. take5"),
    last: int = Query(3, ge=1, le=50, description="How many of the most recent served suggestions to show"),
):
    """Per-suggestion hits vs chance, prize tier, exact p-values, pooled results, backtest gap."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await asyncio.to_thread(build_accuracy_report, game_key, last)
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}


@router.get("/api/suggestion_ledger")
async def suggestion_ledger_entries(
    game: str = Query(...),
    limit: int = Query(25, ge=1, le=500),
):
    """Raw ledger entries for one game (newest first). The ledger is append-only."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    entries = await asyncio.to_thread(suggestion_ledger.read_suggestions, game_key)
    entries.sort(key=lambda e: float(e.get("served_at_epoch") or 0), reverse=True)
    return {"status": "ok", "game": game_key, "total": len(entries), "entries": entries[:limit]}
