import json
import os
import re
import logging
import time
import numpy as np
import joblib
import warnings
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, accuracy_score
from sklearn.exceptions import ConvergenceWarning
from config import GAME_CONFIGS
from utils.training_params import (
    extract_training_params,
    merge_training_params,
    normalize_leaderboard_entry,
    params_to_defaults,
    snapshot_from_trainer,
    top_scored_params,
)

from utils.game_data_parser import (
    _get_rules as get_game_rules,
    _parse_numbers as parse_numbers_util,
    _parse_pick3_digits as parse_pick3_digits_util,
    _clamp_primary as clamp_primary_util,
    _extract_bonus_values as extract_bonus_values_util,
    _extract_primary_candidate as extract_primary_candidate_util,
    _extract_record_sequence as extract_record_sequence_util,
)

# Suppress the specific ConvergenceWarning from sklearn's MLP, which can be noisy
# during the iterative training process of the modular engine.
warnings.filterwarnings("ignore", category=ConvergenceWarning, module="sklearn")


class TrainerService:
    def __init__(self):
        self.models_dir = "/data/models"
        os.makedirs(self.models_dir, exist_ok=True)
        defaults = self.get_training_defaults()
        self.target_accuracy = float(os.environ.get("TRAIN_TARGET_ACCURACY", defaults["target_accuracy"]))
        self.max_train_attempts = int(os.environ.get("TRAIN_MAX_ATTEMPTS", defaults["max_iterations"]))
        self.blend_step = float(os.environ.get("TRAIN_BLEND_STEP", defaults["blend_step"]))
        self.train_size = float(os.environ.get("TRAIN_SIZE", defaults["train_size"]))
        self.n_estimators = int(os.environ.get("TRAIN_N_ESTIMATORS", defaults["n_estimators"]))
        self.max_depth = int(os.environ.get("TRAIN_MAX_DEPTH", defaults["max_depth"]))
        self.random_state = int(os.environ.get("TRAIN_RANDOM_STATE", defaults["random_state"]))
        self.data_limit = int(os.environ.get("TRAIN_DATA_LIMIT", defaults["data_limit"]))
        self.window_size = int(os.environ.get("TRAIN_WINDOW_SIZE", defaults["window_size"]))
        self.auto_tune = str(os.environ.get("TRAIN_AUTO_TUNE", "1")).lower() not in ("0", "false", "no")

    @staticmethod
    def get_training_defaults(game: str | None = None):
        """Return baseline or game-optimized training knobs."""
        try:
            from utils.training_defaults import (
                get_base_training_defaults,
                get_game_training_defaults,
            )
            if game:
                defaults = get_game_training_defaults(game)
            else:
                defaults = get_base_training_defaults()
            # Strip UI-only fields from trainer runtime knobs
            return {
                key: defaults[key]
                for key in (
                    "target_accuracy",
                    "max_iterations",
                    "train_size",
                    "n_estimators",
                    "max_depth",
                    "random_state",
                    "blend_step",
                    "data_limit",
                    "window_size",
                    "auto_tune",
                )
                if key in defaults
            }
        except Exception:
            return {
                "target_accuracy": 0.90,
                "max_iterations": 40,
                "train_size": 0.25,
                "n_estimators": 250,
                "max_depth": 18,
                "random_state": 42,
                "blend_step": 0.05,
                "data_limit": 0,
                "window_size": 3,
                "auto_tune": True,
            }

    @staticmethod
    def _uses_modular_engine() -> bool:
        return os.environ.get("PREDICTION_ENGINE", "modular").lower() != "legacy"

    def _enrich_modular_train_result(self, result: dict, *, record_before: float | None) -> dict:
        """Map modular backtest output to the legacy incremental-training contract."""
        if str(result.get("status", "")).lower() != "success":
            return result

        baseline = record_before
        modular_metrics = result.get("metrics") or {}
        mean_hits = None
        for key in ("mean_partial_hits",):
            value = result.get(key)
            if value is None:
                value = modular_metrics.get(key)
            if value is not None:
                try:
                    mean_hits = float(value)
                    break
                except (TypeError, ValueError):
                    pass

        lift = result.get("lift_vs_random")
        if lift is None:
            lift = modular_metrics.get("lift_vs_random")
        exact_rate = result.get("exact_match_rate")
        if exact_rate is None:
            exact_rate = modular_metrics.get("exact_match_rate")

        has_prior = baseline is not None
        training_target = max(float(self.target_accuracy or 0.0), baseline or 0.0)
        highest_accuracy = baseline

        refreshed = bool(result.get("refreshed"))
        message = "Modular engine backtest complete."
        if refreshed and has_prior and highest_accuracy is not None:
            message = (
                f"Modular engine readiness refreshed. "
                f"Record accuracy held at {highest_accuracy * 100:.2f}%."
            )
        elif has_prior and highest_accuracy is not None:
            message = (
                f"Modular engine backtest complete. "
                f"Record accuracy held at {highest_accuracy * 100:.2f}%."
            )
        elif mean_hits is not None:
            lift_text = f"{float(lift):.4f}" if lift is not None else "n/a"
            message = (
                f"Modular engine backtest complete. "
                f"mean_partial_hits={mean_hits:.4f}, lift_vs_random={lift_text}."
            )

        return {
            **result,
            "engine": "modular",
            "baseline_accuracy": baseline,
            "highest_accuracy": highest_accuracy,
            "record_accuracy": highest_accuracy,
            "accuracy": highest_accuracy,
            "training_target": training_target,
            "target_accuracy": float(self.target_accuracy or 0.0),
            "used_previous_training": has_prior,
            "retained_previous_model": has_prior,
            "previous_accuracy": baseline,
            "candidate_accuracy": mean_hits,
            "modular_metrics": modular_metrics,
            "mean_partial_hits": mean_hits,
            "lift_vs_random": lift,
            "exact_match_rate": exact_rate,
            "message": message,
            "model_strategy": result.get("model_strategy") or "ensemble",
        }

    def configure_training(
        self,
        *,
        target_accuracy=None,
        max_iterations=None,
        train_size=None,
        n_estimators=None,
        max_depth=None,
        random_state=None,
        blend_step=None,
        data_limit=None,
        window_size=None,
        auto_tune=None,
    ):
        if target_accuracy is not None:
            self.target_accuracy = float(target_accuracy)
        if max_iterations is not None:
            self.max_train_attempts = int(max_iterations)
        if train_size is not None:
            self.train_size = float(train_size)
        if n_estimators is not None:
            self.n_estimators = int(n_estimators)
        if max_depth is not None:
            self.max_depth = int(max_depth)
        if random_state is not None:
            self.random_state = int(random_state)
        if blend_step is not None:
            self.blend_step = float(blend_step)
        if data_limit is not None:
            self.data_limit = int(data_limit)
        if window_size is not None:
            self.window_size = max(1, int(window_size))
        if auto_tune is not None:
            self.auto_tune = bool(auto_tune)

    # Use the logger from the module
    logger = logging.getLogger(__name__)

    def _metadata_sort_key(self, metadata):
        if not isinstance(metadata, dict):
            return ""
        for key in ("draw_date", "drawdate", "date", "drawn_at", "draw_datetime"):
            for variant in (key, key.upper(), key.title()):
                value = metadata.get(variant)
                if value:
                    return str(value)
        return ""

    def _sort_metadatas_chronologically(self, metadatas):
        if not metadatas:
            return []
        return sorted(metadatas, key=self._metadata_sort_key)

    def _load_all_metadatas(self, collection):
        page_size = 5000
        total = int(collection.count() or 0)
        if total <= 0:
            return []

        if self.data_limit and self.data_limit > 0:
            total = min(total, self.data_limit)

        metadatas = []
        offset = 0
        while offset < total:
            batch_size = min(page_size, total - offset)
            data = collection.get(
                limit=batch_size,
                offset=offset,
                include=["metadatas"],
            )
            batch = data.get("metadatas") if data else None
            if not batch:
                break
            metadatas.extend(batch)
            offset += len(batch)
            if len(batch) < batch_size:
                break
        return metadatas

    def _extract_winning_sequences(self, metadatas, game: str):
        sequences = []
        game_key = str(game or "").lower()
        for meta in metadatas or []:
            if not isinstance(meta, dict):
                continue

            numbers = _extract_record_sequence(meta, game)
            if numbers: # Use the imported utility function
                sequences.append(numbers)

        return sequences

    def _build_supervised_dataset(self, sequences, window_size: int | None = None):
        window = max(1, int(window_size or self.window_size or 1))
        if len(sequences) < window + 1:
            return None, None, 0, 0

        output_len = max(len(seq) for seq in sequences)
        feature_len = output_len * window

        X = []
        y = []
        for index in range(window, len(sequences)):
            features = []
            for seq in sequences[index - window:index]:
                padded = list(seq[:output_len]) + [0] * max(0, output_len - len(seq))
                features.extend(padded[:output_len])
            target = list(sequences[index][:output_len]) + [0] * max(0, output_len - len(sequences[index]))
            X.append(features[:feature_len])
            y.append(target[:output_len])

        return np.array(X), np.array(y), feature_len, output_len

    @staticmethod
    def build_feature_vector(sequences, window_size: int, feature_len: int):
        window = max(1, int(window_size or 1))
        usable = [seq for seq in (sequences or []) if seq]
        if not usable:
            return []

        per_draw = max(1, feature_len // window)
        recent = usable[-window:]
        if len(recent) < window:
            recent = ([[]] * (window - len(recent))) + recent

        features = []
        for seq in recent:
            padded = list(seq[:per_draw]) + [0] * max(0, per_draw - len(seq))
            features.extend(padded[:per_draw])
        return features[:feature_len]

    def _chronological_split(self, X, y, train_size: float):
        split_idx = int(len(X) * float(train_size))
        split_idx = max(10, min(split_idx, len(X) - 10))
        return X[:split_idx], X[split_idx:], y[:split_idx], y[split_idx:]

    def _score_predictions(self, y_true, y_pred, y_full):
        max_val = float(np.max(y_full)) if np.max(y_full) > 0 else 1.0
        y_pred_scored = np.rint(np.asarray(y_pred, dtype=float))
        y_pred_scored = np.clip(y_pred_scored, 0, max_val)
        mae = float(mean_absolute_error(y_true, y_pred_scored))
        accuracy = max(0.0, 1.0 - (mae / max_val))
        return mae, accuracy

    def _train_size_candidates(self):
        if not self.auto_tune:
            return [float(self.train_size or 0.25)]
        preferred = float(self.train_size or 0.25)
        return sorted({round(preferred, 4), 0.22, 0.25, 0.33})

    def _select_best_candidate(self, candidates, y_val, y_full):
        if not candidates:
            return None

        best_single = max(candidates, key=lambda item: float(item.get("accuracy", 0.0)))
        ensemble_candidate = self._build_top3_ensemble(candidates, y_val, y_full)

        if (
            ensemble_candidate is not None
            and float(ensemble_candidate["accuracy"]) > float(best_single["accuracy"])
        ):
            return {
                "strategy": ensemble_candidate["strategy"],
                "model": ensemble_candidate["model"],
                "top_models": ensemble_candidate.get("top_models"),
                "ensemble_weights": ensemble_candidate.get("ensemble_weights"),
                "top_k_accuracies": ensemble_candidate.get("top_k_accuracies"),
                "mae": float(ensemble_candidate["mae"]),
                "accuracy": float(ensemble_candidate["accuracy"]),
                "predictions": np.asarray(ensemble_candidate.get("predictions"), dtype=float),
                "candidate_accuracy": float(best_single["accuracy"]),
            }

        return {
            "strategy": "single",
            "model": best_single["model"],
            "top_models": None,
            "ensemble_weights": None,
            "top_k_accuracies": None,
            "mae": float(best_single["mae"]),
            "accuracy": float(best_single["accuracy"]),
            "predictions": np.asarray(best_single.get("predictions"), dtype=float),
            "candidate_accuracy": float(best_single["accuracy"]),
        }

    def _rf_n_jobs(self, sample_count: int, n_estimators: int) -> int:
        """Limit parallelism on large fits to reduce backend OOM risk (502 crashes)."""
        if sample_count >= 3000 or n_estimators >= 450:
            return 1
        if sample_count >= 1500 or n_estimators >= 300:
            return 2
        return -1

    def _fit_and_score_model(self, X_train, y_train, X_val, y_val, y_full, attempt: int):
        base_estimators = max(50, int(self.n_estimators or 250))
        base_depth = max(4, int(self.max_depth or 18))
        sample_count = len(X_train)
        estimators_cap = 600
        depth_cap = 32
        if sample_count >= 3000:
            estimators_cap = 200
            depth_cap = 16
        elif sample_count >= 1500:
            estimators_cap = 300
            depth_cap = 20
        n_estimators = min(base_estimators + ((attempt - 1) * 20), estimators_cap)
        max_depth = min(base_depth + max(0, attempt - 1), depth_cap)
        n_jobs = self._rf_n_jobs(len(X_train), n_estimators)
        model = RandomForestRegressor(
            n_estimators=n_estimators,
            random_state=int(self.random_state or 42) + attempt,
            n_jobs=n_jobs,
            max_depth=max_depth,
            min_samples_split=max(2, 4 - (attempt // 6)),
            min_samples_leaf=1,
        )
        model.fit(X_train, y_train)

        predictions = model.predict(X_val)
        mae, accuracy = self._score_predictions(y_val, predictions, y_full)
        return model, mae, accuracy, predictions

    def _ensemble_predictions(self, models, weights, X):
        if not models:
            raise ValueError("No models available for ensemble prediction")
        if len(models) == 1:
            return np.asarray(models[0].predict(X), dtype=float)

        weight_arr = np.asarray(weights[: len(models)], dtype=float)
        if weight_arr.sum() <= 0:
            weight_arr = np.ones(len(models), dtype=float)
        weight_arr = weight_arr / weight_arr.sum()

        stacked = np.stack(
            [np.asarray(model.predict(X), dtype=float) for model in models],
            axis=0,
        )
        return np.tensordot(weight_arr, stacked, axes=(0, 0))

    def _predict_with_artifact(self, artifact: dict, X):
        model_strategy = str((artifact or {}).get("model_strategy", "single"))
        primary_model = (artifact or {}).get("model")
        if primary_model is None:
            raise ValueError("Model artifact is missing primary model")

        if model_strategy == "ensemble_top3":
            top_models = (artifact or {}).get("top_models") or []
            ensemble_weights = (artifact or {}).get("ensemble_weights") or []
            usable_models = [model for model in top_models if model is not None]
            if len(usable_models) >= 2:
                return self._ensemble_predictions(usable_models, ensemble_weights, X)

        if model_strategy == "ensemble":
            secondary_model = (artifact or {}).get("secondary_model")
            if secondary_model is None:
                raise ValueError("Ensemble artifact is missing secondary model")
            blend_weight = float((artifact or {}).get("blend_weight", 0.7))
            blend_weight = max(0.0, min(1.0, blend_weight))
            primary_pred = np.asarray(primary_model.predict(X), dtype=float)
            secondary_pred = np.asarray(secondary_model.predict(X), dtype=float)
            return (blend_weight * primary_pred) + ((1.0 - blend_weight) * secondary_pred)

        return np.asarray(primary_model.predict(X), dtype=float)

    def _train_iterative_collect(
        self,
        X_train,
        y_train,
        X_val,
        y_val,
        y_full,
        training_target: float | None = None,
        floor_accuracy: float | None = None,
    ):
        candidates = []
        target = float(
            training_target if training_target is not None else self.target_accuracy or 0.9
        )
        floor = float(floor_accuracy) if floor_accuracy is not None else None
        effective_target = max(target, floor or 0.0)
        max_attempts = max(1, int(self.max_train_attempts))
        for attempt in range(1, max_attempts + 1):
            try:
                model, mae, accuracy, predictions = self._fit_and_score_model(
                    X_train, y_train, X_val, y_val, y_full, attempt
                )
                candidates.append(
                    {
                        "model": model,
                        "mae": float(mae),
                        "accuracy": float(accuracy),
                        "attempt": int(attempt),
                        "predictions": np.asarray(predictions, dtype=float),
                    }
                )
                candidates.sort(
                    key=lambda item: float(item.get("accuracy", 0.0)),
                    reverse=True,
                )
                candidates = candidates[:3]
                if float(accuracy) >= effective_target:
                    break
            except Exception:
                continue
        return candidates

    def _build_top3_ensemble(self, candidates, y_val, y_full):
        if len(candidates) < 2:
            return None

        top_k = sorted(candidates, key=lambda item: float(item.get("accuracy", 0.0)), reverse=True)[:3]
        accuracies = np.array([max(0.0, float(item.get("accuracy", 0.0))) for item in top_k], dtype=float)
        if accuracies.sum() <= 0:
            weights = np.ones(len(top_k), dtype=float) / len(top_k)
        else:
            weights = accuracies / accuracies.sum()

        stacked_preds = np.stack([item["predictions"] for item in top_k], axis=0)
        ensemble_preds = np.tensordot(weights, stacked_preds, axes=(0, 0))
        ensemble_mae, ensemble_accuracy = self._score_predictions(y_val, ensemble_preds, y_full)
        return {
            "strategy": "ensemble_top3",
            "model": top_k[0]["model"],
            "top_models": [item["model"] for item in top_k],
            "ensemble_weights": weights.tolist(),
            "mae": float(ensemble_mae),
            "accuracy": float(ensemble_accuracy),
            "predictions": ensemble_preds,
            "top_k_accuracies": [float(item.get("accuracy", 0.0)) for item in top_k],
            "attempts": len(candidates),
        }

    def _baseline_accuracy(self, artifact: dict | None, live_accuracy: float | None) -> float | None:
        """Return the highest known accuracy for the current saved model."""
        stored = None
        if isinstance(artifact, dict):
            metrics = artifact.get("metrics") or {}
            stored = metrics.get("accuracy")
            if stored is None:
                stored = artifact.get("accuracy")

        values = []
        if stored is not None:
            try:
                values.append(float(stored))
            except (TypeError, ValueError):
                pass
        if live_accuracy is not None:
            try:
                values.append(float(live_accuracy))
            except (TypeError, ValueError):
                pass

        return max(values) if values else None

    def _load_stored_baseline(self, game: str) -> float | None:
        """Load highest known accuracy from metadata, history, and leaderboard."""
        experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
        metadata_path = os.path.join(experiments_dir, f"{game}_model_metadata.json")
        values: list[float] = []

        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    for key in ("highest_accuracy", "accuracy", "baseline_accuracy"):
                        value = data.get(key)
                        if value is not None:
                            values.append(float(value))
                    for item in data.get("accuracy_history") or []:
                        try:
                            values.append(float(item))
                        except (TypeError, ValueError):
                            continue
                    leaderboard = data.get("score_leaderboard") or []
                    if leaderboard and isinstance(leaderboard[0], dict):
                        top = leaderboard[0].get("accuracy")
                        if top is not None:
                            values.append(float(top))
            except Exception:
                pass

        model_path = os.path.join(self.models_dir, f"{game}_model.joblib")
        if os.path.exists(model_path):
            try:
                artifact = joblib.load(model_path)
                artifact_score = self._baseline_accuracy(artifact, None)
                if artifact_score is not None:
                    values.append(float(artifact_score))
            except Exception:
                pass

        return max(values) if values else None

    def _load_score_leaderboard_for_game(self, game_key: str) -> list[dict]:
        """Build a merged accuracy leaderboard from metadata and saved experiments."""
        from experiments.store import ExperimentStore, _experiment_accuracy

        experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
        metadata_path = os.path.join(experiments_dir, f"{game_key}_model_metadata.json")
        entries: list[dict] = []

        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as handle:
                    metadata = json.load(handle)
                for item in metadata.get("score_leaderboard") or []:
                    normalized = normalize_leaderboard_entry(
                        item,
                        default_timestamp=float(item.get("timestamp") or 0.0)
                        if isinstance(item, dict)
                        else 0.0,
                    )
                    if normalized:
                        entries.append(normalized)
            except Exception:
                pass

        experiments_path = os.path.join(experiments_dir, "experiments.json")
        if os.path.exists(experiments_path):
            try:
                store = ExperimentStore(experiments_path)
                for experiment in store.list_experiments():
                    if str(experiment.get("game") or "").strip().lower() != game_key:
                        continue
                    if str(experiment.get("type") or "").lower() != "training":
                        continue
                    accuracy = _experiment_accuracy(experiment)
                    if accuracy is None:
                        continue
                    entry = normalize_leaderboard_entry(
                        {
                            "accuracy": float(accuracy),
                            "timestamp": float(experiment.get("timestamp") or 0.0),
                            "source": "experiment",
                            "training_params": extract_training_params(experiment),
                            **extract_training_params(experiment),
                            "model_strategy": experiment.get("model_strategy"),
                        },
                        default_timestamp=float(experiment.get("timestamp") or 0.0),
                    )
                    if entry:
                        entries.append(entry)
            except Exception:
                pass

        entries.sort(
            key=lambda item: (
                float(item.get("accuracy") if item.get("accuracy") is not None else -1.0),
                float(item.get("timestamp") or 0.0),
            ),
            reverse=True,
        )
        return entries

    def get_incremental_training_context(
        self,
        game: str,
        *,
        requested_target: float | None = None,
    ) -> dict:
        """Summarize prior model state for incremental (build-on-previous) training."""
        game_key = str(game or "").strip().lower()
        model_path = os.path.join(self.models_dir, f"{game_key}_model.joblib")
        has_saved_model = os.path.exists(model_path)
        modular_ready = False
        if self._uses_modular_engine():
            try:
                from services.prediction_adapter import prediction_adapter

                modular_ready = prediction_adapter.is_ready(game_key)
            except Exception:
                modular_ready = False
        has_saved_model = has_saved_model or modular_ready
        baseline = self._load_stored_baseline(game_key)
        requested = float(
            requested_target if requested_target is not None else self.target_accuracy
        )
        requested = min(0.99, max(0.5, requested))
        training_target = max(requested, baseline or 0.0)
        stored_meta: dict = {}
        experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
        metadata_path = os.path.join(experiments_dir, f"{game_key}_model_metadata.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        stored_meta = loaded
            except Exception:
                stored_meta = {}

        score_leaderboard = self._load_score_leaderboard_for_game(game_key)
        if not score_leaderboard:
            score_leaderboard = stored_meta.get("score_leaderboard") or []
        best_training_params = merge_training_params(
            stored_meta.get("best_training_params"),
            extract_training_params(stored_meta),
            top_scored_params(score_leaderboard),
        )

        return {
            "game": game_key,
            "has_saved_model": has_saved_model,
            "highest_accuracy": baseline,
            "baseline_accuracy": baseline,
            "requested_target_accuracy": requested,
            "training_target": training_target,
            "incremental_learning": bool(has_saved_model or baseline is not None),
            "blend_step": float(self.blend_step),
            "model_type": "modular_ensemble" if modular_ready else "RandomForestRegressor",
            "engine": "modular" if self._uses_modular_engine() else "legacy",
            "model_strategy": stored_meta.get("model_strategy"),
            "blend_weight": stored_meta.get("blend_weight"),
            "accuracy_history": stored_meta.get("accuracy_history") or [],
            "recent_runs": stored_meta.get("recent_runs") or [],
            "score_leaderboard": score_leaderboard,
            "best_training_params": best_training_params,
            "recreate_defaults": params_to_defaults(best_training_params),
        }

    def _resolve_baseline_accuracy(
        self,
        game: str,
        model_baseline: float | None,
    ) -> float | None:
        """Return the highest known accuracy across model artifact and metadata."""
        values = []
        if model_baseline is not None:
            values.append(float(model_baseline))
        stored = self._load_stored_baseline(game)
        if stored is not None:
            values.append(float(stored))
        return max(values) if values else None

    def _load_previous_state(self, model_path: str, X_val, y_val, y_full):
        previous_artifact = None
        previous_model = None
        previous_accuracy = None
        previous_mae = None
        previous_predictions = None

        if not os.path.exists(model_path):
            return previous_artifact, previous_model, previous_accuracy, previous_mae, previous_predictions

        try:
            existing_artifact = joblib.load(model_path)
            if not isinstance(existing_artifact, dict):
                return previous_artifact, previous_model, previous_accuracy, previous_mae, previous_predictions

            previous_artifact = existing_artifact
            previous_model = existing_artifact.get("model")
            if previous_model is None:
                return previous_artifact, previous_model, previous_accuracy, previous_mae, previous_predictions

            previous_predictions = self._predict_with_artifact(existing_artifact, X_val)
            previous_mae, live_accuracy = self._score_predictions(y_val, previous_predictions, y_full)
            previous_accuracy = self._baseline_accuracy(existing_artifact, live_accuracy)
            if previous_accuracy is not None and previous_mae is not None:
                return previous_artifact, previous_model, previous_accuracy, previous_mae, previous_predictions
        except Exception:
            pass

        return None, None, None, None, None

    def _find_best_blend(self, candidate_predictions, previous_predictions, y_val, y_full):
        best = None
        step = self.blend_step if self.blend_step > 0 else 0.1
        weight = step
        while weight < 1.0:
            blend_pred = (weight * candidate_predictions) + ((1.0 - weight) * previous_predictions)
            mae, accuracy = self._score_predictions(y_val, blend_pred, y_full)
            if best is None or accuracy > best["accuracy"]:
                best = {
                    "weight": float(weight),
                    "mae": float(mae),
                    "accuracy": float(accuracy),
                }
            weight = round(weight + step, 10)
        return best

    def _train_recursive(
        self,
        X_train,
        y_train,
        X_val,
        y_val,
        y_full,
        attempt: int = 1,
        best_result: dict | None = None,
        floor_accuracy: float | None = None,
        training_target: float | None = None,
    ):
        model, mae, accuracy, predictions = self._fit_and_score_model(X_train, y_train, X_val, y_val, y_full, attempt)
        target = float(training_target if training_target is not None else self.target_accuracy)
        floor = float(floor_accuracy) if floor_accuracy is not None else None

        current = {
            "model": model,
            "mae": mae,
            "accuracy": accuracy,
            "attempt": attempt,
            "predictions": predictions,
        }

        if best_result is None or accuracy > best_result["accuracy"]:
            best_result = current

        beat_floor = floor is None or accuracy >= floor
        if beat_floor and accuracy >= target:
            return {
                **current,
                "reached_target": True,
                "attempts": attempt,
            }

        if attempt >= self.max_train_attempts:
            return {
                **best_result,
                "reached_target": bool(
                    best_result["accuracy"] >= target
                    and (floor is None or best_result["accuracy"] >= floor)
                ),
                "attempts": attempt,
            }

        return self._train_recursive(
            X_train,
            y_train,
            X_val,
            y_val,
            y_full,
            attempt=attempt + 1,
            best_result=best_result,
            floor_accuracy=floor_accuracy,
            training_target=training_target,
        )

    def train_model(self, game: str):
        if self._uses_modular_engine():
            from services.prediction_adapter import prediction_adapter

            baseline = self._load_stored_baseline(game)
            refresh_only = baseline is not None
            return prediction_adapter.train_or_backtest(
                game,
                refresh_only=refresh_only,
                baseline_accuracy=baseline,
            )

        from .chroma_client import chroma_client

        collection = chroma_client.client.get_collection(game)
        metadatas = self._load_all_metadatas(collection)
        if not metadatas:
            return {"status": "error", "message": "No data found to train on."}

        metadatas = self._sort_metadatas_chronologically(metadatas)
        window_size = max(1, int(self.window_size or 1))
        sequences = self._extract_winning_sequences(metadatas, game)
        X, y, feature_len, output_len = self._build_supervised_dataset(sequences, window_size)
        if X is None or len(X) < 10:
            return {"status": "error", "message": "Not enough parsed winning-number sequences to train."}

        model_path = os.path.join(self.models_dir, f"{game}_model.joblib")
        requested_target = float(self.target_accuracy)
        baseline_accuracy = self._resolve_baseline_accuracy(game, None)
        reported_previous_accuracy = float(baseline_accuracy) if baseline_accuracy is not None else None
        training_target = max(requested_target, baseline_accuracy or 0.0)

        best_run = None
        total_attempts = 0
        for train_size in self._train_size_candidates():
            train_size = min(max(float(train_size), 0.10), 0.50)
            val_size = 1.0 - train_size
            X_train, X_val, y_train, y_val = self._chronological_split(X, y, train_size)
            (
                previous_artifact,
                previous_model,
                previous_accuracy,
                previous_mae,
                previous_predictions,
            ) = self._load_previous_state(model_path, X_val, y_val, y)

            if baseline_accuracy is None and previous_accuracy is not None:
                baseline_accuracy = self._resolve_baseline_accuracy(game, previous_accuracy)
                reported_previous_accuracy = (
                    float(baseline_accuracy) if baseline_accuracy is not None else previous_accuracy
                )
                training_target = max(requested_target, baseline_accuracy or 0.0)

            candidates = self._train_iterative_collect(
                X_train,
                y_train,
                X_val,
                y_val,
                y,
                training_target=training_target,
                floor_accuracy=baseline_accuracy,
            )
            if previous_model is not None and previous_predictions is not None:
                try:
                    prev_mae, prev_acc = self._score_predictions(y_val, previous_predictions, y)
                    candidates.append(
                        {
                            "model": previous_model,
                            "mae": float(prev_mae),
                            "accuracy": float(prev_acc),
                            "attempt": 0,
                            "predictions": np.asarray(previous_predictions, dtype=float),
                        }
                    )
                except Exception:
                    pass

            if not candidates:
                continue

            total_attempts += len(candidates)
            selected_candidate = self._select_best_candidate(candidates, y_val, y)
            if selected_candidate is None:
                continue

            run_payload = {
                **selected_candidate,
                "train_size": train_size,
                "validation_size": val_size,
                "X_val": X_val,
                "y_val": y_val,
                "previous_artifact": previous_artifact,
                "previous_model": previous_model,
                "previous_accuracy": previous_accuracy,
                "previous_mae": previous_mae,
                "previous_predictions": previous_predictions,
                "attempts": len(candidates),
            }
            if best_run is None or float(run_payload["accuracy"]) > float(best_run["accuracy"]):
                best_run = run_payload

        if best_run is None:
            return {"status": "error", "message": "No successful training attempts were completed."}

        train_size = float(best_run["train_size"])
        val_size = float(best_run["validation_size"])
        X_val = best_run["X_val"]
        y_val = best_run["y_val"]
        previous_artifact = best_run["previous_artifact"]
        previous_model = best_run["previous_model"]
        previous_accuracy = best_run["previous_accuracy"]
        previous_mae = best_run["previous_mae"]
        previous_predictions = best_run["previous_predictions"]

        candidate_model = best_run["model"]
        candidate_predictions = np.asarray(best_run.get("predictions"), dtype=float)
        candidate_mae = float(best_run["mae"])
        candidate_accuracy = float(best_run["accuracy"])
        candidate_strategy = best_run["strategy"]
        candidate_top_models = best_run.get("top_models")
        candidate_ensemble_weights = best_run.get("ensemble_weights")
        candidate_top_k_accuracies = best_run.get("top_k_accuracies")

        train_result = {
            "model": candidate_model,
            "mae": candidate_mae,
            "accuracy": candidate_accuracy,
            "predictions": candidate_predictions,
            "attempts": total_attempts,
            "reached_target": bool(
                candidate_accuracy >= training_target
                and (baseline_accuracy is None or candidate_accuracy >= baseline_accuracy)
            ),
        }

        try:
            if candidate_strategy == "ensemble_top3" and candidate_top_models:
                full_dataset_predictions = self._ensemble_predictions(
                    candidate_top_models,
                    candidate_ensemble_weights or [],
                    X,
                )
            else:
                full_dataset_predictions = candidate_model.predict(X)
            full_dataset_mae, full_dataset_accuracy = self._score_predictions(
                y, full_dataset_predictions, y
            )
        except Exception:
            full_dataset_mae = None
            full_dataset_accuracy = None

        selected_strategy = candidate_strategy
        selected_model = candidate_model
        selected_secondary_model = None
        selected_blend_weight = None
        selected_top_models = candidate_top_models
        selected_ensemble_weights = candidate_ensemble_weights
        selected_top_k_accuracies = candidate_top_k_accuracies
        selected_accuracy = candidate_accuracy
        selected_mae = candidate_mae
        retained_previous_model = False
        used_previous_training = previous_predictions is not None
        floor_accuracy = float(baseline_accuracy) if baseline_accuracy is not None else None

        blend_candidate = None
        if (
            candidate_strategy != "ensemble_top3"
            and previous_predictions is not None
            and previous_model is not None
        ):
            blend_candidate = self._find_best_blend(candidate_predictions, previous_predictions, y_val, y)

        blend_accuracy = float(blend_candidate["accuracy"]) if blend_candidate else None
        blend_beats_floor = floor_accuracy is None or (
            blend_accuracy is not None and blend_accuracy >= floor_accuracy
        )
        if (
            blend_candidate is not None
            and previous_model is not None
            and blend_accuracy is not None
            and blend_beats_floor
            and blend_accuracy > selected_accuracy
        ):
            selected_strategy = "ensemble"
            selected_model = candidate_model
            selected_secondary_model = previous_model
            selected_blend_weight = float(blend_candidate["weight"])
            selected_top_models = None
            selected_ensemble_weights = None
            selected_top_k_accuracies = None
            selected_accuracy = blend_accuracy
            selected_mae = float(blend_candidate["mae"])
            retained_previous_model = False

        if floor_accuracy is not None and selected_accuracy < floor_accuracy:
            return {
                "status": "error",
                "message": (
                    f"Training accuracy {candidate_accuracy:.4f} is below the record floor "
                    f"{floor_accuracy:.4f}. Model was not saved to prevent regression."
                ),
                "accuracy": floor_accuracy,
                "highest_accuracy": floor_accuracy,
                "record_accuracy": floor_accuracy,
                "baseline_accuracy": float(baseline_accuracy) if baseline_accuracy is not None else None,
                "candidate_accuracy": float(candidate_accuracy),
                "previous_accuracy": float(reported_previous_accuracy)
                if reported_previous_accuracy is not None
                else None,
                "target_accuracy": requested_target,
                "training_target": training_target,
                "retained_previous_model": False,
                "model_strategy": str(selected_strategy),
                "train_size": train_size,
                "validation_size": val_size,
                "training_params": snapshot_from_trainer(
                    self,
                    train_size=train_size,
                    validation_size=val_size,
                    target_accuracy=requested_target,
                    training_target=training_target,
                    model_strategy=selected_strategy,
                    blend_weight=selected_blend_weight,
                ),
            }

        artifact = {
            "model": selected_model,
            "secondary_model": selected_secondary_model,
            "blend_weight": selected_blend_weight,
            "model_strategy": selected_strategy,
            "top_models": selected_top_models,
            "ensemble_weights": selected_ensemble_weights,
            "feature_len": feature_len,
            "output_len": output_len,
            "game": game,
            "rules": self._get_rules(game),
            "version": 4,
            "window_size": int(window_size),
            "metrics": {
                "accuracy": selected_accuracy,
                "mae": selected_mae,
                "attempts": int(train_result.get("attempts", 1)),
                "reached_target": bool(train_result.get("reached_target", False)),
                "target_accuracy": requested_target,
                "training_target": training_target,
                "baseline_accuracy": baseline_accuracy,
                "train_size": train_size,
                "validation_size": val_size,
                "n_estimators": int(self.n_estimators),
                "max_depth": int(self.max_depth),
                "random_state": int(self.random_state),
                "records_loaded": len(metadatas),
                "window_size": int(window_size),
                "auto_tune": bool(self.auto_tune),
                "split_strategy": "chronological",
                "candidate_accuracy": candidate_accuracy,
                "candidate_mae": candidate_mae,
                "previous_accuracy": reported_previous_accuracy,
                "previous_mae": previous_mae,
                "used_previous_training": used_previous_training,
                "model_strategy": selected_strategy,
                "blend_weight": selected_blend_weight,
                "top_k_accuracies": selected_top_k_accuracies,
                "full_dataset_accuracy": full_dataset_accuracy,
                "full_dataset_mae": full_dataset_mae,
            },
        }

        prior_record = self._load_stored_baseline(game)
        highest_accuracy = float(selected_accuracy) if selected_accuracy is not None else None
        if prior_record is not None:
            highest_accuracy = max(
                [value for value in (highest_accuracy, float(prior_record)) if value is not None],
                default=prior_record,
            )
        record_accuracy = highest_accuracy
        experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
        os.makedirs(experiments_dir, exist_ok=True)
        metadata_path = os.path.join(experiments_dir, f"{game}_model_metadata.json")
        existing_meta: dict = {}
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        existing_meta = loaded
            except Exception:
                existing_meta = {}

        from experiments.store import update_accuracy_history

        accuracy_history = update_accuracy_history(
            existing_meta.get("accuracy_history"),
            highest_accuracy,
        )
        run_training_params = snapshot_from_trainer(
            self,
            train_size=train_size,
            validation_size=val_size,
            target_accuracy=requested_target,
            training_target=training_target,
            model_strategy=selected_strategy,
            blend_weight=selected_blend_weight,
        )
        existing_best_params = merge_training_params(
            existing_meta.get("best_training_params"),
            extract_training_params(existing_meta),
            extract_training_params((previous_artifact or {}).get("metrics") or {}),
        )
        if retained_previous_model:
            best_training_params = existing_best_params or run_training_params
        else:
            best_training_params = run_training_params

        recent_runs = list(existing_meta.get("recent_runs") or [])
        recent_runs.insert(
            0,
            {
                "timestamp": time.time(),
                "accuracy": highest_accuracy,
                "candidate_accuracy": float(candidate_accuracy) if candidate_accuracy is not None else None,
                "retained_previous_model": bool(retained_previous_model),
                "training_params": run_training_params,
            },
        )
        recent_runs = recent_runs[:3]

        metadata_to_save = {
            "game": game,
            "model_type": "RandomForestRegressor",
            "accuracy": highest_accuracy,
            "highest_accuracy": highest_accuracy,
            "record_accuracy": record_accuracy,
            "mae": float(selected_mae) if selected_mae is not None else None,
            "feature_len": int(feature_len),
            "output_len": int(output_len),
            "samples": int(len(X)),
            "target_accuracy": float(requested_target),
            "training_target": float(training_target),
            "baseline_accuracy": float(baseline_accuracy) if baseline_accuracy is not None else None,
            "attempts": int(train_result.get("attempts", 1)),
            "reached_target": bool(train_result.get("reached_target", False)) or bool(
                highest_accuracy is not None and highest_accuracy >= training_target
            ),
            "retained_previous_model": bool(retained_previous_model),
            "used_previous_training": bool(used_previous_training),
            "model_strategy": str(selected_strategy),
            "blend_weight": float(selected_blend_weight) if selected_blend_weight is not None else None,
            "candidate_accuracy": float(candidate_accuracy) if candidate_accuracy is not None else None,
            "previous_accuracy": float(reported_previous_accuracy)
            if reported_previous_accuracy is not None
            else None,
            "train_size": float(train_size),
            "validation_size": float(val_size),
            "n_estimators": int(best_training_params.get("n_estimators") or self.n_estimators),
            "max_depth": int(best_training_params.get("max_depth") or self.max_depth),
            "random_state": int(best_training_params.get("random_state") or self.random_state),
            "window_size": int(best_training_params.get("window_size") or window_size),
            "blend_step": float(best_training_params.get("blend_step") or self.blend_step),
            "auto_tune": bool(
                best_training_params.get("auto_tune")
                if best_training_params.get("auto_tune") is not None
                else self.auto_tune
            ),
            "max_iterations": int(
                best_training_params.get("max_iterations") or self.max_train_attempts
            ),
            "data_limit": best_training_params.get("data_limit"),
            "training_params": run_training_params,
            "best_training_params": best_training_params,
            "last_trained_at": time.time(),
            "last_trained_record_count": len(metadatas),
            "accuracy_history": accuracy_history,
            "recent_runs": recent_runs,
        }
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata_to_save, f, indent=2)
        except Exception as exc:
            print(f"WARNING: Failed to save model metadata JSON for {game}: {exc}")

        if retained_previous_model:
            return {
                "status": "success",
                "message": (
                    f"Training completed for {game}. Using highest current accuracy "
                    f"({reported_previous_accuracy:.4f}) — new candidate ({candidate_accuracy:.4f}) did not improve."
                ),
                "accuracy": highest_accuracy,
                "highest_accuracy": highest_accuracy,
                "record_accuracy": record_accuracy,
                "previous_accuracy": reported_previous_accuracy,
                "candidate_accuracy": candidate_accuracy,
                "new_accuracy": candidate_accuracy,
                "mae": selected_mae,
                "model_path": model_path,
                "feature_len": feature_len,
                "output_len": output_len,
                "samples": int(len(X)),
                "target_accuracy": requested_target,
                "training_target": training_target,
                "baseline_accuracy": baseline_accuracy,
                "attempts": int(train_result.get("attempts", 1)),
                "reached_target": bool(
                    highest_accuracy is not None and highest_accuracy >= training_target
                ),
                "retained_previous_model": True,
                "used_previous_training": used_previous_training,
                "model_strategy": selected_strategy,
                "blend_weight": selected_blend_weight,
                "train_size": train_size,
                "validation_size": val_size,
                "training_params": run_training_params,
                "best_training_params": best_training_params,
            }

        temp_model_path = f"{model_path}.tmp"
        joblib.dump(artifact, temp_model_path, compress=3)
        if not os.path.exists(temp_model_path) or os.path.getsize(temp_model_path) <= 0:
            return {"status": "error", "message": f"Failed to persist model artifact for {game}."}
        os.replace(temp_model_path, model_path)

        return {
            "status": "success",
            "message": (
                f"Trained {game} model with highest current accuracy "
                f"({selected_accuracy:.4f})."
            ),
            "accuracy": highest_accuracy,
            "highest_accuracy": highest_accuracy,
            "record_accuracy": record_accuracy,
            "mae": selected_mae,
            "model_path": model_path,
            "feature_len": feature_len,
            "output_len": output_len,
            "samples": int(len(X)),
            "target_accuracy": requested_target,
            "training_target": training_target,
            "baseline_accuracy": baseline_accuracy,
            "attempts": int(train_result.get("attempts", 1)),
            "reached_target": bool(
                train_result.get("reached_target", False)
                or (selected_accuracy is not None and selected_accuracy >= training_target)
            ),
            "retained_previous_model": False,
            "used_previous_training": used_previous_training,
            "model_strategy": selected_strategy,
            "blend_weight": selected_blend_weight,
            "candidate_accuracy": candidate_accuracy,
            "previous_accuracy": reported_previous_accuracy,
            "train_size": train_size,
            "validation_size": val_size,
            "training_params": run_training_params,
            "best_training_params": best_training_params,
        }

    def train(
        self,
        game: str,
        target_accuracy: float = None,
        max_iterations: int = None,
        train_size: float = None,
        n_estimators: int = None,
        max_depth: int = None,
        random_state: int = None,
        blend_step: float = None,
        data_limit: int = None,
        window_size: int = None,
        auto_tune: bool = None,
    ):
        """Backward-compatible alias used by API routes and tooling."""
        import logging
        from experiments.store import (
            MAX_STORED_PER_GAME,
            ExperimentStore,
            _experiment_accuracy,
            update_accuracy_history,
        )

        logger = logging.getLogger(__name__)
        started_at = time.time()
        game_key = str(game or "").strip().lower()

        caller_overrides = {
            "target_accuracy": target_accuracy,
            "max_iterations": max_iterations,
            "train_size": train_size,
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "random_state": random_state,
            "blend_step": blend_step,
            "data_limit": data_limit,
            "window_size": window_size,
            "auto_tune": auto_tune,
        }

        def _coerce_float(value):
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _coerce_int(value):
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def _config_from_mapping(source: str, accuracy, mapping: dict) -> dict | None:
            if not isinstance(mapping, dict):
                return None
            resolved_accuracy = _coerce_float(accuracy)
            if resolved_accuracy is None:
                for key in ("highest_accuracy", "accuracy", "final_accuracy", "score"):
                    resolved_accuracy = _coerce_float(mapping.get(key))
                    if resolved_accuracy is not None:
                        break
            if resolved_accuracy is None and source == "precision_first_defaults":
                resolved_accuracy = _coerce_float(self._load_stored_baseline(game_key))
            stored_params = extract_training_params(mapping)
            config = {
                "source": source,
                "accuracy": resolved_accuracy,
                "target_accuracy": _coerce_float(
                    stored_params.get("training_target")
                    or stored_params.get("target_accuracy")
                    or mapping.get("training_target")
                    or mapping.get("target_accuracy")
                ),
                "train_size": _coerce_float(stored_params.get("train_size") or mapping.get("train_size")),
                "n_estimators": _coerce_int(stored_params.get("n_estimators") or mapping.get("n_estimators")),
                "max_depth": _coerce_int(stored_params.get("max_depth") or mapping.get("max_depth")),
                "random_state": _coerce_int(stored_params.get("random_state") or mapping.get("random_state")),
                "window_size": _coerce_int(stored_params.get("window_size") or mapping.get("window_size")),
                "blend_step": _coerce_float(stored_params.get("blend_step") or mapping.get("blend_step")),
                "auto_tune": stored_params.get("auto_tune", mapping.get("auto_tune")),
                "max_iterations": _coerce_int(
                    stored_params.get("max_iterations")
                    or mapping.get("max_iterations")
                    or mapping.get("max_train_attempts")
                ),
                "model_strategy": stored_params.get("model_strategy") or mapping.get("model_strategy"),
                "blend_weight": _coerce_float(stored_params.get("blend_weight") or mapping.get("blend_weight")),
                "data_limit": _coerce_int(stored_params.get("data_limit") or mapping.get("data_limit")),
                "training_params": stored_params,
            }
            if resolved_accuracy is None and all(
                config.get(key) is None
                for key in (
                    "target_accuracy",
                    "train_size",
                    "n_estimators",
                    "max_depth",
                    "random_state",
                    "window_size",
                    "blend_step",
                    "max_iterations",
                )
            ):
                return None
            return config

        def _get_dataset_record_count() -> int:
            try:
                from .chroma_client import chroma_client

                return max(0, int(chroma_client.count_documents(game_key) or 0))
            except Exception as exc:
                logger.warning("Unable to count dataset records for %s: %s", game_key, exc)
                return 0

        def _precision_first_defaults(record_count: int) -> dict:
            defaults = self.get_training_defaults()
            baseline = self._load_stored_baseline(game_key)
            if record_count > 3000:
                n_estimators = 120
                max_depth = 14
                max_iterations = 10
                auto_tune = False
            elif record_count > 1500:
                n_estimators = 150
                max_depth = 16
                max_iterations = 12
                auto_tune = False
            else:
                n_estimators = min(max(int(defaults["n_estimators"]), 450), 600)
                max_depth = min(max(int(defaults["max_depth"]), 28), 32)
                max_iterations = defaults["max_iterations"]
                auto_tune = defaults["auto_tune"]
            return _config_from_mapping(
                "precision_first_defaults",
                baseline,
                {
                    "target_accuracy": max(
                        float(defaults["target_accuracy"]),
                        float(baseline or 0.0),
                    ),
                    "train_size": defaults["train_size"],
                    "n_estimators": n_estimators,
                    "max_depth": max_depth,
                    "random_state": defaults["random_state"],
                    "window_size": defaults["window_size"],
                    "blend_step": defaults["blend_step"],
                    "auto_tune": auto_tune,
                    "max_iterations": max_iterations,
                },
            )

        def _load_game_optimal_config(record_count: int) -> dict:
            """Load the highest-accuracy known configuration for this game."""
            import gc

            candidates: list[dict] = []
            experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
            metadata_path = os.path.join(experiments_dir, f"{game_key}_model_metadata.json")

            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r") as handle:
                        metadata = json.load(handle)
                    candidate = _config_from_mapping(
                        "metadata",
                        None,
                        {
                            **metadata,
                            **(metadata.get("best_training_params") or {}),
                        },
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                    for item in metadata.get("score_leaderboard") or []:
                        if not isinstance(item, dict):
                            continue
                        board_candidate = _config_from_mapping(
                            "score_leaderboard",
                            item.get("accuracy"),
                            item,
                        )
                        if board_candidate is not None:
                            candidates.append(board_candidate)
                except Exception as exc:
                    logger.warning(
                        "Unable to read optimal config metadata for %s: %s",
                        game_key,
                        exc,
                    )

            experiments_path = os.path.join(experiments_dir, "experiments.json")
            if os.path.exists(experiments_path):
                try:
                    store = ExperimentStore(experiments_path)
                    for experiment in store.list_experiments():
                        if str(experiment.get("game") or "").strip().lower() != game_key:
                            continue
                        if str(experiment.get("type") or "").lower() != "training":
                            continue
                        candidate = _config_from_mapping(
                            "experiment",
                            _experiment_accuracy(experiment),
                            experiment,
                        )
                        if candidate is not None:
                            candidates.append(candidate)
                except Exception as exc:
                    logger.warning(
                        "Unable to read experiment leaderboard config for %s: %s",
                        game_key,
                        exc,
                    )

            # Avoid loading full model artifacts on large games — can OOM before training starts.
            if record_count <= 1500:
                model_path = os.path.join(self.models_dir, f"{game_key}_model.joblib")
                if os.path.exists(model_path):
                    try:
                        artifact = joblib.load(model_path)
                        if isinstance(artifact, dict):
                            metrics = artifact.get("metrics") or {}
                            merged = {**metrics, **artifact}
                            candidate = _config_from_mapping("model_artifact", None, merged)
                            if candidate is not None:
                                candidates.append(candidate)
                    except Exception as exc:
                        logger.warning(
                            "Unable to read optimal config from model artifact for %s: %s",
                            game_key,
                            exc,
                        )
                    finally:
                        gc.collect()

            if not candidates:
                fallback = _precision_first_defaults(record_count)
                logger.info(
                    "No stored optimal config for %s; using precision-first defaults",
                    game_key,
                )
                return fallback or {}

            candidates.sort(
                key=lambda item: (
                    float(item.get("accuracy") if item.get("accuracy") is not None else -1.0),
                    float(item.get("timestamp") or 0.0),
                ),
                reverse=True,
            )
            best = candidates[0]
            logger.info(
                "Preloaded optimal config for %s from %s (accuracy=%s)",
                game_key,
                best.get("source"),
                best.get("accuracy"),
            )
            return best

        def _apply_config(config: dict):
            configure_kwargs = {
                "target_accuracy": config.get("target_accuracy"),
                "max_iterations": config.get("max_iterations"),
                "train_size": config.get("train_size"),
                "n_estimators": config.get("n_estimators"),
                "max_depth": config.get("max_depth"),
                "random_state": config.get("random_state"),
                "blend_step": config.get("blend_step"),
                "data_limit": data_limit,
                "window_size": config.get("window_size"),
                "auto_tune": config.get("auto_tune"),
            }
            self.configure_training(
                **{key: value for key, value in configure_kwargs.items() if value is not None}
            )

        def _apply_memory_safe_caps(record_count: int, *, skip: bool = False) -> dict:
            """Cap heavy training knobs on large datasets to reduce backend OOM (HTTP 502)."""
            profile = {"record_count": record_count, "applied": False, "caps": {}}
            if skip or record_count <= 1500:
                return profile

            record = _coerce_float(self._load_stored_baseline(game_key))

            if record_count > 3000:
                caps = {
                    "max_iterations": 12 if record and record >= 0.85 else 10,
                    "n_estimators": 150 if record and record >= 0.85 else 120,
                    "max_depth": 14,
                    "auto_tune": False,
                    "data_limit": 2500,
                }
            else:
                caps = {
                    "max_iterations": 15 if record and record >= 0.85 else 12,
                    "n_estimators": 180 if record and record >= 0.85 else 150,
                    "max_depth": 16,
                    "auto_tune": False,
                    "data_limit": 3500,
                }

            applied: dict = {}
            if self.max_train_attempts > caps["max_iterations"]:
                applied["max_iterations"] = caps["max_iterations"]
                self.max_train_attempts = caps["max_iterations"]
            if self.n_estimators > caps["n_estimators"]:
                applied["n_estimators"] = caps["n_estimators"]
                self.n_estimators = caps["n_estimators"]
            if self.max_depth > caps["max_depth"]:
                applied["max_depth"] = caps["max_depth"]
                self.max_depth = caps["max_depth"]
            if self.auto_tune and caps["auto_tune"] is False:
                applied["auto_tune"] = False
                self.auto_tune = False
            if (not self.data_limit or self.data_limit <= 0) and data_limit is None:
                applied["data_limit"] = caps["data_limit"]
                self.data_limit = caps["data_limit"]

            if applied:
                profile["applied"] = True
                profile["caps"] = applied
                logger.warning(
                    "Applied memory-safe training caps for %s (%s records): %s",
                    game_key,
                    record_count,
                    applied,
                )
            return profile

        def _load_existing_leaderboard() -> list[dict]:
            experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
            metadata_path = os.path.join(experiments_dir, f"{game_key}_model_metadata.json")
            entries: list[dict] = []

            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r") as handle:
                        metadata = json.load(handle)
                    for item in metadata.get("score_leaderboard") or []:
                        if isinstance(item, dict) and item.get("accuracy") is not None:
                            entries.append(dict(item))
                    for accuracy in metadata.get("accuracy_history") or []:
                        value = _coerce_float(accuracy)
                        if value is not None:
                            entries.append({"accuracy": value, "timestamp": 0.0, "source": "history"})
                except Exception as exc:
                    logger.warning("Unable to load leaderboard for %s: %s", game_key, exc)

            experiments_path = os.path.join(experiments_dir, "experiments.json")
            if os.path.exists(experiments_path):
                try:
                    store = ExperimentStore(experiments_path)
                    for experiment in store.list_experiments():
                        if str(experiment.get("game") or "").strip().lower() != game_key:
                            continue
                        if str(experiment.get("type") or "").lower() != "training":
                            continue
                        accuracy = _experiment_accuracy(experiment)
                        if accuracy is None:
                            continue
                        entry = normalize_leaderboard_entry(
                            {
                                "accuracy": float(accuracy),
                                "timestamp": float(experiment.get("timestamp") or 0.0),
                                "source": "experiment",
                                "training_params": extract_training_params(experiment),
                                **extract_training_params(experiment),
                                "model_strategy": experiment.get("model_strategy"),
                            },
                            default_timestamp=float(experiment.get("timestamp") or 0.0),
                        )
                        if entry:
                            entries.append(entry)
                except Exception as exc:
                    logger.warning(
                        "Unable to load experiment scores for %s: %s",
                        game_key,
                        exc,
                    )

            return entries

        def _update_leaderboard(existing_entries: list[dict], new_entry: dict | None) -> list[dict]:
            merged: list[dict] = []
            for item in existing_entries or []:
                normalized = normalize_leaderboard_entry(
                    item,
                    default_timestamp=float(item.get("timestamp") or 0.0),
                )
                if normalized:
                    merged.append(normalized)
            if isinstance(new_entry, dict) and new_entry.get("accuracy") is not None:
                normalized_new = normalize_leaderboard_entry(
                    new_entry,
                    default_timestamp=time.time(),
                )
                if normalized_new:
                    merged.append(normalized_new)
            merged.sort(
                key=lambda item: (
                    float(item.get("accuracy") if item.get("accuracy") is not None else -1.0),
                    float(item.get("timestamp") or 0.0),
                ),
                reverse=True,
            )
            trimmed: list[dict] = []
            seen_accuracy: list[float] = []
            for item in merged:
                accuracy = _coerce_float(item.get("accuracy"))
                if accuracy is None:
                    continue
                if any(abs(accuracy - seen) <= 1e-9 for seen in seen_accuracy):
                    continue
                seen_accuracy.append(accuracy)
                trimmed.append(item)
                if len(trimmed) >= MAX_STORED_PER_GAME:
                    break
            return trimmed

        def _persist_leaderboard(
            leaderboard: list[dict],
            accuracy_history: list[float],
            *,
            trained_record_count: int | None = None,
            trained_dataset_hash: str | None = None,
        ):
            experiments_dir = os.path.join(os.path.dirname(self.models_dir), "experiments")
            os.makedirs(experiments_dir, exist_ok=True)
            metadata_path = os.path.join(experiments_dir, f"{game_key}_model_metadata.json")
            metadata: dict = {}
            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r") as handle:
                        loaded = json.load(handle)
                        if isinstance(loaded, dict):
                            metadata = loaded
                except Exception:
                    metadata = {}
            metadata["score_leaderboard"] = leaderboard
            metadata["accuracy_history"] = accuracy_history
            if trained_record_count is not None and trained_record_count > 0:
                metadata["last_trained_record_count"] = int(trained_record_count)
                metadata["last_trained_at"] = time.time()
                if trained_dataset_hash:
                    metadata["last_trained_dataset_hash"] = trained_dataset_hash
            top_params = top_scored_params(leaderboard)
            if top_params:
                metadata["best_training_params"] = top_params
                for key, value in top_params.items():
                    if value is not None:
                        metadata[key] = value
            try:
                with open(metadata_path, "w") as handle:
                    json.dump(metadata, handle, indent=2)
            except Exception as exc:
                logger.warning("Failed to persist leaderboard for %s: %s", game_key, exc)

        def _build_response(
            result: dict,
            *,
            leaderboard: list[dict],
            optimal_config: dict,
            optimal_config_applied: bool,
            accuracy_history: list[float],
            memory_profile: dict | None = None,
            status_override: str | None = None,
        ) -> dict:
            status = status_override or str(result.get("status") or "error")
            response = {
                "status": status,
                "game": game_key,
                "message": result.get("message", ""),
                "training_time": round(time.time() - started_at, 3),
                "leaderboard": leaderboard,
                "accuracy_history": accuracy_history,
                "optimal_config_applied": bool(optimal_config_applied),
                "optimal_config": {
                    key: optimal_config.get(key)
                    for key in (
                        "source",
                        "accuracy",
                        "target_accuracy",
                        "train_size",
                        "n_estimators",
                        "max_depth",
                        "random_state",
                        "window_size",
                        "blend_step",
                        "auto_tune",
                        "max_iterations",
                        "model_strategy",
                    )
                },
                "memory_profile": memory_profile or {},
            }
            for key in (
                "accuracy",
                "highest_accuracy",
                "mae",
                "model_path",
                "feature_len",
                "output_len",
                "samples",
                "target_accuracy",
                "training_target",
                "baseline_accuracy",
                "attempts",
                "reached_target",
                "retained_previous_model",
                "used_previous_training",
                "model_strategy",
                "blend_weight",
                "candidate_accuracy",
                "previous_accuracy",
                "record_accuracy",
                "new_accuracy",
                "train_size",
                "validation_size",
                "training_params",
                "best_training_params",
            ):
                if key in result and result.get(key) is not None:
                    response[key] = result.get(key)
            if response.get("accuracy") is not None and "score" not in response:
                response["score"] = response.get("accuracy")
            return response

        if not game_key:
            logger.error("Training requested without a valid game key")
            return _build_response(
                {"status": "error", "message": "A valid game key is required."},
                leaderboard=[],
                optimal_config={},
                optimal_config_applied=False,
                accuracy_history=[],
            )

        optimal_config: dict = {}
        optimal_config_applied = False
        leaderboard: list[dict] = []
        accuracy_history: list[float] = []
        memory_profile: dict = {}

        try:
            record_count = _get_dataset_record_count()
            logger.info("Starting training for game=%s records=%s", game_key, record_count)
            optimal_config = _load_game_optimal_config(record_count)
            if optimal_config:
                _apply_config(optimal_config)
                optimal_config_applied = True
            self.configure_training(
                **{key: value for key, value in caller_overrides.items() if value is not None}
            )
            record_baseline = _coerce_float(self._load_stored_baseline(game_key))
            if record_baseline is not None:
                self.target_accuracy = max(float(self.target_accuracy or 0.0), record_baseline)
            memory_profile = _apply_memory_safe_caps(record_count)
            logger.info(
                "Training %s with n_estimators=%s max_depth=%s train_size=%s "
                "target_accuracy=%s max_iterations=%s auto_tune=%s data_limit=%s",
                game_key,
                self.n_estimators,
                self.max_depth,
                self.train_size,
                self.target_accuracy,
                self.max_train_attempts,
                self.auto_tune,
                self.data_limit,
            )

            existing_leaderboard = _load_existing_leaderboard()
            result = self.train_model(game_key)

            if self._uses_modular_engine() and str(result.get("status", "")).lower() == "success":
                result = self._enrich_modular_train_result(result, record_before=record_baseline)

            record_before = _coerce_float(self._load_stored_baseline(game_key))
            candidate_acc = _coerce_float(result.get("candidate_accuracy"))
            should_recreate = (
                not self._uses_modular_engine()
                and str(result.get("status", "")).lower() == "success"
                and bool(result.get("retained_previous_model"))
                and record_before is not None
                and candidate_acc is not None
                and candidate_acc + 1e-9 < record_before
                and optimal_config
                and not memory_profile.get("recreate_attempted")
            )
            if should_recreate:
                logger.info(
                    "Recreate-accuracy retry for %s (candidate=%.4f record=%.4f)",
                    game_key,
                    candidate_acc,
                    record_before,
                )
                memory_profile["recreate_attempted"] = True
                _apply_config(optimal_config)
                self.target_accuracy = max(float(self.target_accuracy or 0.0), record_before)
                recreate_result = self.train_model(game_key)
                recreate_acc = _coerce_float(
                    recreate_result.get("highest_accuracy") or recreate_result.get("accuracy")
                )
                prior_acc = _coerce_float(result.get("highest_accuracy") or result.get("accuracy"))
                if (
                    str(recreate_result.get("status", "")).lower() == "success"
                    and recreate_acc is not None
                    and (prior_acc is None or recreate_acc + 1e-9 >= prior_acc)
                ):
                    result = recreate_result
                    memory_profile["recreate_succeeded"] = True
                else:
                    memory_profile["recreate_succeeded"] = False

            result_accuracy = _coerce_float(
                result.get("highest_accuracy") or result.get("accuracy")
            )
            new_leaderboard_entry = None
            if result_accuracy is not None:
                scored_params = merge_training_params(
                    result.get("best_training_params"),
                    result.get("training_params"),
                    {
                        "train_size": result.get("train_size"),
                        "validation_size": result.get("validation_size"),
                        "n_estimators": self.n_estimators,
                        "max_depth": self.max_depth,
                        "random_state": self.random_state,
                        "window_size": self.window_size,
                        "blend_step": self.blend_step,
                        "auto_tune": self.auto_tune,
                        "max_iterations": self.max_train_attempts,
                        "data_limit": self.data_limit,
                        "target_accuracy": result.get("target_accuracy"),
                        "training_target": result.get("training_target"),
                        "model_strategy": result.get("model_strategy"),
                        "blend_weight": result.get("blend_weight"),
                    },
                )
                new_leaderboard_entry = {
                    "accuracy": result_accuracy,
                    "timestamp": time.time(),
                    "source": "train",
                    "training_params": scored_params,
                    **scored_params,
                }
            leaderboard = _update_leaderboard(existing_leaderboard, new_leaderboard_entry)
            accuracy_history = update_accuracy_history(
                [
                    item.get("accuracy")
                    for item in existing_leaderboard
                    if item.get("accuracy") is not None
                ],
                result_accuracy,
                limit=MAX_STORED_PER_GAME,
            )
            _persist_leaderboard(
                leaderboard,
                accuracy_history,
                trained_record_count=record_count,
                trained_dataset_hash=result.get("dataset_hash"),
            )

            if str(result.get("status", "")).lower() == "error":
                logger.error(
                    "Training failed for %s: %s",
                    game_key,
                    result.get("message", "unknown error"),
                )
            else:
                logger.info(
                    "Training completed for %s accuracy=%s leaderboard_top=%s",
                    game_key,
                    result_accuracy,
                    leaderboard[0]["accuracy"] if leaderboard else None,
                )

            return _build_response(
                result,
                leaderboard=leaderboard,
                optimal_config=optimal_config,
                optimal_config_applied=optimal_config_applied,
                accuracy_history=accuracy_history,
                memory_profile=memory_profile,
            )
        except Exception as exc:
            logger.exception("Unhandled training error for %s", game_key)
            return _build_response(
                {"status": "error", "message": str(exc)},
                leaderboard=leaderboard,
                optimal_config=optimal_config,
                optimal_config_applied=optimal_config_applied,
                accuracy_history=accuracy_history,
                memory_profile=memory_profile,
            )

    def train_all_games(self, games: list[str] | None = None):
        from config import GAME_CONFIGS

        targets = games or list(GAME_CONFIGS.keys())
        results = []
        for game in targets:
            try:
                results.append(self.train(game))
            except Exception as exc:
                results.append({"game": game, "status": "error", "message": str(exc)})
        return results


trainer_service = TrainerService()
