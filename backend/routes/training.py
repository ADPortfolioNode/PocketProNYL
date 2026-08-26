"""Training API routes."""
import logging
import asyncio
import hashlib
import json
import os
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from services.trainer import trainer_service
from experiments.store import ExperimentStore
from utils.training_params import extract_training_params, merge_training_params
from utils.validation import _require_game_key
from config import GAME_CONFIGS
from services.chroma_client import chroma_client
from utils.timestamps import normalize_experiment_record, runtime_timestamp_fields
from state.train_jobs import get_job, set_job, list_jobs, is_running

router = APIRouter()
logger = logging.getLogger(__name__)

exp_store = ExperimentStore("/data/experiments/experiments.json")


class TrainingRequest(BaseModel):
    game: str
    target_accuracy: float = Field(default=0.90, ge=0.5, le=0.99)
    max_iterations: int = Field(default=40, ge=1, le=100)
    train_size: float = Field(default=0.25, ge=0.10, le=0.50)
    n_estimators: int = Field(default=250, ge=50, le=600)
    max_depth: int = Field(default=18, ge=4, le=32)
    random_state: int = Field(default=42, ge=0)
    window_size: int = Field(default=3, ge=1, le=8)
    auto_tune: bool = True
    blend_step: float | None = Field(default=None, ge=0.01, le=0.5)

    @field_validator("target_accuracy", mode="before")
    @classmethod
    def clamp_target_accuracy(cls, value):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value
        return min(0.99, max(0.5, numeric))


class TrainAllRequest(BaseModel):
    games: list[str] | None = None
    target_accuracy: float = Field(default=0.90, ge=0.5, le=0.99)
    max_iterations: int = Field(default=40, ge=1, le=100)
    train_size: float = Field(default=0.25, ge=0.10, le=0.50)
    n_estimators: int = Field(default=250, ge=50, le=600)
    max_depth: int = Field(default=18, ge=4, le=32)
    random_state: int = Field(default=42, ge=0)
    window_size: int = Field(default=3, ge=1, le=8)
    auto_tune: bool = True


def _dataset_snapshot(game: str) -> dict:
    try:
        count = chroma_client.count_documents(game)
        return {
            "dataset_hash": hashlib.md5(f"{game}|{count}".encode("utf-8")).hexdigest(),
            "record_count": int(count or 0),
        }
    except Exception as exc:
        logger.error("Error getting dataset snapshot for game %s: %s", game, exc)
        return {"dataset_hash": None, "record_count": 0}


def _experiment_timestamp(experiment: dict) -> float:
    for key in ("timestamp", "timestamp_seconds"):
        value = experiment.get(key)
        if value is None:
            continue
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if ts >= 1_000_000_000_000:
            return ts / 1000.0
        return ts
    return 0.0


