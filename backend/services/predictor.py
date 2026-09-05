import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional
from routes.chroma_repository import chroma_repository # Import chroma_repository
from typing import Any
import numpy as np

from config import GAME_CONFIGS, GAME_PREDICTION_FORMATS, GAME_PREDICTION_SCHEDULES
from prediction.core.types import Draw, date, datetime  # Ensure datetime is imported for Draw constructor
from services.statistical_strategies import score_frequency, score_hybrid, score_overdue, select_top_numbers, score_hot_cold
from services.trainer import TrainerService
from utils.model_utils import (
    _load_model_metadata,
    build_prediction_model_metadata,
    load_model_artifact,
    _load_model_metadata,
)
from utils.game_data_parser import (
    _get_rules as get_game_rules,
    _clamp_primary as clamp_primary_util,
    _extract_record_sequence as extract_record_sequence_util,
)

class PredictorService:
    def __init__(self):
        self.models_dir = "/data/models"
        self.prediction_timezone = os.getenv("PREDICTION_TIMEZONE", "America/New_York")
        self.max_session_draws = int(os.getenv("PREDICTION_MAX_SESSION_DRAWS", "400"))
        # New configurable parameters for statistical strategies
        self.default_hot_window = int(os.getenv("PREDICTION_HOT_WINDOW", "50"))
        self.default_cold_window = int(os.getenv("PREDICTION_COLD_WINDOW", "200"))
        self.default_hot_weight = float(os.getenv("PREDICTION_HOT_WEIGHT", "0.7"))
        self.default_cold_weight = float(os.getenv("PREDICTION_COLD_WEIGHT", "0.3"))

    def _get_draw_date_from_meta(self, metadata: dict) -> Optional[str]:
        """Flexibly extracts a date string from metadata using common key variants."""
        if not isinstance(metadata, dict):
            return None
        # This list of keys is borrowed from the robust TrainerService implementation
        for key in ("draw_date", "drawdate", "date", "drawn_at", "draw_datetime"):
            for variant in (key, key.upper(), key.title()):
                value = metadata.get(variant)
                if value:
                    return str(value)
        return None

    def _get_rules(self, game_key: str) -> Dict[str, Any]:
        """Return merged game rules from GAME_CONFIGS."""
        return get_game_rules(game_key)

    def _normalize_prediction_metadata(self, meta: dict, game_key: str) -> list[dict]:
        """
        Normalize Chroma metadata into one-or-more draw records.

        Take 5 NY Open Data stores midday/evening boards in separate fields
        instead of a single winning_numbers column.
        """
        if not isinstance(meta, dict):
            return []

        base = dict(meta)
        # Normalize space-separated boards to comma-separated.
        for key, value in list(base.items()):
            if isinstance(value, str) and "winning" in key.lower() and "number" in key.lower():
                if " " in value and "," not in value:
                    base[key] = value.replace(" ", ",")

        game = str(game_key or "").lower()
        if game == "take5":
            records: list[dict] = []
            for session_key, session_name in (
                ("midday_winning_numbers", "midday"),
                ("evening_winning_numbers", "evening"),
            ):
                raw = base.get(session_key)
                if not raw:
                    continue
                row = dict(base)
                row["winning_numbers"] = raw
                row["draw_session"] = session_name
                records.append(row)
            if records:
                return records
            # Fallback: some rows only have a generic winning_numbers field.
            if base.get("winning_numbers"):
                return [base]
            return []

        if "winning_numbers" in base and isinstance(base["winning_numbers"], str):
            if " " in base["winning_numbers"] and "," not in base["winning_numbers"]:
                base["winning_numbers"] = base["winning_numbers"].replace(" ", ",")
        return [base]

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

    def _resolve_session_draw_datetimes(self, game: str, target_draw_date: date, count: int) -> list[datetime]:
        """Resolve each scheduled draw to a timezone-aware datetime when configured."""
        schedule = GAME_PREDICTION_SCHEDULES.get(game, {}) or {}
        raw_times = schedule.get("draw_times") or []
        sessions = schedule.get("draw_sessions") or []
        result: list[datetime] = []
        timezone = ZoneInfo(self.prediction_timezone)
        for index in range(max(1, count)):
            draw_time = None
            if index < len(raw_times):
                try:
                    draw_time = datetime.strptime(str(raw_times[index]), "%H:%M").time()
                except ValueError:
                    draw_time = None
            draw_datetime = datetime.combine(target_draw_date, draw_time or datetime.min.time(), tzinfo=timezone)
            result.append(draw_datetime)
        return result

    def _session_metadata(self, game: str, target_draw_date: date, count: int) -> list[dict]:
        schedule = GAME_PREDICTION_SCHEDULES.get(game, {}) or {}
        sessions = schedule.get("draw_sessions") or []
        datetimes = self._resolve_session_draw_datetimes(game, target_draw_date, count)
        metadata: list[dict] = []
        previous = None
        for index, draw_datetime in enumerate(datetimes):
            spacing = None
            if previous is not None:
                spacing = int((draw_datetime - previous).total_seconds() // 60)
            metadata.append({
                "draw_datetime": draw_datetime.isoformat(),
                "draw_time": draw_datetime.strftime("%H:%M"),
                "draw_session": sessions[index] if index < len(sessions) else None,
                "spacing_from_previous_minutes": spacing,
            })
            previous = draw_datetime
        return metadata

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

        return sorted(list(set(normalized[:primary_count]))) # Ensure uniqueness and sort

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
            # np.unique returns numpy scalars; coerce back to plain Python ints for JSON.
            seen = set()
            unique_mains: list[int] = []
            for value in main_values:
                if value not in seen:
                    seen.add(value)
                    unique_mains.append(int(value))
            main_values = unique_mains
        if format_spec.get("sort_main", False):
            main_values = sorted(int(v) for v in main_values)
        else:
            main_values = [int(v) for v in main_values]

        bonus_values = [
            int(max(bonus_min, min(bonus_max, int(np.round(value)))))
            for value in normalized[main_count:main_count + bonus_count]
        ]

        formatted = {
            "main_numbers": main_values,
            "bonus_numbers": bonus_values,
            "main_label": format_spec.get("main_label", "Numbers"),
            "bonus_label": format_spec.get("bonus_label", "Bonus"),
            "has_bonus": bonus_count > 0,
        }

        return formatted, [int(v) for v in (main_values + bonus_values)]

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
        # Lazy import to avoid ChromaDB connection during module import
        game_key = str(game or "").strip().lower()
        from utils.model_utils import load_model_artifact, _load_model_metadata, build_prediction_model_metadata

        artifact, load_error = load_model_artifact(game_key, self.models_dir)
        if load_error:
            return {"has_model": False, "game": game_key, "message": load_error}

        metadata = _load_model_metadata(game_key)
        model_metadata = build_prediction_model_metadata(game_key, artifact, metadata)
        highest_accuracy = model_metadata.get("highest_accuracy")

        return {
            "has_model": True,
            "game": game_key,
            "accuracy": model_metadata.get("accuracy"),
            "highest_accuracy": highest_accuracy,
            "model_strategy": model_metadata.get("model_strategy"),
        }

    def predict_next_draw(
        self,
        game: str,
        recent_k: int = 10,
        strategy: Optional[str] = "rf",
        target_draw_date_str: Optional[str] = None,
        hot_window: Optional[int] = None,
        cold_window: Optional[int] = None,
        hot_weight: Optional[float] = None,
        cold_weight: Optional[float] = None,
        target_draw_date: Optional[str] = None, # Accept as alias for backward compatibility
    ):
        """
        Predicts the next draw numbers for a given game using a specified strategy.

        Args:
            game: The name of the game.
            recent_k: The number of recent draws to consider for RF model input.
            strategy: The prediction strategy to use ("rf", "frequency", "overdue", "hybrid", "ensemble").
                      Defaults to "rf".
            target_draw_date_str: Optional ISO format date string (YYYY-MM-DD) for the target draw.
                                  If omitted, defaults to the next scheduled draw date.

        Returns:
            A dictionary containing prediction results. # PocketProNYL Project
        """
        game_key = str(game or "").strip().lower()
        rules = get_game_rules(game_key)
        game_prediction_params = rules.get("prediction_params", {})

        # Resolve target_draw_date_str from target_draw_date if provided (backward compatibility)
        if target_draw_date_str is None and target_draw_date is not None:
            target_draw_date_str = target_draw_date

        # Layered configuration resolution: API request > game config > environment default
        resolved_hot_window = hot_window if hot_window is not None else game_prediction_params.get("hot_window", self.default_hot_window)
        resolved_cold_window = cold_window if cold_window is not None else game_prediction_params.get("cold_window", self.default_cold_window)
        resolved_hot_weight = hot_weight if hot_weight is not None else game_prediction_params.get("hot_weight", self.default_hot_weight)
        resolved_cold_weight = cold_weight if cold_weight is not None else game_prediction_params.get("cold_weight", self.default_cold_weight)

        generated_at = self._current_prediction_datetime()

        # Resolve target_draw_date
        if target_draw_date_str:
            try:
                target_draw_date = datetime.fromisoformat(target_draw_date_str).date()
            except ValueError:
                return {"status": "error", "message": f"Invalid target_draw_date format: {target_draw_date_str}. Use ISO format (YYYY-MM-DD)."}
        else:
            next_scheduled_dt = self._resolve_next_scheduled_datetime(game_key, when=generated_at)
            target_draw_date = next_scheduled_dt.date()

        # Fetch all history and filter

        collection = chroma_repository.get_or_create_collection(game_key)
        # Load all metadatas, not just a limited subset, to ensure full history is available for filtering
        all_metadatas = TrainerService()._load_all_metadatas(collection)
        if not all_metadatas:
            return {"status": "error", "message": "No historical data available for this game."}

        sorted_metadatas = TrainerService()._sort_metadatas_chronologically(all_metadatas)
        
        history_for_strategy: List[Draw] = []
        for meta in sorted_metadatas:
            draw_date_str_meta = self._get_draw_date_from_meta(meta)
            if not draw_date_str_meta:
                continue
            try:
                draw_date_obj = datetime.fromisoformat(
                    str(draw_date_str_meta).replace("Z", "+00:00")
                ).date()
            except (ValueError, TypeError):
                try:
                    draw_date_obj = datetime.strptime(str(draw_date_str_meta)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

            # Use <= to include draws from the target date.
            if draw_date_obj > target_draw_date:
                continue

            for normalized_meta in self._normalize_prediction_metadata(meta, game_key):
                try:
                    numbers = extract_record_sequence_util(normalized_meta, game_key)
                    if not numbers:
                        continue
                    primary_count = int(rules.get("primary_count", 0) or 0)
                    if primary_count <= 0 or len(numbers) < primary_count:
                        continue
                    primary = numbers[:primary_count]
                    bonus = numbers[primary_count:]
                    # Take5 suggestion format is 5 mains / no bonus even if config lists bonus_keys.
                    fmt = GAME_PREDICTION_FORMATS.get(game_key) or {}
                    if int(fmt.get("bonus_count") or 0) <= 0:
                        bonus = []
                    history_for_strategy.append(
                        Draw(
                            primary=primary,
                            bonus=bonus,
                            date=draw_date_obj,
                            metadata={
                                **normalized_meta,
                                "draw_date": draw_date_obj.isoformat(),
                            },
                        )
                    )
                except (ValueError, TypeError):
                    continue

        if not history_for_strategy:
            return {"status": "error", "message": f"No historical draws found on or before {target_draw_date.isoformat()}."}

        # Dispatch based on strategy (UI often omits strategy → treat as RF with statistical fallback)
        strategy_lower = str(strategy or "rf").strip().lower() or "rf"
        if strategy_lower in ("rf", "ensemble", "ensemble_hot_cold"):
            rf_result = self._predict_with_rf_model(
                game_key, recent_k, strategy_lower, target_draw_date, generated_at, history_for_strategy, rules,
                hot_window=resolved_hot_window, cold_window=resolved_cold_window, hot_weight=resolved_hot_weight, cold_weight=resolved_cold_weight
            )
            if isinstance(rf_result, dict) and rf_result.get("status") == "success":
                return rf_result
            # No trained model (or RF path failed): fall back so Suggest still returns numbers.
            fallback = self._predict_with_statistical_strategy(
                game_key, "frequency", target_draw_date, generated_at, history_for_strategy, rules, window=recent_k,
                hot_window=resolved_hot_window, cold_window=resolved_cold_window, hot_weight=resolved_hot_weight, cold_weight=resolved_cold_weight
            )
            if isinstance(fallback, dict) and fallback.get("status") == "success":
                rf_message = ""
                if isinstance(rf_result, dict):
                    rf_message = str(rf_result.get("message") or "").strip()
                fallback["strategy_used"] = "frequency"
                fallback["fallback_from"] = strategy_lower
                if rf_message:
                    fallback["fallback_reason"] = rf_message
                for item in fallback.get("prediction_session") or []:
                    if isinstance(item, dict):
                        item["strategy_used"] = "frequency"
                        item["fallback_from"] = strategy_lower
                return fallback
            return rf_result if isinstance(rf_result, dict) else fallback
        elif strategy_lower in ("frequency", "overdue", "hybrid", "hot_cold"):
            return self._predict_with_statistical_strategy(
                game_key, strategy_lower, target_draw_date, generated_at, history_for_strategy, rules, window=recent_k, 
                hot_window=resolved_hot_window, cold_window=resolved_cold_window, hot_weight=resolved_hot_weight, cold_weight=resolved_cold_weight
            )
        else:
            return {"status": "error", "message": f"Unknown prediction strategy: {strategy}"}

    def _suggestion_counts(self, game_key: str, rules: Dict[str, Any]) -> tuple[int, int, int, int]:
        """Return (primary_count, bonus_count, bonus_min, bonus_max) for suggestion output shape."""
        format_spec = GAME_PREDICTION_FORMATS.get(game_key) or {}
        primary_count = int(
            format_spec.get("main_count", rules.get("primary_count", 5)) or 5
        )
        bonus_count = int(
            format_spec.get("bonus_count", rules.get("bonus_count", 0)) or 0
        )
        bonus_min = int(
            format_spec.get("bonus_min", rules.get("bonus_min", 1)) or 1
        )
        bonus_max = int(
            format_spec.get("bonus_max", rules.get("bonus_max", 99)) or 99
        )
        return primary_count, bonus_count, bonus_min, bonus_max

    def _select_bonus_numbers(
        self,
        history: List[Draw],
        bonus_count: int,
        bonus_min: int,
        bonus_max: int,
        window: int,
        exclude: Optional[List[int]] = None,
    ) -> List[int]:
        """Pick bonus balls from recent bonus history (frequency), falling back to range fill."""
        if bonus_count <= 0:
            return []
        exclude_set = set(int(n) for n in (exclude or []))
        recent = history[-max(1, int(window or 1)) :]
        from collections import Counter

        counts: Counter = Counter()
        for draw in recent:
            for num in draw.bonus or []:
                try:
                    value = int(num)
                except (TypeError, ValueError):
                    continue
                if bonus_min <= value <= bonus_max and value not in exclude_set:
                    counts[value] += 1

        ranked = [num for num, _ in counts.most_common()]
        picks: List[int] = []
        for num in ranked:
            if num not in picks:
                picks.append(int(num))
            if len(picks) >= bonus_count:
                break

        if len(picks) < bonus_count:
            for candidate in range(bonus_min, bonus_max + 1):
                if candidate in exclude_set or candidate in picks:
                    continue
                picks.append(candidate)
                if len(picks) >= bonus_count:
                    break
        return picks[:bonus_count]

    def _predict_with_statistical_strategy(
        self,
        game_key: str,
        strategy: str,
        target_draw_date: date,
        generated_at: datetime,
        history: List[Draw],
        rules: Dict[str, Any],
        hot_window: int,
        cold_window: int,
        hot_weight: float,
        cold_weight: float,
        window: int = 100,
        freq_weight: float = 0.6,
    ):
        """Generates lottery number suggestions using pure statistical strategies."""
        primary_count, bonus_count, bonus_min, bonus_max = self._suggestion_counts(
            game_key, rules
        )
        # Score against full primary universe from GAME_CONFIGS, but select suggestion-sized ticket.
        scoring_rules = dict(rules)
        scoring_rules["primary_count"] = primary_count

        scores: Dict[int, float] = {}
        if strategy == "frequency":
            scores = score_frequency(history, scoring_rules, window=window)
        elif strategy == "overdue":
            scores = score_overdue(history, scoring_rules)
        elif strategy == "hybrid":
            scores = score_hybrid(history, scoring_rules, window=window, freq_weight=freq_weight)
        elif strategy == "hot_cold":
            scores = score_hot_cold(
                history,
                scoring_rules,
                hot_window=hot_window,
                cold_window=cold_window,
                hot_weight=hot_weight,
                cold_weight=cold_weight,
            )

        if not scores:
            return {"status": "error", "message": f"Could not generate scores for strategy '{strategy}'."}

        primary_numbers = [int(n) for n in select_top_numbers(scores, scoring_rules)]
        # Bonus balls are scored separately from historical bonus fields (not sliced from mains).
        bonus_numbers = self._select_bonus_numbers(
            history,
            bonus_count=bonus_count,
            bonus_min=bonus_min,
            bonus_max=bonus_max,
            window=window,
            exclude=primary_numbers if game_key in ("nylotto", "take5") else None,
        )
        suggested_numbers = primary_numbers + bonus_numbers

        formatted_prediction, normalized_flat = self._format_prediction(
            game_key, suggested_numbers
        )
        # Prefer formatter output (respects GAME_PREDICTION_FORMATS) when present.
        if isinstance(formatted_prediction, dict):
            mains_fmt = formatted_prediction.get("main_numbers")
            bonus_fmt = formatted_prediction.get("bonus_numbers")
            if isinstance(mains_fmt, list) and mains_fmt:
                primary_numbers = [int(n) for n in mains_fmt]
            if isinstance(bonus_fmt, list):
                bonus_numbers = [int(n) for n in bonus_fmt]
            suggested_numbers = primary_numbers + bonus_numbers
            normalized_flat = [int(n) for n in (primary_numbers + bonus_numbers)]

        session_metadata = self._session_metadata(game_key, target_draw_date, 1)
        session_predictions = [{
            "draw_index": 1,
            "prediction_date": target_draw_date.isoformat(),
            "prediction_weekday": target_draw_date.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "predicted_numbers": normalized_flat,
            "formatted_prediction": formatted_prediction,
            "predicted_main_numbers": primary_numbers,
            "predicted_bonus_numbers": bonus_numbers,
            "strategy_used": strategy,
            "generated_at": generated_at.isoformat(),
            "predicted_for_date": target_draw_date.isoformat(), # Added for consistency
            **session_metadata[0],
        }]

        return {
            "status": "success",
            "game": game_key,
            "prediction_session": session_predictions,
            "session_draw_count": len(session_predictions), # Will be 1 for statistical
            "prediction_date": target_draw_date.isoformat(),
            "prediction_weekday": target_draw_date.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "strategy_used": strategy,
            "generated_at": generated_at.isoformat(),
            "predicted_for_date": target_draw_date.isoformat(), # Added for consistency
            # RF-specific fields are omitted for statistical strategies
        }

    def _predict_with_rf_model(
        self,
        game_key: str,
        recent_k: int,
        strategy: str, # "rf" or "ensemble"
        target_draw_date: date,
        generated_at: datetime,
        history_for_strategy: List[Draw], # Changed from all_sorted_metadatas
        rules: Dict[str, Any],
        hot_window: int,
        cold_window: int,
        hot_weight: float,
        cold_weight: float,
    ):
        """Generates lottery number suggestions using a trained Random Forest model."""
        artifact, load_error = load_model_artifact(game_key, self.models_dir)
        if load_error:
            return {"status": "error", "message": load_error}

        metadata = _load_model_metadata(game_key)
        model_metadata = build_prediction_model_metadata(game_key, artifact, metadata)
        highest_accuracy = model_metadata.get("highest_accuracy")

        model_strategy = str(artifact.get("model_strategy", "single"))
        blend_weight = artifact.get("blend_weight")
        feature_len = int(artifact.get("feature_len", 10))
        output_len = int(rules.get("primary_count", 5) + rules.get("bonus_count", 0)) # Ensure output_len matches game rules
        window_size = int(artifact.get("window_size", 1))
        
        # The RF model must only use data strictly *before* the target date for its feature vector.
        rf_history = [d for d in history_for_strategy if d.date < target_draw_date]
        sequences_for_rf = [d.primary + d.bonus for d in rf_history]
        sequences_for_rf = [seq for seq in sequences_for_rf if seq]
        if len(sequences_for_rf) < window_size:
            return {"status": "error", "message": "Not enough historical draws for RF model window after date filtering."}
        
        session_draw_count = self._get_session_draw_count(game_key, generated_at) # Use generated_at for session count
        if session_draw_count <= 0:
            return {
                "status": "error",
                "game": game_key,
                "message": f"No scheduled draws found for {game_key} on {target_draw_date.isoformat()}.",
            }

        primary_count = int(rules.get("primary_count", 5))
        bonus_count = int(rules.get("bonus_count", 0) or 0)
        include_bonus = bonus_count > 0 and output_len >= (primary_count + bonus_count)

        session_metadata = self._session_metadata(game_key, target_draw_date, session_draw_count)
        session_predictions = []
        rolling_sequences = sequences_for_rf[-window_size:]

        strategy_used_in_response = strategy # Default for RF
        for draw_index in range(session_draw_count):
            feature_vector = TrainerService.build_feature_vector(
                rolling_sequences,
                window_size,
                feature_len,
            )
            X = np.array([feature_vector])
            rf_prediction = self._predict_from_artifact(artifact, X)
            rf_prediction = np.array(rf_prediction).reshape(-1)[:output_len]

            primary_raw = rf_prediction[:primary_count]
            bonus_raw = rf_prediction[primary_count:primary_count + bonus_count] if include_bonus else [] # PocketProNYL Project

            primary_numbers = self._normalize_primary_predictions(primary_raw, rules) # PocketProNYL Project
            bonus_numbers = self._normalize_bonus_predictions(bonus_raw, rules, include_bonus)
            
            # Ensemble logic: blend RF with Hybrid statistical strategy
            if strategy == "ensemble":
                hybrid_scores = score_hybrid(history_for_strategy, rules, window=100, freq_weight=0.6)
                
                ensemble_scores = {num: 0.0 for num in range(rules["primary_min"], rules["primary_max"] + 1)}
                # Give RF predicted numbers a high score
                for num in (primary_numbers + bonus_numbers):
                    ensemble_scores[num] = max(ensemble_scores.get(num, 0.0), 1.0)
                
                blend_weight_ensemble = 0.5 # Weight for RF component in ensemble
                for num in ensemble_scores.keys():
                    ensemble_scores[num] = (ensemble_scores[num] * blend_weight_ensemble) + (hybrid_scores.get(num, 0.0) * (1.0 - blend_weight_ensemble))
                
                # Select top numbers from blended scores
                ensemble_suggested_numbers = select_top_numbers(ensemble_scores, rules)
                primary_numbers = ensemble_suggested_numbers[:primary_count]
                bonus_numbers = ensemble_suggested_numbers[primary_count:] if bonus_count > 0 else []
                predicted_numbers = primary_numbers + bonus_numbers
                strategy_used_in_response = "ensemble (RF + Hybrid)" # PocketProNYL Project
            elif strategy == "ensemble_hot_cold":
                # Use the hot/cold strategy for the statistical part of the ensemble
                hot_cold_scores = score_hot_cold(history_for_strategy, rules, hot_window=hot_window, cold_window=cold_window, hot_weight=hot_weight, cold_weight=cold_weight)
                
                ensemble_scores = {num: 0.0 for num in range(rules["primary_min"], rules["primary_max"] + 1)}
                # Give RF predicted numbers a high score
                for num in (primary_numbers + bonus_numbers):
                    ensemble_scores[num] = max(ensemble_scores.get(num, 0.0), 1.0)
                
                blend_weight_ensemble = 0.5 # Weight for RF component in ensemble
                for num in ensemble_scores.keys():
                    ensemble_scores[num] = (ensemble_scores[num] * blend_weight_ensemble) + (hot_cold_scores.get(num, 0.0) * (1.0 - blend_weight_ensemble))
                
                # Select top numbers from blended scores
                ensemble_suggested_numbers = select_top_numbers(ensemble_scores, rules)
                primary_numbers = ensemble_suggested_numbers[:primary_count]
                bonus_numbers = ensemble_suggested_numbers[primary_count:] if bonus_count > 0 else []
                predicted_numbers = primary_numbers + bonus_numbers
                strategy_used_in_response = "ensemble (RF + Hot/Cold)"
            else:
                predicted_numbers = primary_numbers + bonus_numbers
            formatted_prediction, normalized_flat = self._format_prediction(game_key, predicted_numbers)

            session_predictions.append({
                "draw_index": draw_index + 1,
                "prediction_date": target_draw_date.isoformat(),
                "prediction_weekday": target_draw_date.strftime("%A"),
                "prediction_timezone": self.prediction_timezone,
                "predicted_numbers": normalized_flat,
                "formatted_prediction": formatted_prediction,
                "predicted_main_numbers": primary_numbers,
                "predicted_bonus_numbers": bonus_numbers,
                "strategy_used": strategy_used_in_response, # New field
                "generated_at": generated_at.isoformat(), # New field
                "predicted_for_date": target_draw_date.isoformat(), # Added for consistency
                **session_metadata[draw_index],
            })

            rolling_sequences = (rolling_sequences + [list(normalized_flat)])[-window_size:]

        return {
            "status": "success",
            "game": game_key,
            "prediction_session": session_predictions,
            "session_draw_count": len(session_predictions),
            "prediction_date": target_draw_date.isoformat(),
            "prediction_weekday": target_draw_date.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "model_strategy": model_strategy,
            "blend_weight": blend_weight,
            "model_metadata": model_metadata,
            "highest_accuracy": highest_accuracy,
            "strategy_used": strategy_used_in_response, # New field
            "generated_at": generated_at.isoformat(), # New field
            "predicted_for_date": target_draw_date.isoformat(), # Added for consistency
        }

    def predict_all_games(self, games, recent_k: int = 10):
        normalized = []
        for game in games:
            game_key = str(game or "").strip().lower()
            try:
                # Call predict_next_draw with default strategy and no target date
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

    def predict(self, game: str, recent_k: int = 10, strategy: str = "rf", target_draw_date_str: Optional[str] = None):
        """Backward-compatible alias used by API routes and tooling."""
        return self.predict_next_draw(game, recent_k, strategy, target_draw_date_str)

# Export a module-level instance expected by main_rag and other modules
predictor_service = PredictorService()
