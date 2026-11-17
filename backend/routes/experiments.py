"""
Experiments API routes.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query
from experiments.store import ExperimentStore
from services.experiment_accuracy import build_experiment_accuracy
from utils.timestamps import normalize_experiment_record
from utils.paths import experiments_file
from utils.validation import _require_game_key


router = APIRouter()
exp_store = ExperimentStore(experiments_file())


@router.get("/api/experiments")
async def get_experiments(limit: int = 100):
    """
    Returns recent completed experiments (newest first).
    """
    try:
        safe_limit = max(1, min(limit, 500))
        experiments = await asyncio.to_thread(exp_store.list_experiments)
        experiments = [normalize_experiment_record(item) for item in experiments]
        experiments.sort(
            key=lambda item: float(item.get("timestamp") or item.get("timestamp_seconds") or 0),
            reverse=True,
        )
        recent = experiments[:safe_limit]
        return {
            "status": "ok",
            "experiments": recent,
            "count": len(recent),
            "total": len(experiments),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/api/experiment_accuracy")
async def experiment_accuracy(
    game: str = Query(...),
    last: int = Query(3, ge=1, le=50),
):
    """Grade a game's past experiment suggestions against real draws.

    Bridges Experiments to ground truth: each prediction experiment is
    graded against the immediate draw after it was served (ingested draws,
    plus verified official draws for missing dates). See the bias_note in
    the response: the live experiments file keeps only top-accuracy
    records, so this is a rich diagnostic, not the unbiased verdict —
    that one lives in /api/accuracy_report (the append-only ledger).
    """
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await asyncio.to_thread(build_experiment_accuracy, game_key, last)
    except Exception as exc:
        return {"status": "error", "game": game_key, "message": str(exc)}