def _last_trained_dataset_snapshot(game_key: str) -> dict:
    game_key = str(game_key or "").strip().lower()
    last_count = 0
    last_hash = None
    last_trained_at = 0.0

    for experiment in exp_store.list_experiments() or []:
        if str(experiment.get("game") or "").strip().lower() != game_key:
            continue
        if str(experiment.get("type") or "").strip().lower() != "training":
            continue
        if str(experiment.get("status") or "").strip().lower() not in ("completed", "success"):
            continue
        ts = _experiment_timestamp(experiment)
        if ts >= last_trained_at:
            last_trained_at = ts
            last_count = int(experiment.get("record_count") or 0)
            last_hash = experiment.get("dataset_hash")

    data_dir = os.environ.get("DATA_DIR", "/data")
    metadata_path = os.path.join(data_dir, "experiments", f"{game_key}_model_metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as handle:
                metadata = json.load(handle)
            if isinstance(metadata, dict):
                meta_count = int(metadata.get("last_trained_record_count") or 0)
                meta_ts = float(metadata.get("last_trained_at") or 0.0)
                if meta_ts >= last_trained_at and meta_count > 0:
                    last_trained_at = meta_ts
                    last_count = meta_count
                    last_hash = metadata.get("last_trained_dataset_hash") or last_hash
        except Exception:
            pass

    ready_path = os.path.join(
        os.environ.get("PREDICTION_STATE_DIR", "/data/prediction"),
        f"{game_key}_ready.json",
    )
    if os.path.exists(ready_path):
        try:
            with open(ready_path, encoding="utf-8") as handle:
                ready = json.load(handle)
            if isinstance(ready, dict):
                ready_count = int(ready.get("record_count") or 0)
                ready_ts = float(ready.get("trained_at") or 0.0)
                if ready_ts >= last_trained_at and ready_count > 0:
                    last_trained_at = ready_ts
                    last_count = ready_count
                    last_hash = ready.get("dataset_hash") or last_hash
        except Exception:
            pass

    return {
        "record_count": last_count,
        "dataset_hash": last_hash,
        "last_trained_at": last_trained_at or None,
    }


def _training_data_status(game_key: str, *, incremental: dict | None = None) -> dict:
    game_key = str(game_key or "").strip().lower()
    current = _dataset_snapshot(game_key)
    last = _last_trained_dataset_snapshot(game_key)
    current_count = int(current.get("record_count") or 0)
    last_count = int(last.get("record_count") or 0)
    incremental = incremental or {}
    has_model = bool(
        incremental.get("has_saved_model")
        or incremental.get("incremental_learning")
        or last_count > 0
    )

    if current_count <= 0:
        return {
            "status": "no_data",
            "has_untrained_data": False,
            "current_record_count": 0,
            "last_trained_record_count": last_count,
            "new_draws": 0,
            "message": "No ingested draws for this game.",
        }

    if not has_model and last_count <= 0:
        return {
            "status": "never_trained",
            "has_untrained_data": True,
            "current_record_count": current_count,
            "last_trained_record_count": 0,
            "new_draws": current_count,
            "message": f"{current_count:,} draws ingested — model has never been trained.",
        }

    current_hash = current.get("dataset_hash")
    last_hash = last.get("dataset_hash")
    hash_changed = bool(current_hash and last_hash and current_hash != last_hash)
    new_draws = max(0, current_count - last_count)

    if new_draws > 0 or hash_changed:
        return {
            "status": "stale",
            "has_untrained_data": True,
            "current_record_count": current_count,
            "last_trained_record_count": last_count,
            "new_draws": new_draws if new_draws > 0 else max(1, current_count - last_count),
            "message": (
                f"{new_draws:,} new draw(s) since last training "
                f"({last_count:,} → {current_count:,})."
                if new_draws > 0
                else f"Dataset changed since last training ({last_count:,} → {current_count:,})."
            ),
        }

    return {
        "status": "current",
        "has_untrained_data": False,
        "current_record_count": current_count,
        "last_trained_record_count": last_count,
        "new_draws": 0,
        "message": f"Model trained on current dataset ({current_count:,} draws).",
    }


def _run_trainer(game_key: str, request: TrainingRequest):
    if hasattr(trainer_service, "train"):
        return trainer_service.train(
            game_key,
            target_accuracy=request.target_accuracy,
            max_iterations=request.max_iterations,
            train_size=request.train_size,
            n_estimators=request.n_estimators,
            max_depth=request.max_depth,
            random_state=request.random_state,
            blend_step=request.blend_step,
            window_size=request.window_size,
            auto_tune=request.auto_tune,
        )
    if hasattr(trainer_service, "train_model"):
        trainer_service.configure_training(
            target_accuracy=request.target_accuracy,
            max_iterations=request.max_iterations,
            train_size=request.train_size,
            n_estimators=request.n_estimators,
            max_depth=request.max_depth,
            random_state=request.random_state,
            window_size=request.window_size,
            auto_tune=request.auto_tune,
            blend_step=request.blend_step,
        )
        return trainer_service.train_model(game_key)
    raise RuntimeError("TrainerService is missing train/train_model methods")


def _persist_training_result(game_key: str, request: TrainingRequest, result: dict) -> dict:
    if result.get("status") == "error":
        payload = {
            "status": "error",
            "game": game_key,
            "message": result.get("message", "Training failed."),
            **{k: result.get(k) for k in (
                "accuracy", "highest_accuracy", "record_accuracy", "baseline_accuracy",
                "candidate_accuracy", "previous_accuracy", "training_target",  "target_accuracy",
                "retained_previous_model", "used_previous_training", "training_time",
            )},
        }
        set_job(game_key, payload)
        return payload

    accuracy = result.get("highest_accuracy") or result.get("record_accuracy") or result.get("accuracy")
    runtime_ts = runtime_timestamp_fields()
    experiment_id = f"train-{game_key}-{runtime_ts['timestamp_seconds']}"
    dataset = _dataset_snapshot(game_key)
    scored_params = merge_training_params(
        result.get("best_training_params"),
        result.get("training_params"),
        extract_training_params(request.model_dump()),
        {
            "train_size": result.get("train_size", request.train_size),
            "validation_size": result.get("validation_size"),
            "n_estimators": request.n_estimators,
            "max_depth": request.max_depth,
            "random_state": request.random_state,
            "window_size": request.window_size,
            "auto_tune": request.auto_tune,
            "blend_step": request.blend_step,
            "max_iterations": request.max_iterations,
            "target_accuracy": result.get("target_accuracy", request.target_accuracy),
            "training_target": result.get("training_target"),
            "model_strategy": result.get("model_strategy"),
            "blend_weight": result.get("blend_weight"),
        },
    )
    exp_store.save_experiment(normalize_experiment_record({
        "experiment_id": experiment_id,
        "game": game_key,
        **runtime_ts,
        "status": "COMPLETED",
        "type": "training",
        "target_accuracy": result.get("target_accuracy", request.target_accuracy),
        "training_target": result.get("training_target"),
        "baseline_accuracy": result.get("baseline_accuracy"),
        "final_accuracy": accuracy,
        "highest_accuracy": result.get("highest_accuracy", accuracy),
        "record_accuracy": result.get("record_accuracy", result.get("highest_accuracy", accuracy)),
        "score": result.get("highest_accuracy", accuracy),
        "accuracy": result.get("highest_accuracy", accuracy),
        "iterations": result.get("attempts"),
        "max_iterations": scored_params.get("max_iterations", request.max_iterations),
        "training_time": result.get("training_time"),
        "model_strategy": result.get("model_strategy"),
        "blend_weight": result.get("blend_weight"),
        "candidate_accuracy": result.get("candidate_accuracy"),
        "previous_accuracy": result.get("previous_accuracy"),
        "retained_previous_model": result.get("retained_previous_model"),
        "used_previous_training": result.get("used_previous_training"),
        "message": result.get("message"),
        "train_size": scored_params.get("train_size", result.get("train_size", request.train_size)),
        "validation_size": scored_params.get("validation_size", result.get("validation_size")),
        "n_estimators": scored_params.get("n_estimators", request.n_estimators),
        "max_depth": scored_params.get("max_depth", request.max_depth),
        "random_state": scored_params.get("random_state", request.random_state),
        "window_size": scored_params.get("window_size", request.window_size),
        "auto_tune": scored_params.get("auto_tune", request.auto_tune),
        "blend_step": scored_params.get("blend_step", request.blend_step),
        "data_limit": scored_params.get("data_limit"),
        "training_params": scored_params,
        "best_training_params": result.get("best_training_params") or scored_params,
        "leaderboard": result.get("leaderboard", []),
        "accuracy_history": result.get("accuracy_history", []),
        "optimal_config_applied": result.get("optimal_config_applied", False),
        "optimal_config": result.get("optimal_config", {}),
        "record_count": dataset.get("record_count"),
        "dataset_hash": dataset.get("dataset_hash"),
    }))
    payload = {
        **result,
        "status": "COMPLETED",
        "game": game_key,
        "experiment_id": experiment_id,
        "record_count": dataset.get("record_count"),
        "dataset_hash": dataset.get("dataset_hash"),
        "score": accuracy,
        "accuracy": accuracy,
        "highest_accuracy": result.get("highest_accuracy", accuracy),
        "record_accuracy": result.get("record_accuracy", accuracy),
        "message": result.get("message") or "Training completed.",
    }
    set_job(game_key, payload)
    return payload


def _training_worker(game_key: str, request: TrainingRequest):
    try:
        result = _run_trainer(game_key, request)
        _persist_training_result(game_key, request, result)
    except Exception as exc:
        logger.exception("Training failed for %s", game_key)
        set_job(game_key, {"status": "error", "game": game_key, "message": str(exc)})


@router.get("/api/train_settings")
async def get_train_settings(game: str = None):
    defaults = trainer_service.get_training_defaults()
    defaults["blend_step"] = float(trainer_service.blend_step)

    if game:
        game_key = _require_game_key(game)
        incremental = trainer_service.get_incremental_training_context(
            game_key,
            requested_target=defaults["target_accuracy"],
        )
        recreate_defaults = incremental.get("recreate_defaults") or {}
        merged_defaults = {
            **defaults,
            **{key: value for key, value in recreate_defaults.items() if value is not None},
        }
        training_data = _training_data_status(game_key, incremental=incremental)
        return {
            "game": game_key,
            "defaults": merged_defaults,
            "dataset": _dataset_snapshot(game_key),
            "incremental": incremental,
            "training_data": training_data,
            "best_training_params": incremental.get("best_training_params") or {},
            "recreate_defaults": recreate_defaults,
            "job": get_job(game_key),
        }

    per_game = {}
    for game_name in GAME_CONFIGS.keys():
        incremental = trainer_service.get_incremental_training_context(
            game_name,
            requested_target=defaults["target_accuracy"],
        )
        per_game[game_name] = {
            **_dataset_snapshot(game_name),
            "has_saved_model": incremental.get("has_saved_model"),
            "engine": incremental.get("engine"),
            "highest_accuracy": incremental.get("highest_accuracy"),
            "best_training_params": incremental.get("best_training_params") or {},
            "recreate_defaults": incremental.get("recreate_defaults") or {},
            "score_leaderboard": incremental.get("score_leaderboard") or [],
            "training_data": _training_data_status(game_name, incremental=incremental),
            "job": get_job(game_name),
        }
    return {"game": None, "defaults": defaults, "per_game": per_game}


@router.get("/api/train_settings/{game}")
async def get_train_settings_by_path(game: str):
    return await get_train_settings(game=game)


@router.get("/api/train_status")
async def get_train_status(game: str = None):
    if game:
        return get_job(_require_game_key(game))
    return {"jobs": list_jobs()}


@router.post("/api/train")
async def train_model(request: TrainingRequest):
    try:
        game_key = _require_game_key(request.game)
        if is_running(game_key):
            job = get_job(game_key)
            return {"status": "already_running", "game": game_key, "message": "Training already in progress.", **job}

        set_job(game_key, {
            "status": "running",
            "message": "Training in progress",
            "target_accuracy": request.target_accuracy,
            "max_iterations": request.max_iterations,
        })
        thread = threading.Thread(
            target=_training_worker,
            args=(game_key, request),
            daemon=True,
            name=f"train-{game_key}",
        )
        thread.start()
        return {
            "status": "started",
            "game": game_key,
            "message": "Training started in the background. Poll /api/train_status.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {"status": "error", "game": request.game, "message": str(e)}


@router.post("/api/train_all")
async def train_all_models(request: TrainAllRequest):
    try:
        trainer_service.configure_training(
            target_accuracy=request.target_accuracy,
            max_iterations=request.max_iterations,
            train_size=request.train_size,
            n_estimators=request.n_estimators,
            max_depth=request.max_depth,
            random_state=request.random_state,
            window_size=request.window_size,
            auto_tune=request.auto_tune,
        )

        game_keys = None
        if request.games:
            game_keys = [_require_game_key(game) for game in request.games]

        results = await asyncio.to_thread(trainer_service.train_all_games, game_keys)
        completed = [item for item in results if str(item.get("status", "")).lower() in ("success", "completed")]
        return {
            "status": "COMPLETED",
            "trained": len(completed),
            "total": len(results),
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
