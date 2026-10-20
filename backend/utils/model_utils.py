"""
Model utilities for loading and managing ML models.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
from utils.paths import models_dir, experiments_dir


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _models_dir() -> str:
    return models_dir()


def _experiments_dir() -> str:
    return experiments_dir()


def _load_model_metadata(game_key: str) -> dict:
    """
    Load persisted model artifact metadata for a specific game.
    Returns empty dict if metadata file doesn't exist.
    """
    metadata_path = Path(_experiments_dir()) / f"{game_key}_model_metadata.json"

    if not metadata_path.exists():
        return {}

    try:
        with open(metadata_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(f"Failed to load model metadata for {game_key}: {exc}")
        return {}


def _artifact_accuracy(artifact: dict | None) -> Optional[float]:
    if not isinstance(artifact, dict):
        return None

    values = []
    metrics = artifact.get("metrics") or {}
    for key in (
        "accuracy",
        "candidate_accuracy",
        "previous_accuracy",
        "full_dataset_accuracy",
        "baseline_accuracy",
    ):
        value = _coerce_float(metrics.get(key))
        if value is not None:
            values.append(value)

    for key in ("accuracy", "highest_accuracy"):
        value = _coerce_float(artifact.get(key))
        if value is not None:
            values.append(value)

    return max(values) if values else None


def resolve_highest_accuracy(
    game_key: str,
    *,
    artifact: dict | None = None,
    metadata: dict | None = None,
) -> Optional[float]:
    """Return the highest known accuracy for a game across artifact and persisted metadata."""
    resolved_metadata = metadata if metadata is not None else _load_model_metadata(game_key)
    values = []

    artifact_score = _artifact_accuracy(artifact)
    if artifact_score is not None:
        values.append(artifact_score)

    if isinstance(resolved_metadata, dict):
        for key in ("highest_accuracy", "accuracy", "baseline_accuracy"):
            value = _coerce_float(resolved_metadata.get(key))
            if value is not None:
                values.append(value)

        for item in resolved_metadata.get("accuracy_history") or []:
            value = _coerce_float(item)
            if value is not None:
                values.append(value)

        leaderboard = resolved_metadata.get("score_leaderboard") or []
        if leaderboard and isinstance(leaderboard[0], dict):
            value = _coerce_float(leaderboard[0].get("accuracy"))
            if value is not None:
                values.append(value)

    return max(values) if values else None


def load_model_artifact(game_key: str, models_dir: str | None = None) -> Tuple[Optional[dict], Optional[str]]:
    """
    Load the saved highest-accuracy model artifact for a game.
    Returns (artifact, error_message).
    """
    base_dir = models_dir or _models_dir()
    model_path = os.path.join(base_dir, f"{game_key}_model.joblib")

    if not os.path.exists(model_path):
        return None, f"Model for game '{game_key}' not found."

    if os.path.getsize(model_path) <= 0:
        return None, f"Model for game '{game_key}' is empty/corrupted."

    try:
        artifact = joblib.load(model_path)
    except Exception as exc:
        return None, f"Unable to load model for game '{game_key}': {exc}"

    if not isinstance(artifact, dict) or artifact.get("model") is None:
        return None, f"Model artifact for game '{game_key}' is invalid."

    return artifact, None


def build_prediction_model_metadata(
    game_key: str,
    artifact: dict,
    metadata: dict | None = None,
) -> Dict[str, Any]:
    """Build model metadata attached to suggestion responses."""
    resolved_metadata = metadata if metadata is not None else _load_model_metadata(game_key)
    metrics = artifact.get("metrics") or {}
    highest_accuracy = resolve_highest_accuracy(
        game_key,
        artifact=artifact,
        metadata=resolved_metadata,
    )
    leaderboard = resolved_metadata.get("score_leaderboard") or []
    leaderboard_top = leaderboard[0] if leaderboard and isinstance(leaderboard[0], dict) else {}

    return {
        "game": game_key,
        "highest_accuracy": highest_accuracy,
        "model_accuracy": _artifact_accuracy(artifact),
        "model_strategy": str(artifact.get("model_strategy", "single")),
        "blend_weight": artifact.get("blend_weight"),
        "used_previous_training": bool(metrics.get("used_previous_training")),
        "retained_previous_model": bool(resolved_metadata.get("retained_previous_model")),
        "leaderboard_top_accuracy": _coerce_float(leaderboard_top.get("accuracy")),
        "leaderboard_top_strategy": leaderboard_top.get("model_strategy"),
        "accuracy_history": resolved_metadata.get("accuracy_history") or [],
        "uses_highest_accuracy_model": True,
    }