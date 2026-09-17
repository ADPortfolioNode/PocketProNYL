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
from services.tuning_assistant import TuningAssistant

# Keep iterating past a single walk-forward pass so regressions get another
# chance to be rejected and accuracy can still climb.
RELENTLESS_VERIFICATION_ROUNDS = 6
RELENTLESS_MIN_ROUNDS = 2
RELENTLESS_PATIENCE = 2
RELENTLESS_ROLLING_WINDOW = 40
HISTORY_LIMIT = 80


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
            "metrics_by_game": {},
            "run_history": [],
            "last_run": None,
            "progress": None,
            "run_state": "idle",
            "assistant": None,
            "objective": "optimize tuner settings, reject regressions, raise validated accuracy",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set_status(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)
            self._status["updated_at"] = time.time()

    def _store_game_metrics(self, game_key: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            by_game = dict(self._status.get("metrics_by_game") or {})
            by_game[game_key] = metrics
            self._status["metrics_by_game"] = by_game
            self._status["tuning_metrics"] = metrics
            self._status["updated_at"] = time.time()

    def _progress_message(self, game_key: str, payload: dict[str, Any], *, round_complete: bool) -> str:
        round_no = payload.get("round") or 1
        held = payload.get("held_accuracy_percent", payload.get("accuracy_percent"))
        this_run = payload.get("this_run_accuracy_percent")
        delta = payload.get("delta_percent")
        held_text = f"{float(held):.1f}%" if held is not None else "n/a"
        if payload.get("between_runs") or (round_complete and payload.get("run_state") == "between_runs"):
            if payload.get("improved"):
                sign = "+" if delta is not None and float(delta) > 0 else ""
                delta_text = f" ({sign}{float(delta):.1f} pts)" if delta is not None else ""
                return f"Between runs: {game_key} held {held_text}{delta_text}. Next run must beat {held_text}."
            this_text = f"{float(this_run):.1f}%" if this_run is not None else "n/a"
            return f"Between runs: {game_key} held {held_text}. This run {this_text} did not beat the floor; retrying."
        if round_complete and delta is not None:
            sign = "+" if float(delta) > 0 else ""
            return f"{game_key} run {round_no}: {held_text} ({sign}{float(delta):.1f} vs previous run)"
        if round_complete:
            return f"{game_key} run {round_no}: {held_text} (baseline)"
        return f"{game_key} validating run {round_no}: {held_text}"

    def _on_progress(self, game_key: str, payload: dict[str, Any]) -> None:
        history = list(payload.get("suggestion_history") or [])[-HISTORY_LIMIT:]
        round_complete = bool(payload.get("round_complete"))
        between_runs = bool(payload.get("between_runs") or payload.get("run_state") == "between_runs")
        run = {
            "game": game_key,
            "round": payload.get("round"),
            "accuracy_percent": payload.get("held_accuracy_percent", payload.get("accuracy_percent")),
            "this_run_accuracy_percent": payload.get("this_run_accuracy_percent"),
            "held_accuracy_percent": payload.get("held_accuracy_percent"),
            "next_run_target_percent": payload.get("next_run_target_percent"),
            "previous_accuracy_percent": payload.get("previous_round_accuracy_percent"),
            "delta_percent": payload.get("delta_percent"),
            "improved": payload.get("improved"),
            "rolling_accuracy_percent": payload.get("rolling_accuracy_percent"),
            "best_round_accuracy_percent": payload.get("best_round_accuracy_percent"),
            "evaluated_draws": payload.get("evaluated"),
            "promotion_count": payload.get("promotion_count", 0),
            "regression_count": payload.get("regression_count", 0),
            "completed": round_complete,
            "between_runs": between_runs,
        }
        metrics = {
            "accuracy_percent": payload.get("held_accuracy_percent", payload.get("accuracy_percent")),
            "this_run_accuracy_percent": payload.get("this_run_accuracy_percent"),
            "held_accuracy_percent": payload.get("held_accuracy_percent"),
            "next_run_target_percent": payload.get("next_run_target_percent"),
            "highest_accuracy_percent": payload.get("highest_accuracy_percent"),
            "best_round_accuracy_percent": payload.get("best_round_accuracy_percent"),
            "target_accuracy_percent": payload.get("target_accuracy_percent"),
            "live_rolling_accuracy_percent": payload.get("rolling_accuracy_percent"),
            "rolling_accuracy_percent": max(
                [value for value in (
                    payload.get("highest_rolling_accuracy_percent"),
                    payload.get("rolling_accuracy_percent"),
                    payload.get("held_accuracy_percent"),
                ) if isinstance(value, (int, float))],
                default=payload.get("rolling_accuracy_percent"),
            ),
            "highest_rolling_accuracy_percent": payload.get("highest_rolling_accuracy_percent"),
            "regression_count": payload.get("regression_count", 0),
            "promotion_count": payload.get("promotion_count", 0),
            "verification_round": payload.get("round"),
            "evaluated_draws": payload.get("evaluated"),
            "suggestion_history_count": payload.get("suggestion_history_count") or len(history),
            "suggestion_history": history,
            "round_metrics": payload.get("round_metrics") or [],
            "delta_percent": payload.get("delta_percent"),
            "previous_run_accuracy_percent": payload.get("previous_round_accuracy_percent"),
            "target_reached": payload.get("target_reached", False),
            "live": True,
        }
        progress = {
            "game": game_key,
            "round": payload.get("round"),
            "accuracy_percent": payload.get("held_accuracy_percent", payload.get("accuracy_percent")),
            "this_run_accuracy_percent": payload.get("this_run_accuracy_percent"),
            "held_accuracy_percent": payload.get("held_accuracy_percent"),
            "next_run_target_percent": payload.get("next_run_target_percent"),
            "delta_percent": payload.get("delta_percent"),
            "improved": payload.get("improved"),
            "round_complete": round_complete,
            "between_runs": between_runs,
            "run_state": "between_runs" if between_runs else "running",
            "message": self._progress_message(game_key, payload, round_complete=round_complete),
        }
        status_update = {
            "current_game": game_key,
            "run_state": "between_runs" if between_runs else "running",
            "current_task": (
                "between_runs" if between_runs
                else f"validating_round_{payload.get('round') or 1}"
            ),
            "progress": progress,
        }
        if round_complete:
            status_update["last_run"] = run
        if round_complete:
            with self._lock:
                run_history = list(self._status.get("run_history") or [])
                run_history.append(run)
                status_update["run_history"] = run_history[-40:]
                metrics["run_history"] = [
                    item for item in status_update["run_history"] if item.get("game") == game_key
                ]
        else:
            with self._lock:
                metrics["run_history"] = [
                    item for item in (self._status.get("run_history") or []) if item.get("game") == game_key
                ]
        self._set_status(**status_update)
        self._store_game_metrics(game_key, metrics)

    def _on_assistant_trial(self, payload: dict[str, Any]) -> None:
        self._set_status(
            assistant={
                "mode": "optimize",
                "game": payload.get("game"),
                "trial": payload.get("trial"),
                "trials_total": payload.get("trials_total"),
                "label": payload.get("label") or (payload.get("settings") or {}).get("label"),
                "learning_rate": payload.get("learning_rate") or (payload.get("settings") or {}).get("learning_rate"),
                "rolling_window": payload.get("rolling_window") or (payload.get("settings") or {}).get("rolling_window"),
                "kept": payload.get("kept"),
                "score": payload.get("score"),
            },
            current_task=str(payload.get("label") or "optimize_trial"),
            run_state="between_runs" if payload.get("between_runs") else "running",
            progress={
                "game": payload.get("game"),
                "message": payload.get("message"),
                "trial": payload.get("trial"),
                "accuracy_percent": payload.get("held_accuracy_percent", payload.get("accuracy_percent")),
                "held_accuracy_percent": payload.get("held_accuracy_percent"),
                "next_run_target_percent": payload.get("next_run_target_percent"),
                "between_runs": bool(payload.get("between_runs")),
                "run_state": "between_runs" if payload.get("between_runs") else "running",
                "rolling_accuracy_percent": payload.get("rolling_accuracy_percent"),
            },
        )

    def tune_game(self, game: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        game_key = str(game or "").strip().lower()
        if game_key not in GAME_CONFIGS:
            return {"status": "error", "game": game_key, "message": "Unknown game."}

        trial = dict(settings or {})
        started = time.time()
        self._set_status(current_game=game_key, current_task="loading_draw_history")
        try:
            self._set_status(current_task=str(trial.get("label") or "iterative_validation"))
            engine = LotteryPredictionEngine(str(self.state_dir))
            metrics = engine.backtest(
                game_key,
                verification_rounds=int(trial.get("rounds") or RELENTLESS_VERIFICATION_ROUNDS),
                min_verification_rounds=RELENTLESS_MIN_ROUNDS,
                improvement_patience=int(trial.get("patience") or RELENTLESS_PATIENCE),
                rolling_window=int(trial.get("rolling_window") or RELENTLESS_ROLLING_WINDOW),
                learning_rate=trial.get("learning_rate"),
                progress_callback=lambda payload: self._on_progress(game_key, payload),
            )
            weights = WeightStore(str(self.state_dir)).load(game_key)
            result = {
                "status": metrics.get("status", "ok"),
                "game": game_key,
                "training_draws": (metrics.get("split") or {}).get("training_draws"),
                "test_draws": (metrics.get("split") or {}).get("test_draws"),
                "evaluated_draws": metrics.get("evaluated_draws", 0),
                "accuracy": metrics.get("accuracy"),
                "accuracy_percent": metrics.get("accuracy_percent"),
                "rolling_accuracy_percent": metrics.get("rolling_accuracy_percent"),
                "highest_rolling_accuracy_percent": metrics.get("highest_rolling_accuracy_percent"),
                "highest_accuracy_percent": metrics.get("highest_accuracy_percent"),
                "best_round_accuracy_percent": metrics.get("best_round_accuracy_percent"),
                "target_reached": metrics.get("target_reached", False),
                "verification_rounds": metrics.get("verification_rounds", 0),
                "stopped_reason": metrics.get("stopped_reason"),
                "regression_count": metrics.get("regression_count", 0),
                "promotion_count": metrics.get("promotion_count", 0),
                "delta_percent": (metrics.get("round_metrics") or [{}])[-1].get("delta_percent") if (metrics.get("round_metrics") or []) else None,
                "suggestion_history_count": len(metrics.get("suggestion_history") or []),
                "suggestion_history": (metrics.get("suggestion_history") or [])[-HISTORY_LIMIT:],
                "training_seconds": round(time.time() - started, 2),
                "learning_rate": trial.get("learning_rate"),
                "rolling_window": trial.get("rolling_window") or RELENTLESS_ROLLING_WINDOW,
                "assistant_trial": trial.get("label"),
                "message": metrics.get("note"),
            }
            last_entry = (result.get("suggestion_history") or [])[-1:]
            round_metrics = metrics.get("round_metrics") or []
            last_round = round_metrics[-1] if round_metrics else {}
            with self._lock:
                game_runs = [
                    item for item in (self._status.get("run_history") or []) if item.get("game") == game_key
                ]
            tuning_metrics = {
                "accuracy_percent": result.get("accuracy_percent"),
                "held_accuracy_percent": result.get("held_accuracy_percent", result.get("accuracy_percent")),
                "this_run_accuracy_percent": result.get("this_run_accuracy_percent"),
                "next_run_target_percent": result.get("next_run_target_percent", result.get("accuracy_percent")),
                "highest_accuracy_percent": result.get("highest_accuracy_percent"),
                "best_round_accuracy_percent": result.get("best_round_accuracy_percent"),
                "rolling_accuracy_percent": metrics.get("rolling_accuracy_percent")
                if metrics.get("rolling_accuracy_percent") is not None
                else (last_entry[0].get("rolling_accuracy_percent") if last_entry else None),
                "highest_rolling_accuracy_percent": metrics.get("highest_rolling_accuracy_percent"),
                "previous_run_accuracy_percent": last_round.get("previous_accuracy_percent"),
                "delta_percent": last_round.get("delta_percent"),
                "target_accuracy_percent": metrics.get("target_accuracy_percent"),
                "random_baseline": metrics.get("random_baseline"),
                "lift_vs_random": metrics.get("lift_vs_random"),
                "regression_count": result.get("regression_count"),
                "promotion_count": result.get("promotion_count"),
                "verification_round": result.get("verification_rounds"),
                "evaluated_draws": result.get("evaluated_draws"),
                "stopped_reason": result.get("stopped_reason"),
                "target_reached": result.get("target_reached"),
                "suggestion_history_count": result.get("suggestion_history_count"),
                "suggestion_history": result.get("suggestion_history", []),
                "round_metrics": round_metrics,
                "run_history": game_runs,
                "weights": weights.weights,
                "best_weights": weights.best_weights,
                "live": False,
            }
            game_progress = {
                "game": game_key,
                "round": result.get("verification_rounds"),
                "accuracy_percent": result.get("accuracy_percent"),
                "held_accuracy_percent": result.get("held_accuracy_percent", result.get("accuracy_percent")),
                "this_run_accuracy_percent": result.get("this_run_accuracy_percent"),
                "next_run_target_percent": result.get("next_run_target_percent"),
                "delta_percent": last_round.get("delta_percent"),
                "improved": last_round.get("improved"),
                "round_complete": True,
                "between_runs": True,
                "run_state": "between_runs",
                "game_complete": True,
                "message": (
                    f"Between runs: {game_key} held {result.get('accuracy_percent')}% "
                    f"after {result.get('verification_rounds')} run(s)"
                ),
            }
            self._set_status(
                current_game=game_key,
                run_state="between_runs",
                latest_result=result,
                progress=game_progress,
                last_run={
                    "game": game_key,
                    "round": result.get("verification_rounds"),
                    "accuracy_percent": result.get("accuracy_percent"),
                    "previous_accuracy_percent": last_round.get("previous_accuracy_percent"),
                    "delta_percent": last_round.get("delta_percent"),
                    "improved": last_round.get("improved"),
                    "rolling_accuracy_percent": result.get("rolling_accuracy_percent"),
                    "best_round_accuracy_percent": result.get("best_round_accuracy_percent"),
                    "evaluated_draws": result.get("evaluated_draws"),
                    "completed": True,
                    "game_complete": True,
                },
            )
            WeightStore(str(self.state_dir)).adopt_best(game_key)
            weights = WeightStore(str(self.state_dir)).load(game_key)
            tuning_metrics["weights"] = weights.weights
            tuning_metrics["best_weights"] = weights.best_weights
            self._store_game_metrics(game_key, tuning_metrics)
            return result
        except Exception as exc:
            error_result = {
                "status": "error",
                "game": game_key,
                "message": str(exc),
                "training_seconds": round(time.time() - started, 2),
            }
            self._set_status(latest_result=error_result, current_task="error")
            return error_result

    def optimize_game(self, game: str, trials: tuple[dict[str, Any], ...] | None = None) -> dict[str, Any]:
        assistant = TuningAssistant(
            WeightStore(str(self.state_dir)),
            on_trial=self._on_assistant_trial,
        )
        if trials:
            result = assistant.optimize_game(
                game,
                run_trial=lambda settings: self.tune_game(game, settings=settings),
                trials=trials,
            )
        else:
            result = assistant.optimize_game(
                game,
                run_trial=lambda settings: self.tune_game(game, settings=settings),
            )
        assistant_meta = result.get("assistant") or {}
        best = assistant_meta.get("best_settings") or {}
        weights = WeightStore(str(self.state_dir)).load(game)
        with self._lock:
            current_metrics = dict((self._status.get("metrics_by_game") or {}).get(game) or self._status.get("tuning_metrics") or {})
        current_metrics.update({
            "accuracy_percent": result.get("accuracy_percent"),
            "rolling_accuracy_percent": result.get("rolling_accuracy_percent"),
            "best_round_accuracy_percent": result.get("best_round_accuracy_percent"),
            "assistant_trials": assistant_meta.get("trials") or [],
            "best_settings": best,
            "weights": weights.weights,
            "best_weights": weights.best_weights,
            "live": False,
        })
        self._set_status(
            latest_result=result,
            assistant={
                "mode": "optimize",
                "game": game,
                "label": best.get("label"),
                "learning_rate": best.get("learning_rate"),
                "rolling_window": best.get("rolling_window"),
                "score": assistant_meta.get("best_score"),
                "kept": True,
            },
            progress={
                "game": game,
                "accuracy_percent": result.get("accuracy_percent"),
                "message": (
                    f"{game} assistant kept {best.get('label') or 'best'} "
                    f"at {result.get('accuracy_percent')}%"
                ),
                "game_complete": True,
            },
        )
        self._store_game_metrics(game, current_metrics)
        return result

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
            run_state="running",
            run_history=[],
            last_run=None,
            progress={
                "game": games[0] if games else None,
                "round": 0,
                "run_state": "running",
                "message": f"Queued {len(games)} game(s) for suggestion tuning.",
            },
        )
        results = []
        for game_key in games:
            result = self.optimize_game(game_key)
            results.append(result)
            last_run = self.status().get("last_run") or {}
            self._set_status(
                games_completed=len(results),
                progress={
                    "game": game_key,
                    "round": result.get("verification_rounds"),
                    "accuracy_percent": result.get("accuracy_percent"),
                    "delta_percent": last_run.get("delta_percent"),
                    "game_complete": True,
                    "games_completed": len(results),
                    "games_total": len(games),
                    "message": (
                        f"{game_key} optimized to {result.get('accuracy_percent')}% "
                        f"({len(results)}/{len(games)} games)"
                    ),
                },
            )
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
            run_state="completed",
            games_completed=len(results),
        )
        return report

    def start(self, game: str | None = None) -> dict[str, Any]:
        current = self.status()
        if current.get("status") == "running":
            return {"status": "already_running", **current}

        self._set_status(
            status="running",
            game=game or "all",
            current_task="queued",
            run_history=[],
            last_run=None,
            progress={"message": "Tuning assistant is optimizing suggestion weights."},
            assistant={"mode": "optimize"},
        )
        thread = threading.Thread(target=self.tune, args=(game,), daemon=True, name="GameSuggestionTuner")
        thread.start()
        return {**self.status(), "status": "running"}


game_tuner = GameTuner()
