"""
Predictions API routes.
"""
import asyncio # Keep asyncio for async operations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from services.predictor import PredictorService # Import the class
from services.predictor import predictor_service
from experiments.store import ExperimentStore
from utils.validation import _require_game_key
from utils.timestamps import normalize_experiment_record, runtime_timestamp_fields


router = APIRouter()
exp_store = ExperimentStore("/data/experiments/experiments.json")


class PredictionRequest(BaseModel):
    game: str
    recent_k: int = 10
    strategy: Optional[str] = None
    target_draw_date: Optional[str] = None # ISO date string (YYYY-MM-DD)
    hot_window: int = 50 # For 'hot_cold' strategy
    cold_window: int = 200 # For 'hot_cold' strategy
    hot_weight: float = 0.7 # For 'hot_cold' strategy
    cold_weight: float = 0.3 # For 'hot_cold' strategy


class PredictAllRequest(BaseModel):
    games: Optional[List[str]] = None
    recent_k: int = 10


class PredictionFeedbackRequest(BaseModel):
    game: str
    primary: List[int]
    bonus: Optional[List[int]] = None
    draw_id: Optional[str] = None


@router.get("/api/predictions/all")
async def get_all_predictions():
    """
    Returns summary of available predictions across all games.
    """
    try:
        from config import GAME_CONFIGS
        from utils.model_utils import load_model_artifact, _load_model_metadata, resolve_highest_accuracy
        
        predictions_summary = []
        for game in GAME_CONFIGS.keys():
            game_key = game.strip().lower()
            try:
                summary = predictor_service.get_prediction_summary(game_key)
                predictions_summary.append({
                    "game": game,
                    "has_model": summary.get("has_model", False),
                    "accuracy": summary.get("accuracy"),
                    "highest_accuracy": summary.get("highest_accuracy"),
                    "model_strategy": summary.get("model_strategy"),
                })
            except Exception as e:
                predictions_summary.append({
                    "game": game,
                    "has_model": False,
                    "message": str(e)
                })
        
        return {
            "status": "ok",
            "predictions": predictions_summary
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


def _normalize_prediction_result(game_key: str, result: dict, recent_k: int) -> dict:
    if result.get("status") == "error":
        return {
            "status": "error",
            "game": game_key,
            "message": result.get("message", "Suggestion failed."),
        }

    next_draw_date = (
        result.get("prediction_date")
        or result.get("predicted_for_date")
        or None
    )

    runtime_ts = runtime_timestamp_fields()
    exp_store.save_experiment(normalize_experiment_record({
        "experiment_id": f"predict-{game_key}-{runtime_ts['timestamp_seconds']}",
        "game": game_key,
        **runtime_ts,
        "status": "COMPLETED",
        "type": "prediction",
        "recent_k": recent_k,
        "prediction": result,
        "next_draw_date": next_draw_date,
    }))

    return {
        **result,
        "status": "COMPLETED",
        "game": game_key,
        "next_draw_date": next_draw_date,
        "highest_accuracy": result.get("highest_accuracy"),
        "model_metadata": result.get("model_metadata"),
        "strategy_used": result.get("strategy_used"),
        "generated_at": result.get("generated_at"),
    }


@router.post("/api/prediction/feedback")
async def prediction_feedback(request: PredictionFeedbackRequest):
    """Record an actual draw and update ensemble strategy weights."""
    try:
        game_key = _require_game_key(request.game)
        from prediction.core.types import Draw
        # The prediction_adapter is no longer used.
        # Weight update logic needs to be implemented directly in PredictorService or a dedicated service.
        # For now, we'll return a NotImplementedError to prevent breaking changes.
        raise NotImplementedError("Weight update functionality is not yet implemented in the new architecture.")
        # actual = Draw(primary=request.primary, bonus=request.bonus or [], draw_id=request.draw_id)
        # result = await asyncio.to_thread(predictor_service.update_weights, game_key, actual)
        # return {"status": "ok", "game": game_key, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/predict_all")
async def predict_all_games(request: PredictAllRequest):
    """
    Generate suggestions for multiple games in one request (processed sequentially).
    """
    try:
        from config import GAME_CONFIGS

        requested_games = request.games or list(GAME_CONFIGS.keys())
        game_keys = []
        for game in requested_games:
            game_keys.append(_require_game_key(game))

        def _run_predictions():
            return predictor_service.predict_all_games(game_keys, request.recent_k)

        results = await asyncio.to_thread(_run_predictions)
        failed_count = sum(1 for item in results if item.get("status") == "error")

        return {
            "status": "ok",
            "results": results,
            "failed_count": failed_count,
            "total": len(results),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "results": [],
            "failed_count": 0,
            "total": 0,
        }


@router.post("/api/predict")
async def make_prediction(request: PredictionRequest):
    """
    Generate prediction for a specific game using recent history.
    """
    try:
        game_key = _require_game_key(request.game)
        def _run_prediction():
            if hasattr(predictor_service, "predict_next_draw"):
                return predictor_service.predict_next_draw(
                    game_key,
                    request.recent_k,
                    strategy=request.strategy,
                    target_draw_date=request.target_draw_date,
                    hot_window=request.hot_window,
                    cold_window=request.cold_window,
                    hot_weight=request.hot_weight,
                    cold_weight=request.cold_weight,
                )
            if hasattr(predictor_service, "predict"):
                return predictor_service.predict(
                    game_key,
                    request.recent_k,
                    strategy=request.strategy,
                    target_draw_date=request.target_draw_date,
                    hot_window=request.hot_window,
                    cold_window=request.cold_window,
                    hot_weight=request.hot_weight,
                    cold_weight=request.cold_weight,
                )
            raise RuntimeError("PredictorService is missing predict_next_draw/predict methods")

        result = await asyncio.to_thread(_run_prediction)
        return _normalize_prediction_result(game_key, result, request.recent_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "status": "error",
            "game": request.game,
            "message": str(e)
        }