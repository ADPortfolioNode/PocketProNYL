"""Adapter between Chroma data, modular prediction engine, and API response format."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import GAME_CONFIGS, GAME_PREDICTION_FORMATS, GAME_PREDICTION_SCHEDULES
from prediction.config.loader import load_game_config
from prediction.core.draw_loader import from_chroma
from prediction.engine import LotteryPredictionEngine


class PredictionAdapter:
    """Bridge modular engine to legacy PredictorService response contract."""

    def __init__(self, state_dir: str | None = None):
        self.state_dir = Path(state_dir or os.environ.get("PREDICTION_STATE_DIR", "/data/prediction"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.prediction_timezone = os.getenv("PREDICTION_TIMEZONE", "America/New_York")
        self.engine = LotteryPredictionEngine(str(self.state_dir))

    def _current_prediction_datetime(self):
        try:
            return datetime.now(ZoneInfo(self.prediction_timezone))
        except Exception:
            return datetime.utcnow()

    def _get_session_draw_count(self, game: str, when: datetime) -> int:
        schedule = GAME_PREDICTION_SCHEDULES.get(game, {}) or {}
        daily_draws = int(schedule.get("daily_draws", 1) or 1)
        weekday_draws = schedule.get("weekday_draws", {}) or {}
        weekday = int(when.weekday())
        if weekday_draws:
            return int(weekday_draws.get(weekday, daily_draws) or daily_draws)
        return daily_draws

    def _format_prediction(self, game: str, primary: list[int], bonus: list[int] | None = None):
        """Reuse GAME_PREDICTION_FORMATS for API-compatible output."""
        from prediction.config.loader import game_rules_from_config

        format_spec = GAME_PREDICTION_FORMATS.get(game, {}) or {}
        rules = game_rules_from_config(game)
        main_count = int(rules.primary_count or format_spec.get("main_count", len(primary)))
        bonus_count = int(format_spec.get("bonus_count", 0))
        main_min = int(format_spec.get("main_min", 0))
        main_max = int(format_spec.get("main_max", 99))
        bonus_min = int(format_spec.get("bonus_min", main_min))
        bonus_max = int(format_spec.get("bonus_max", main_max))

        main_values = [
            max(main_min, min(main_max, int(n))) for n in primary[:main_count]
        ]
        while len(main_values) < main_count:
            main_values.append(main_min)

        if format_spec.get("unique_main", False):
            seen = set()
            deduped = []
            for n in main_values:
                if n not in seen:
                    seen.add(n)
                    deduped.append(n)
            main_values = deduped
            candidate = main_min
            while len(main_values) < main_count and candidate <= main_max:
                if candidate not in seen:
                    main_values.append(candidate)
                    seen.add(candidate)
                candidate += 1

        if format_spec.get("sort_main", False):
            main_values = sorted(main_values[:main_count])

        bonus_values = []
        if bonus_count > 0 and bonus:
            bonus_values = [
                max(bonus_min, min(bonus_max, int(n))) for n in bonus[:bonus_count]
            ]
        while len(bonus_values) < bonus_count:
            bonus_values.append(bonus_min)

        formatted = {
            "main_numbers": main_values,
            "bonus_numbers": bonus_values,
            "main_label": format_spec.get("main_label", "Numbers"),
            "bonus_label": format_spec.get("bonus_label", "Bonus"),
            "has_bonus": bonus_count > 0,
        }
        return formatted, main_values + bonus_values

    def _ready_marker(self, game: str) -> Path:
        return self.state_dir / f"{game}_ready.json"

    def mark_ready(self, game: str, payload: dict) -> None:
        path = self._ready_marker(game)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        tmp.replace(path)

    def is_ready(self, game: str) -> bool:
        return self._ready_marker(game).exists()

    def _dataset_snapshot(self, game: str) -> dict:
        from services.chroma_client import chroma_client

        try:
            count = chroma_client.count_documents(game)
            return {
                "dataset_hash": hashlib.md5(f"{game}|{count}".encode("utf-8")).hexdigest(),
                "record_count": int(count or 0),
            }
        except Exception:
            return {"dataset_hash": None, "record_count": 0}

    def predict_next_draw(self, game: str, recent_k: int = 10) -> dict:
        if game not in GAME_CONFIGS:
            return {"status": "error", "message": f"Game '{game}' has no modular prediction config."}

        try:
            history = from_chroma(game, limit=max(recent_k, 100))
            ticket = self.engine.predict(game, history=history)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        prediction_dt = self._current_prediction_datetime()
        session_draw_count = self._get_session_draw_count(game, prediction_dt)
        formatted, flat = self._format_prediction(game, ticket.primary, ticket.bonus)

        session_item = {
            "draw_index": 1,
            "prediction_date": prediction_dt.date().isoformat(),
            "prediction_weekday": prediction_dt.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "predicted_numbers": flat,
            "formatted_prediction": formatted,
            "predicted_main_numbers": formatted["main_numbers"],
            "predicted_bonus_numbers": formatted["bonus_numbers"],
        }

        return {
            "status": "success",
            "game": game,
            "predicted_numbers": flat,
            "formatted_prediction": formatted,
            "predicted_main_numbers": formatted["main_numbers"],
            "predicted_bonus_numbers": formatted["bonus_numbers"],
            "prediction_session": [session_item] * max(1, session_draw_count),
            "session_draw_count": max(1, session_draw_count),
            "prediction_date": prediction_dt.date().isoformat(),
            "prediction_weekday": prediction_dt.strftime("%A"),
            "prediction_timezone": self.prediction_timezone,
            "model_strategy": "ensemble",
            "blend_weight": None,
            "strategy_weights": ticket.weights_used,
            "strategy_contributions": ticket.strategy_contributions,
            "metrics": ticket.metrics,
            "engine": "modular",
        }

    def _load_ready_payload(self, game: str) -> dict:
        path = self._ready_marker(game)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def train_or_backtest(
        self,
        game: str,
        *,
        refresh_only: bool = False,
        baseline_accuracy: float | None = None,
    ) -> dict:
        """Run walk-forward backtest and persist readiness marker."""
        if game not in GAME_CONFIGS:
            return {"status": "error", "message": f"Game '{game}' has no modular prediction config."}

        start = time.time()
        dataset = self._dataset_snapshot(game)
        existing = self._load_ready_payload(game) if refresh_only else {}
        metrics = existing.get("metrics") if isinstance(existing.get("metrics"), dict) else None

        if refresh_only:
            if not metrics:
                metrics = {
                    "status": "ok",
                    "note": "Readiness refresh without walk-forward backtest.",
                    "legacy_baseline_accuracy": baseline_accuracy,
                }
            mean_hits = metrics.get("mean_partial_hits", 0.0)
            lift = metrics.get("lift_vs_random", 0.0)
            payload = {
                **existing,
                "game": game,
                "trained_at": time.time(),
                "metrics": metrics,
                "suggestion_history": metrics.get("suggestion_history", []),
                "engine": "modular",
                "record_count": dataset.get("record_count"),
                "dataset_hash": dataset.get("dataset_hash"),
                "refreshed": True,
            }
            self.mark_ready(game, payload)
            return {
                "status": "success",
                "game": game,
                "message": "Modular engine readiness refreshed.",
                "training_time": round(time.time() - start, 2),
                "metrics": metrics,
                "engine": "modular",
                "mean_partial_hits": mean_hits,
                "lift_vs_random": lift,
                "exact_match_rate": metrics.get("exact_match_rate"),
                "model_strategy": "ensemble",
                "attempts": 1,
                "record_count": dataset.get("record_count"),
                "dataset_hash": dataset.get("dataset_hash"),
                "refreshed": True,
            }

        try:
            metrics = self.engine.backtest(game)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        mean_hits = metrics.get("mean_partial_hits", 0.0)
        lift = metrics.get("lift_vs_random", 0.0)

        payload = {
            "game": game,
            "trained_at": time.time(),
            "metrics": metrics,
            "suggestion_history": metrics.get("suggestion_history", []),
            "engine": "modular",
            "record_count": dataset.get("record_count"),
            "dataset_hash": dataset.get("dataset_hash"),
        }
        self.mark_ready(game, payload)

        return {
            "status": "success",
            "game": game,
            "message": "Modular engine backtest complete.",
            "training_time": round(time.time() - start, 2),
            "metrics": metrics,
            "engine": "modular",
            "mean_partial_hits": mean_hits,
            "lift_vs_random": lift,
            "exact_match_rate": metrics.get("exact_match_rate"),
            "model_strategy": "ensemble",
            "attempts": 1,
            "record_count": dataset.get("record_count"),
            "dataset_hash": dataset.get("dataset_hash"),
        }

    def get_prediction_summary(self, game: str) -> dict:
        ready_path = self._ready_marker(game)
        if not ready_path.exists():
            return {"has_model": False, "game": game, "engine": "modular"}

        with open(ready_path, encoding="utf-8") as handle:
            data = json.load(handle)
        metrics = data.get("metrics", {})
        return {
            "has_model": True,
            "game": game,
            "engine": "modular",
            "last_prediction": data.get("trained_at"),
            "accuracy": metrics.get("mean_partial_hits"),
            "accuracy_percent": metrics.get("accuracy_percent"),
            "target_accuracy": metrics.get("target_accuracy"),
            "target_reached": metrics.get("target_reached", False),
            "lift_vs_random": metrics.get("lift_vs_random"),
            "metrics": metrics,
            "suggestion_history": metrics.get("suggestion_history", []),
        }

    def on_new_draw(self, game: str, metadata: dict, draw_id: str | None = None) -> dict | None:
        from prediction.adapter_hooks import on_new_draw_metadata

        return on_new_draw_metadata(game, metadata, draw_id=draw_id)


prediction_adapter = PredictionAdapter()