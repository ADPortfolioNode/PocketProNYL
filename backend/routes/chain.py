"""Chained-ensemble evaluation routes (champion/challenger vs the incumbent)."""

import threading
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from prediction.chain import champion_store
from utils.validation import _require_game_key

router = APIRouter()
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class ChainEvaluateRequest(BaseModel):
    game: str
    commit: bool = True
    history_limit: int = 400


def _run(game: str, commit: bool, limit: int) -> None:
    try:
        from prediction.chain.evaluate import evaluate_chain
        from prediction.core.draw_loader import from_chroma
        from services.prediction_adapter import prediction_adapter

        history = from_chroma(game, limit=max(100, min(int(limit), 2000)))
        result = evaluate_chain(prediction_adapter.engine, game, history, commit=commit)
        state = {"status": "completed", "result": result}
    except Exception as exc:
        state = {"status": "error", "message": str(exc)}
    with _LOCK:
        _JOBS[game] = {**_JOBS.get(game, {}), **state, "finished_at": time.time()}


@router.post("/api/chain/evaluate")
async def chain_evaluate(request: ChainEvaluateRequest):
    """Start a background champion/challenger evaluation for one game."""
    try:
        game = _require_game_key(request.game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with _LOCK:
        if (_JOBS.get(game) or {}).get("status") == "running":
            return {"status": "running", "game": game, "started_at": _JOBS[game].get("started_at")}
        _JOBS[game] = {"status": "running", "game": game, "started_at": time.time()}
    threading.Thread(target=_run, args=(game, request.commit, request.history_limit), daemon=True).start()
    return {"status": "running", "game": game}


@router.get("/api/chain/status")
async def chain_status(game: str = Query(...)):
    """Job state plus the stored champion record for one game."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with _LOCK:
        job = dict(_JOBS.get(game_key) or {"status": "idle"})
    return {"game": game_key, "job": job, "champion": champion_store.champion_summary(game_key)}
