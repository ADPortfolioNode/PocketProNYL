import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
from config import GAME_CONFIGS, GAME_PREDICTION_FORMATS, GAME_PREDICTION_SCHEDULES
from utils.model_utils import (
    _load_model_metadata,
    build_prediction_model_metadata,
    load_model_artifact,
    resolve_highest_accuracy,
)
from utils.game_data_parser import (
    _get_rules,
    _parse_numbers,
    _clamp_primary,
)


def _use_modular_engine() -> bool:
    return os.environ.get("PREDICTION_ENGINE", "modular").lower() != "legacy"


class PredictorService:
    def __init__(self):
        self.models_dir = "/data/models"
        self.prediction_timezone = os.getenv("PREDICTION_TIMEZONE", "America/New_York")
        self.max_session_draws = int(os.getenv("PREDICTION_MAX_SESSION_DRAWS", "400"))

    def _current_prediction_datetime(self):
        try:
            return datetime.now(ZoneInfo(self.prediction_timezone))
        except Exception:
            return datetime.utcnow()

    def _get_session_draw_count(self, game: str, when: datetime):
        schedule = GAME_PREDICTION_SCHEDULES.get(game, {}) or {}
        daily_draws = int(schedule.get("daily_draws", 1) or 0)
        weekday_draws = schedule.get("weekday_draws", {}) or {}
        weekday = int(when.weekday())

        if weekday_draws:
            draw_count = int(weekday_draws.get(weekday, daily_draws) or 0)
        else:
            draw_count = daily_draws

        suggestion_cap = schedule.get("suggestion_session_draws")
        if suggestion_cap is not None:
            draw_count = min(draw_count, int(suggestion_cap))

        draw_count = max(0, draw_count)
        if self.max_session_draws > 0:
            draw_count = min(draw_count, self.max_session_draws)

        return draw_count

    def _resolve_next_scheduled_datetime(self, game: str, when: datetime | None = None) -> datetime:
        """Pick the next calendar day (today first) that has scheduled draws for the game."""
        base = when or self._current_prediction_datetime()
        tz = base.tzinfo
        max_lookahead_days = int(os.getenv("PREDICTION_SCHEDULE_LOOKAHEAD_DAYS", "14"))

        for offset in range(0, max_lookahead_days + 1):
            candidate = base + timedelta(days=offset)
            if self._get_session_draw_count(game, candidate) <= 0:
                continue
            if tz is not None:
                return datetime(candidate.year, candidate.month, candidate.day, tzinfo=tz)
            return datetime(candidate.year, candidate.month, candidate.day)

        return base

    def _normalize_primary_predictions(self, values, rules):
        primary_count = int(rules["primary_count"])
        primary_min = int(rules["primary_min"])
        primary_max = int(rules["primary_max"])
        unique_required = bool(rules.get("primary_unique", True))

        normalized = []
        for value in values[:primary_count]:
            n = int(np.round(value))
            n = max(primary_min, min(primary_max, n))
            if unique_required and n in normalized:
                candidate = n
                while candidate in normalized and candidate <= primary_max:
                    candidate += 1
                while candidate in normalized and candidate >= primary_min:
                    candidate -= 1
                n = max(primary_min, min(primary_max, candidate))
            normalized.append(n)

        if unique_required:
            seen = set()
            deduped = []
            for n in normalized:
                if n not in seen:
                    seen.add(n)
                    deduped.append(n)
            normalized = deduped

            candidate = primary_min
            while len(normalized) < primary_count and candidate <= primary_max:
                if candidate not in seen:
                    seen.add(candidate)
                    normalized.append(candidate)
                candidate += 1

        while len(normalized) < primary_count:
            normalized.append(primary_min)

        return normalized[:primary_count]

    def _normalize_bonus_predictions(self, values, rules, include_bonus):
        if not include_bonus:
            return []

        bonus_count = int(rules.get("bonus_count", 0) or 0)
        bonus_min = int(rules.get("bonus_min", 1))
        bonus_max = int(rules.get("bonus_max", 99))

        normalized = []
        for value in values[:bonus_count]:
            n = int(np.round(value))
            n = max(bonus_min, min(bonus_max, n))
            normalized.append(n)

        while len(normalized) < bonus_count:
            normalized.append(bonus_min)

        return normalized[:bonus_count]

    def _format_prediction(self, game: str, raw_numbers):
        format_spec = GAME_PREDICTION_FORMATS.get(game, {})
        main_count = int(format_spec.get("main_count", len(raw_numbers) or 0))
        bonus_count = int(format_spec.get("bonus_count", 0))
        total_count = main_count + bonus_count

        normalized = list(raw_numbers or [])
        if len(normalized) < total_count:
            normalized.extend([0] * (total_count - len(normalized)))
        if total_count > 0:
            normalized = normalized[:total_count]

        main_min = int(format_spec.get("main_min", 0))
        main_max = int(format_spec.get("main_max", 99))
        bonus_min = int(format_spec.get("bonus_min", main_min))
        bonus_max = int(format_spec.get("bonus_max", main_max))

        main_values = [max(main_min, min(main_max, int(np.round(value)))) for value in normalized[:main_count]]
        if format_spec.get("unique_main", False):
            main_values = list(np.unique(main_values)) # Simpler way to ensure uniqueness
        if format_spec.get("sort_main", False):
            main_values = sorted(main_values)

        bonus_values = [
            max(bonus_min, min(bonus_max, int(np.round(value))))
            for value in normalized[main_count:main_count + bonus_count]
        ]

        formatted = {
            "main_numbers": main_values,
            "bonus_numbers": bonus_values,
            "main_label": format_spec.get("main_label", "Numbers"),
            "bonus_label": format_spec.get("bonus_label", "Bonus"),
            "has_bonus": bonus_count > 0,
        }

        return formatted, main_values + bonus_values

    def _predict_from_artifact(self, artifact: dict, X):
        model_strategy = str((artifact or {}).get("model_strategy", "single"))
        primary_model = (artifact or {}).get("model")
        if primary_model is None:
            raise ValueError("Model artifact for suggestion is invalid.")

        if model_strategy == "ensemble_top3":
            top_models = (artifact or {}).get("top_models") or []
            ensemble_weights = (artifact or {}).get("ensemble_weights") or []
            usable_models = [model for model in top_models if model is not None]
            if len(usable_models) >= 2:
                weight_arr = np.asarray(ensemble_weights[: len(usable_models)], dtype=float)
                if weight_arr.sum() <= 0:
                    weight_arr = np.ones(len(usable_models), dtype=float)
                weight_arr = weight_arr / weight_arr.sum()
                stacked = np.stack(
                    [np.asarray(model.predict(X), dtype=float) for model in usable_models],
                    axis=0,
                )
                return np.tensordot(weight_arr, stacked, axes=(0, 0))

        if model_strategy == "ensemble":
            secondary_model = (artifact or {}).get("secondary_model")
            if secondary_model is None:
                raise ValueError("Ensemble model artifact is missing secondary model.")
            blend_weight = float((artifact or {}).get("blend_weight", 0.7))
            blend_weight = max(0.0, min(1.0, blend_weight))
            primary_pred = np.asarray(primary_model.predict(X), dtype=float)
            secondary_pred = np.asarray(secondary_model.predict(X), dtype=float)
            return (blend_weight * primary_pred) + ((1.0 - blend_weight) * secondary_pred)

        return np.asarray(primary_model.predict(X), dtype=float)

    def _predict_first_draw_payload(self, game: str):
        format_spec = GAME_PREDICTION_FORMATS.get(game, {}) or {}
        return {
            "predicted_numbers": [],
            "formatted_prediction": {
                "main_numbers": [],
                "bonus_numbers": [],
                "main_label": format_spec.get("main_label", "Numbers"),
                "bonus_label": format_spec.get("bonus_label", "Bonus"),
                "has_bonus": int(format_spec.get("bonus_count", 0) or 0) > 0,
            },
            "predicted_main_numbers": [],
            "predicted_bonus_numbers": [],
        }

    def get_prediction_summary(self, game: str):
        """Summarize saved model readiness and highest known accuracy for suggestions."""
        if _use_modular_engine():
            from services.prediction_adapter import prediction_adapter
            return prediction_adapter.get_prediction_summary(game)

        game_key = str(game or "").strip().lower()
        artifact, error = load_model_artifact(game_key, self.models_dir)
        metadata = _load_model_metadata(game_key)
        highest_accuracy = resolve_highest_accuracy(
            game_key,
            artifact=artifact,
            metadata=metadata,
        )
        return {
            "game": game_key,
            "has_model": artifact is not None,
            "accuracy": highest_accuracy,
            "highest_accuracy": highest_accuracy,
            "model_strategy": str((artifact or {}).get("model_strategy", "single")),
            "message": error,
        }

    def predict_next_draw(self, game: str, recent_k: int = 10):
        if _use_modular_engine():
            from services.prediction_adapter import prediction_adapter
            return prediction_adapter.predict_next_draw(game, recent_k)

        session_result = self.predict_prediction_session(game=game, recent_k=recent_k)
        if session_result.get("status") != "success":
            return session_result

        first_draw = session_result.get("prediction_session", [])
        first_payload = first_draw[0] if first_draw else self._predict_first_draw_payload(game)
        message = session_result.get("message")
        if not first_draw and not message:
            message = f"No scheduled draws found for {game} within the lookahead window."

        return {
            "status": "success",
            "game": game,
            **first_payload,
            "prediction_session": session_result.get("prediction_session", []),
            "session_draw_count": session_result.get("session_draw_count", 0),
            "prediction_date": session_result.get("prediction_date"),
            "prediction_weekday": session_result.get("prediction_weekday"),
            "prediction_timezone": session_result.get("prediction_timezone"),
            "predicted_for_date": session_result.get("prediction_date"),
            "model_strategy": session_result.get("model_strategy", "single"),
            "blend_weight": session_result.get("blend_weight"),
            "model_metadata": session_result.get("model_metadata"),
            "highest_accuracy": session_result.get("highest_accuracy"),
            "message": message,
        }

    def predict_prediction_session(self, game: str, recent_k: int = 10):
        # Lazy import to avoid ChromaDB connection during module import
        from .chroma_client import chroma_client
        from services.trainer import TrainerService

        game_key = str(game or "").strip().lower()
        artifact, load_error = load_model_artifact(game_key, self.models_dir)
        if load_error:
            return {"status": "error", "message": load_error}

        metadata = _load_model_metadata(game_key)
        model_metadata = build_prediction_model_metadata(game_key, artifact, metadata)
        highest_accuracy = model_metadata.get("highest_accuracy")

        model_strategy = str(artifact.get("model_strategy", "single"))
        blend_weight = artifact.get("blend_weight")
        feature_len = int(artifact.get("feature_len", 10))
        output_len = int(artifact.get("output_len", 6))
        window_size = int(artifact.get("window_size", 1))
        rules = artifact.get("rules") or _get_rules(game_key)

        # Fetch recent draws from ChromaDB for prediction input
        collection = chroma_client.client.get_collection(game_key)
        fetch_k = max(int(recent_k), window_size + 2)
        data = collection.get(limit=fetch_k, include=["metadatas"])

        if not data or not data["metadatas"]:
            return {"status": "error", "message": "Not enough data to make a suggestion."}

        metadatas = TrainerService()._sort_metadatas_chronologically(data["metadatas"]) # TrainerService still has this sort
        sequences = [self._extract_sequence(meta, game_key) for meta in metadatas]
        sequences = [seq for seq in sequences if seq]

        if len(sequences) < window_size:
            return {"status": "error", "message": "Not enough historical draws for model window."}
        
        prediction_dt = self._resolve_next_scheduled_datetime(game_key)
        session_draw_count = self._get_session_draw_count(game_key, prediction_dt)

        if session_draw_count <= 0:
            return {
                "status": "error",
                "game": game_key,
                "message": f"No scheduled draws found for {game_key} within the lookahead window.",
            }

        primary_count = int(rules.get("primary_count", 5))
        bonus_count = int(rules.get("bonus_count", 0) or 0)
        include_bonus = bonus_count > 0 and output_len >= (primary_count + bonus_count)

        session_predictions = []
        rolling_sequences = sequences[-window_size:]

        for draw_index in range(session_draw_count):
            feature_vector = TrainerService.build_feature_vector(
                rolling_sequences,
                window_size,
                feature_len,
            )
            X = np.array([feature_vector])
            prediction = self._predict_from_artifact(artifact, X)
            prediction = np.array(prediction).reshape(-1)[:output_len]

            primary_raw = prediction[:primary_count]
            bonus_raw = prediction[primary_count:primary_count + bonus_count] if include_bonus else []

            primary_numbers = self._normalize_primary_predictions(primary_raw, rules)
            bonus_numbers = self._normalize_bonus_predictions(bonus_raw, rules, include_bonus)
            predicted_numbers = primary_numbers + bonus_numbers
            formatted_prediction, normalized_flat = self._format_prediction(game_key, predicted_numbers)

            session_predictions.append({
                "draw_index": draw_index + 1,
                "prediction_date": prediction_dt.date().isoformat(),
                "prediction_weekday": prediction_dt.strftime("%A"),
                "prediction_timezone": self.prediction_timezone,
                "predicted_numbers": normalized_flat,
                "formatted_prediction": formatted_prediction,
                "predicted_main_numbers": primary_numbers,
                "predicted_bonus_numbers": bonus_numbers,
            })

            rolling_sequences = (rolling_sequences + [list(normalized_flat)])[-window_size:]

        return {
            "status": "success",
            "game": game_key,
            "prediction_session": session_predictions,
            "session_draw_count": len(session_predictions),
            "prediction_date": prediction_dt.date().isoformat(),
            "prediction_weekday": prediction_dt.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "model_strategy": model_strategy,
            "blend_weight": blend_weight,
            "model_metadata": model_metadata,
            "highest_accuracy": highest_accuracy,
        }

    def predict_all_games(self, games, recent_k: int = 10):
        normalized = []
        for game in games:
            game_key = str(game or "").strip().lower()
            try:
                result = self.predict_next_draw(game_key, recent_k)
                if result.get("status") == "error":
                    normalized.append({
                        "game": game_key,
                        "status": "error",
                        "message": result.get("message", "Suggestion failed."),
                        "prediction": None,
                    })
                    continue

                normalized.append({
                    "game": game_key,
                    "status": "success",
                    "message": "",
                    "prediction": {
                        **result,
                        "status": "COMPLETED",
                        "game": game_key,
                    },
                })
            except Exception as exc:
                normalized.append({
                    "game": game_key,
                    "status": "error",
                    "message": str(exc),
                    "prediction": None,
                })
        return normalized

    def predict(self, game: str, recent_k: int = 10):
        """Backward-compatible alias used by API routes and tooling."""
        return self.predict_next_draw(game, recent_k)

# Export a module-level instance expected by main_rag and other modules
predictor_service = PredictorService()
