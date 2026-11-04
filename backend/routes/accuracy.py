"""Real-world accuracy routes: grade served suggestions against real draws."""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.accuracy_report import build_accuracy_report
from services.accuracy_overview import build_accuracy_overview
from services.suggestion_ledger import suggestion_ledger
from utils.validation import _require_game_key

router = APIRouter()


class ReweightRequest(BaseModel):
    game: str


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


@router.get("/api/accuracy_overview")
async def accuracy_overview():
    """All games at once: graded counts, verdicts, reweight readiness.

    Read-only. Grades every game's ledger (ingested draws + verified
    fallback) and reports which games have reached the 30-unique-graded
    threshold for a reweight proposal. Quick Draw is excluded (draws
    every 4 minutes cannot be pinned by date).
    """
    try:
        return await asyncio.to_thread(build_accuracy_overview)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


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


def _graded_for_reweight(game_key: str):
    """Grade every served suggestion for a game (live draws + verified fallback)."""
    from services.suggestion_grader import grade_entries

    entries = sorted(suggestion_ledger.read_suggestions(game_key),
                     key=lambda e: float(e.get("served_at_epoch") or 0))
    reported = suggestion_ledger.read_feedback(game_key)
    graded, _info = grade_entries(game_key, entries, reported=reported)
    return graded


@router.get("/api/reweight_proposal")
async def reweight_proposal(game: str = Query(...)):
    """Read-only graded-reweight proposal: candidate weights or a hold verdict.

    Nothing is changed. The proposal states its evidence (unique graded
    tickets, per-strategy lifts and p-values) and its reasons for holding.
    """
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        from prediction.state.weight_store import WeightStore
        from services.graded_reweight import propose_reweight

        graded = await asyncio.to_thread(_graded_for_reweight, game_key)
        state = await asyncio.to_thread(WeightStore().load, game_key)
        proposal = propose_reweight(game_key, graded, state.weights)
        return {"status": "ok", **proposal}
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}


@router.post("/api/graded_reweight")
async def graded_reweight(request: ReweightRequest):
    """Apply a graded-reweight move only when every gate passes.

    Gates live in services.graded_reweight: 30 unique graded tickets, a
    significant material per-strategy lift, a capped shrinkage blend, and a
    recorded history entry with the previous weights. When any gate fails
    the weights are left untouched and the hold reasons are returned.
    """
    try:
        game_key = _require_game_key(request.game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        from services.graded_reweight import apply_reweight

        graded = await asyncio.to_thread(_graded_for_reweight, game_key)
        result = await asyncio.to_thread(apply_reweight, game_key, graded)
        return {"status": "ok", **result}
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}
