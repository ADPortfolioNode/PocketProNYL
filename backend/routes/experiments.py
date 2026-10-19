"""
Experiments API routes.
"""
import asyncio

from fastapi import APIRouter
from experiments.store import ExperimentStore
from utils.timestamps import normalize_experiment_record
from utils.paths import experiments_file


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