"""Retro-test routes: walk-forward backtests over ingested draw history.

Simulated evidence only. Runs write to the retro store, never to the live
suggestion ledger, and reports are labelled simulated end to end.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.retro_backtest import retro_report, run_retro_backtest
from utils.validation import _require_game_key

router = APIRouter()


class RetroRunRequest(BaseModel):
    game: str
    max_trials: int = 300
    stride: int = 1
    warmup: int = 50
    history_window: int = 500
    tail_reserve: int | None = None
    include_tail: bool = False


def _current_weights(game_key: str) -> dict:
    try:
        from prediction.state.weight_store import WeightStore

        return dict(WeightStore().load(game_key).weights or {})
    except Exception:
        return {}


@router.post("/api/retro_backtest/run")
async def retro_backtest_run(request: RetroRunRequest):
    """Replay draw history walk-forward and grade the engine's past picks.

    Each trial predicts from draws strictly before its target draw. The
    most recent tail of history is held out unless ``include_tail`` is set.
    Results are stored as simulated evidence (see the report route).
    """
    try:
        game_key = _require_game_key(request.game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        summary = await asyncio.to_thread(
            run_retro_backtest,
            game_key,
            max_trials=request.max_trials,
            stride=request.stride,
            warmup=request.warmup,
            history_window=request.history_window,
            tail_reserve=request.tail_reserve,
            include_tail=request.include_tail,
        )
        report = await asyncio.to_thread(
            retro_report, game_key, current_weights=_current_weights(game_key))
        return {"status": "success", **summary, "report": report}
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}


@router.get("/api/retro_backtest/report")
async def retro_backtest_report(
    game: str = Query(..., description="Game key, e.g. take5"),
):
    """Simulated per-game accuracy: ticket level, per-strategy credit, and
    the advisory (never applied) reweight the live gates would propose."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        report = await asyncio.to_thread(
            retro_report, game_key, current_weights=_current_weights(game_key))
        return {"status": "success", **report}
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}
