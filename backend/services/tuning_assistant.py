"""Search tuner settings and keep the mix that raises validated accuracy."""

from __future__ import annotations

from typing import Any, Callable

from prediction.state.weight_store import WeightStore

# Bounded search: baseline, then a more aggressive step, then a more stable one.
OPTIMIZE_TRIALS: tuple[dict[str, Any], ...] = (
    {"learning_rate": 0.05, "rolling_window": 40, "rounds": 4, "patience": 2, "label": "baseline"},
    {"learning_rate": 0.10, "rolling_window": 28, "rounds": 3, "patience": 2, "label": "aggressive"},
    {"learning_rate": 0.03, "rolling_window": 56, "rounds": 3, "patience": 2, "label": "stable"},
)

# Skip remaining trials when baseline already raises the stored held floor.
EARLY_EXIT_MARGIN_PERCENT = 0.5

# After RF training: guess → verify → keep or restore. Fewer rounds so Train All stays usable.
POST_TRAIN_TRIALS: tuple[dict[str, Any], ...] = (
    {"learning_rate": 0.05, "rolling_window": 40, "rounds": 3, "patience": 2, "label": "baseline"},
    {"learning_rate": 0.10, "rolling_window": 28, "rounds": 2, "patience": 2, "label": "aggressive"},
    {"learning_rate": 0.03, "rolling_window": 56, "rounds": 2, "patience": 2, "label": "stable"},
)


def result_score(result: dict[str, Any] | None) -> float:
    """Prefer the held accuracy floor so a dipping live window cannot win a trial."""
    payload = result or {}
    for key in (
        "held_accuracy_percent",
        "accuracy_percent",
        "best_round_accuracy_percent",
        "highest_rolling_accuracy_percent",
        "rolling_accuracy_percent",
    ):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return -1.0


class TuningAssistant:
    """Try a few tuner settings and restore weights when a trial regresses."""

    def __init__(self, store: WeightStore, on_trial: Callable[[dict[str, Any]], None] | None = None):
        self.store = store
        self.on_trial = on_trial

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.on_trial is None:
            return
        self.on_trial(payload)

    def optimize_game(
        self,
        game_key: str,
        run_trial: Callable[[dict[str, Any]], dict[str, Any]],
        trials: tuple[dict[str, Any], ...] = OPTIMIZE_TRIALS,
    ) -> dict[str, Any]:
        best_snapshot = self.store.snapshot(game_key)
        existing = self.store.load(game_key)
        previous_score = -1.0
        if existing.best_validation_accuracy is not None:
            try:
                previous_score = float(existing.best_validation_accuracy) * 100.0
            except (TypeError, ValueError):
                previous_score = -1.0
        best_result: dict[str, Any] | None = None
        best_score = previous_score
        best_settings: dict[str, Any] | None = None
        trial_log: list[dict[str, Any]] = []
        early_exit = False
        stopped_reason = "all_trials"

        for index, settings in enumerate(trials, start=1):
            before = self.store.snapshot(game_key)
            self.store.clear_last_draw(game_key)
            if index > 1:
                self._emit({
                    "game": game_key,
                    "trial": index,
                    "trials_total": len(trials),
                    "between_runs": True,
                    "run_state": "between_runs",
                    "held_accuracy_percent": best_score if best_score >= 0 else None,
                    "next_run_target_percent": best_score if best_score >= 0 else None,
                    "settings": dict(settings),
                    "message": (
                        f"Between runs: {game_key} held {best_score:.1f}%. "
                        f"Starting {settings.get('label')} trial to beat it."
                    ),
                })
            self._emit({
                "game": game_key,
                "trial": index,
                "trials_total": len(trials),
                "settings": dict(settings),
                "message": (
                    f"{game_key} assistant trial {index}/{len(trials)} "
                    f"({settings.get('label')}, lr={settings.get('learning_rate')}, "
                    f"window={settings.get('rolling_window')})"
                ),
            })
            result = run_trial(settings) or {"status": "error", "game": game_key}
            score = result_score(result)
            improved = score > best_score + 1e-9
            trial_entry = {
                "trial": index,
                "label": settings.get("label"),
                "learning_rate": settings.get("learning_rate"),
                "rolling_window": settings.get("rolling_window"),
                "accuracy_percent": result.get("accuracy_percent"),
                "rolling_accuracy_percent": result.get("rolling_accuracy_percent"),
                "best_round_accuracy_percent": result.get("best_round_accuracy_percent"),
                "score": round(score, 4) if score >= 0 else None,
                "improved": improved,
                "kept": improved,
                "status": result.get("status"),
            }
            trial_log.append(trial_entry)
            if improved:
                best_result = dict(result)
                best_score = score
                best_settings = dict(settings)
                best_snapshot = self.store.snapshot(game_key)
                self._emit({
                    **trial_entry,
                    "game": game_key,
                    "kept": True,
                    "message": (
                        f"{game_key} kept {settings.get('label')} trial "
                        f"at {score:.1f}% rolling/run score"
                    ),
                })
            else:
                self.store.restore(game_key, before if index > 1 else best_snapshot)
                self._emit({
                    **trial_entry,
                    "game": game_key,
                    "kept": False,
                    "message": (
                        f"{game_key} rejected {settings.get('label')} trial "
                        f"({score:.1f}% vs best {best_score:.1f}%)"
                    ),
                })

            baseline_beat_floor = (
                index == 1
                and previous_score >= 0
                and best_score >= previous_score + EARLY_EXIT_MARGIN_PERCENT
            )
            if baseline_beat_floor and index < len(trials):
                early_exit = True
                stopped_reason = "baseline_improved"
                self._emit({
                    "game": game_key,
                    "trial": index,
                    "trials_total": len(trials),
                    "early_exit": True,
                    "kept": True,
                    "score": best_score if best_score >= 0 else None,
                    "held_accuracy_percent": best_score if best_score >= 0 else None,
                    "settings": dict(settings),
                    "message": (
                        f"{game_key} baseline beat the held floor "
                        f"({best_score:.1f}% vs {previous_score:.1f}%); "
                        "skipping remaining trials."
                    ),
                })
                break

        self.store.restore(game_key, best_snapshot)
        self.store.adopt_best(game_key)
        if best_result is None:
            optimized = {
                "status": "ok",
                "game": game_key,
                "held_accuracy_percent": previous_score if previous_score >= 0 else None,
                "accuracy_percent": previous_score if previous_score >= 0 else None,
                "message": "No trial beat the held accuracy floor; kept existing mix.",
            }
        else:
            optimized = dict(best_result)
        optimized["assistant"] = {
            "optimized": True,
            "trials": trial_log,
            "best_settings": best_settings,
            "best_score": best_score if best_score >= 0 else None,
            "previous_score": previous_score if previous_score >= 0 else None,
            "early_exit": early_exit,
            "stopped_reason": stopped_reason,
            "kept_existing": best_result is None,
        }
        if best_settings:
            optimized["learning_rate"] = best_settings.get("learning_rate")
            optimized["rolling_window"] = best_settings.get("rolling_window")
        return optimized
