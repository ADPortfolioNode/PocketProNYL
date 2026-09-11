"""Per-game draw-history tuning orchestration."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from config import GAME_CONFIGS
from prediction.engine import LotteryPredictionEngine
from prediction.state.weight_store import WeightStore


class GameTuner:
    """Run the validated iterative learner independently for each game."""

    def __init__(self, state_dir: str | None = None):
        self.state_dir = Path(state_dir or os.environ.get("PREDICTION_STATE_DIR", "/data/prediction"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "status": "idle",
            "game": None,
            "games_total": 0,
            "games_completed": 0,
            "current_game": None,
            "current_task": None,
            "updated_at": time.time(),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set_status(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)
            self._status["updated_at"] = time.time()

    def tune_game(self, game: str) -> dict[str, Any]:
        game_key = str(game or "").strip().lower()
        if game_key not in GAME_CONFIGS:
            return {"status": "error", "game": game_key, "message": "Unknown game."}

        started = time.time()
        self._set_status(current_game=game_key, current_task="loading_draw_history")
        try:
            self._set_status(current_task="iterative_validation")
            metrics = LotteryPredictionEngine(str(self.state_dir)).backtest(game_key)
            weights = WeightStore(str(self.state_dir)).load(game_key)
            result = {
                "status": metrics.get("status", "ok"),
                "game": game_key,
                "training_draws": (metrics.get("split") or {}).get("training_draws"),
                "test_draws": (metrics.get("split") or {}).get("test_draws"),
                "evaluated_draws": metrics.get("evaluated_draws", 0),
                "accuracy": metrics.get("accuracy"),
                "accuracy_percent": metrics.get("accuracy_percent"),
                "highest_accuracy_percent": metrics.get("highest_accuracy_percent"),
                "target_reached": metrics.get("target_reached", False),
                "verification_rounds": metrics.get("verification_rounds", 0),
                "suggestion_history_count": len(metrics.get("suggestion_history") or []),
                "suggestion_history": (metrics.get("suggestion_history") or [])[-25:],
                "training_seconds": round(time.time() - started, 2),
                "message": metrics.get("note"),
            }
            self._set_status(
                current_game=game_key,
                latest_result=result,
                tuning_metrics={
                    "accuracy_percent": result.get("accuracy_percent"),
                    "highest_accuracy_percent": result.get("highest_accuracy_percent"),
                    "target_accuracy_percent": metrics.get("target_accuracy_percent"),
                    "random_baseline": metrics.get("random_baseline"),
                    "lift_vs_random": metrics.get("lift_vs_random"),
                    "suggestion_history_count": result.get("suggestion_history_count"),
                    "suggestion_history": result.get("suggestion_history", []),
                    "weights": weights.weights,
                    "best_weights": weights.best_weights,
                },
            )
            return result
        except Exception as exc:
            return {
                "status": "error",
                "game": game_key,
                "message": str(exc),
                "training_seconds": round(time.time() - started, 2),
            }

    def tune(self, game: str | None = None) -> dict[str, Any]:
        normalized_game = str(game or "").strip().lower()
        games = [] if normalized_game in ("", "all", "*") else [normalized_game]
        if not games:
            games = list(GAME_CONFIGS.keys())
        self._set_status(
            status="running",
            game=game or "all",
            games_total=len(games),
            games_completed=0,
            current_game=None,
            current_task="queued",
        )
        results = []
        for game_key in games:
            result = self.tune_game(game_key)
            results.append(result)
            self._set_status(games_completed=len(results))
        report = {
            "status": "ok" if all(item.get("status") == "ok" for item in results) else "partial",
            "generated_at": time.time(),
            "games": results,
        }
        report_path = self.state_dir / "tuning_report.json"
        tmp_path = report_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        tmp_path.replace(report_path)
        self._set_status(
            status="completed" if report["status"] == "ok" else "partial",
            current_game=None,
            current_task="complete",
            games_completed=len(results),
        )
        return report

    def start(self, game: str | None = None) -> dict[str, Any]:
        current = self.status()
        if current.get("status") == "running":
            return {"status": "already_running", **current}

        thread = threading.Thread(target=self.tune, args=(game,), daemon=True, name="GameSuggestionTuner")
        thread.start()
        return {"status": "started", **self.status()}


game_tuner = GameTuner()
