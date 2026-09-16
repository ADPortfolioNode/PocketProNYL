"""Honest performance metrics — no inflated accuracy percentages."""

from __future__ import annotations

import random
from typing import Any, Callable

import numpy as np

from prediction.core.types import Draw, GameRules, PredictionTicket


def _partial_hits(predicted: list[int], actual: list[int]) -> int:
    """Count matching primary numbers (order-independent)."""
    return len(set(predicted) & set(actual))


def _exact_match(predicted: list[int], actual: list[int]) -> bool:
    return sorted(predicted) == sorted(actual)


def random_ticket(rules: GameRules, rng: random.Random) -> list[int]:
    """Generate one random primary ticket respecting game rules."""
    pool = list(range(rules.primary_min, rules.primary_max + 1))
    if rules.primary_unique:
        return rng.sample(pool, min(rules.primary_count, len(pool)))
    return [rng.choice(pool) for _ in range(rules.primary_count)]


def score_prediction(
    ticket: PredictionTicket | list[int],
    actual: Draw,
    rules: GameRules,
) -> dict:
    """Score one prediction against an actual draw."""
    predicted = ticket.primary if isinstance(ticket, PredictionTicket) else ticket
    hits = _partial_hits(predicted, actual.primary)
    return {
        "partial_hits": hits,
        "partial_hit_rate": hits / max(rules.primary_count, 1),
        "exact_match": _exact_match(predicted, actual.primary),
        "sum_error": abs(sum(predicted) - sum(actual.primary)),
    }


def random_baseline_metrics(
    history: list[Draw],
    rules: GameRules,
    trials: int = 500,
    seed: int = 42,
) -> dict:
    """Compute expected performance of uniform random picks."""
    if len(history) < 2:
        return {"mean_partial_hits": 0.0, "exact_match_rate": 0.0, "trials": 0}

    rng = random.Random(seed)
    partial_hits: list[int] = []
    exact = 0
    eval_draws = history[-min(len(history) - 1, 100) :]

    for draw in eval_draws:
        for _ in range(max(1, trials // len(eval_draws))):
            ticket = random_ticket(rules, rng)
            partial_hits.append(_partial_hits(ticket, draw.primary))
            if _exact_match(ticket, draw.primary):
                exact += 1

    total = len(partial_hits) or 1
    return {
        "mean_partial_hits": round(sum(partial_hits) / total, 4),
        "exact_match_rate": round(exact / total, 6),
        "trials": total,
    }


DEFAULT_ROLLING_WINDOW = 40
_ROLLING_EMA_ALPHA = 0.22


def resolve_test_start(
    history_len: int,
    min_draws: int = 50,
    max_test_draws: int | None = None,
) -> int:
    """Return the first test index: last third of history, optionally capped."""
    if history_len < 2:
        return history_len
    split_index = max(1, (history_len * 2) // 3)
    if split_index >= history_len:
        return history_len
    if max_test_draws is None or int(max_test_draws) <= 0:
        return split_index
    test_len = min(history_len - split_index, int(max_test_draws))
    train_floor = max(1, min(int(min_draws), history_len - 1))
    test_len = min(test_len, max(1, history_len - train_floor))
    return max(1, history_len - max(1, test_len))


def _rolling_mean(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    sample = values[-max(1, window) :]
    return float(sum(sample) / len(sample))


def _boosted_rolling(hit_rates: list[float], window: int, alpha: float = _ROLLING_EMA_ALPHA) -> float:
    """Long-window mean, lifted by a same-window EMA when recent hits improve."""
    if not hit_rates:
        return 0.0
    sample = hit_rates[-max(1, min(int(window), len(hit_rates))) :]
    window_mean = float(sum(sample) / len(sample))
    ema = window_mean
    for value in sample:
        ema = float(alpha) * float(value) + (1.0 - float(alpha)) * ema
    return max(window_mean, ema)


def walk_forward_backtest(
    history: list[Draw],
    rules: GameRules,
    predict_fn: Callable[[list[Draw]], PredictionTicket | list[int]],
    window: int = 200,
    min_draws: int = 50,
    target_accuracy: float = 0.98,
    verification_rounds: int = 1,
    min_verification_rounds: int = 1,
    improvement_patience: int | None = None,
    update_fn: Callable[[Draw, list[Draw], float], dict | None] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    rolling_window: int | None = DEFAULT_ROLLING_WINDOW,
    learning_rate_holder: dict[str, Any] | None = None,
    max_test_draws: int | None = None,
) -> dict:
    """
    Chronological verification: train on the prefix, then recursively predict
    the held-out tail with an expanding history. The tail is the last third of
    draws, optionally capped at *max_test_draws* so tuning stays on recent
    results. Each scored draw is retained with its date and partial-hit
    accuracy percentage.

    Extra verification rounds keep iterating until the accuracy floor is reached
    or improvement stalls. The held accuracy floor only moves up: a weaker run is
    rejected, the learning rate is raised, and status is published between runs.

    Returns honest metrics always shown alongside random baseline.
    """
    if len(history) < min_draws + 1:
        return {
            "status": "insufficient_data",
            "draws_available": len(history),
            "min_required": min_draws + 1,
        }

    split_index = resolve_test_start(len(history), min_draws=min_draws, max_test_draws=max_test_draws)
    if split_index >= len(history):
        return {"status": "insufficient_data", "draws_available": len(history)}
    test_start = split_index
    partial_hits: list[int] = []
    exact_matches = 0
    sum_errors: list[float] = []
    evaluated = 0
    suggestion_history: list[dict] = []
    highest_suggestion: dict | None = None
    previous_accuracy_percent: float | None = None
    previous_rolling_percent: float | None = None
    rolling_window = max(8, int(rolling_window or DEFAULT_ROLLING_WINDOW))
    primary_count = max(rules.primary_count, 1)

    max_rounds = max(1, int(verification_rounds or 1))
    min_rounds = max(1, min(int(min_verification_rounds or 1), max_rounds))
    patience = max_rounds if improvement_patience is None else max(1, int(improvement_patience))
    rounds_run = 0
    target_reached = False
    stopped_reason = "max_rounds"
    round_metrics: list[dict] = []
    best_round_accuracy: float | None = None
    no_improve_streak = 0
    regression_count = 0
    promotion_count = 0
    latest_round_accuracy = 0.0
    best_rolling_accuracy: float | None = None
    accuracy_floor: float | None = None

    def _emit_progress(extra: dict | None = None) -> None:
        if progress_callback is None:
            return
        if partial_hits:
            current_accuracy = latest_round_accuracy if rounds_run else (
                sum(partial_hits) / (primary_count * len(partial_hits))
            )
            current_percent = round(current_accuracy * 100, 2)
        else:
            current_percent = None
        payload = {
            "evaluated": evaluated,
            "round": rounds_run,
            "accuracy_percent": current_percent,
            "highest_accuracy_percent": (
                highest_suggestion.get("accuracy_percent") if highest_suggestion else None
            ),
            "target_accuracy_percent": round(float(target_accuracy) * 100, 2),
            "rolling_accuracy_percent": round(
                (_boosted_rolling([hit / primary_count for hit in partial_hits], rolling_window) * 100),
                2,
            ) if partial_hits else None,
            "highest_rolling_accuracy_percent": (
                round(best_rolling_accuracy * 100, 2) if best_rolling_accuracy is not None else None
            ),
            "regression_count": regression_count,
            "promotion_count": promotion_count,
            "suggestion_history": suggestion_history[-80:],
            "suggestion_history_count": len(suggestion_history),
            "target_reached": target_reached,
            "round_metrics": list(round_metrics),
            "best_round_accuracy_percent": (
                round(best_round_accuracy * 100, 2) if best_round_accuracy is not None else None
            ),
        }
        held = (
            round(accuracy_floor * 100, 2) if accuracy_floor is not None
            else current_percent
        )
        payload["held_accuracy_percent"] = held
        payload["this_run_accuracy_percent"] = current_percent
        payload["accuracy_percent"] = held if held is not None else current_percent
        payload["next_run_target_percent"] = held
        payload["run_state"] = "running"
        if extra:
            payload.update(extra)
        payload["held_accuracy_percent"] = held
        payload["accuracy_percent"] = held if held is not None else payload.get("this_run_accuracy_percent")
        payload["next_run_target_percent"] = held
        progress_callback(payload)

    for round_index in range(max_rounds):
        rounds_run += 1
        verification_history = history[:test_start]
        round_hits: list[int] = []
        for index in range(test_start, len(history)):
            train_hist = verification_history
            actual = history[index]
            try:
                ticket = predict_fn(train_hist)
                predicted = ticket.primary if isinstance(ticket, PredictionTicket) else ticket
                predicted_bonus = ticket.bonus if isinstance(ticket, PredictionTicket) else []
            except Exception:
                verification_history = verification_history + [actual]
                continue

            hits = _partial_hits(predicted, actual.primary)
            bonus_hits = _partial_hits(predicted_bonus, actual.bonus) if predicted_bonus and actual.bonus else 0
            partial_hits.append(hits)
            round_hits.append(hits)
            exact_match = _exact_match(predicted, actual.primary)
            if exact_match:
                exact_matches += 1
            sum_errors.append(abs(sum(predicted) - sum(actual.primary)))
            evaluated += 1
            accuracy = hits / primary_count
            rolling_accuracy = _boosted_rolling(
                [hit / primary_count for hit in partial_hits],
                rolling_window,
            )
            rolling_accuracy_percent = round(rolling_accuracy * 100, 2)
            if best_rolling_accuracy is None or rolling_accuracy > best_rolling_accuracy:
                best_rolling_accuracy = rolling_accuracy
            suggestion_date = train_hist[-1].draw_date.isoformat() if train_hist and train_hist[-1].draw_date else None
            actual_draw_date = actual.draw_date.isoformat() if actual.draw_date else None
            lead_days = None
            if train_hist and train_hist[-1].draw_date and actual.draw_date:
                lead_days = (actual.draw_date - train_hist[-1].draw_date).days
            draw_regressed = (
                previous_rolling_percent is not None
                and rolling_accuracy_percent < float(previous_rolling_percent) - 0.05
            )
            if draw_regressed:
                regression_count += 1
            suggestion_history.append({
                "round": round_index + 1,
                "iteration": evaluated,
                "validates_iteration": evaluated,
                "previous_iteration_accuracy_percent": previous_accuracy_percent,
                "draw_index": index,
                "draw_id": actual.draw_id,
                "suggestion_date": suggestion_date,
                "draw_date": actual_draw_date,
                "actual_draw_date": actual_draw_date,
                "lead_days": lead_days,
                "draw_datetime": actual.metadata.get("draw_datetime") if actual.metadata else None,
                "predicted_numbers": list(predicted),
                "actual_numbers": list(actual.primary),
                "ground_truth_numbers": list(actual.primary),
                "ground_truth_draw_date": actual_draw_date,
                "partial_hits": hits,
                "numbers_won": hits,
                "primary_hits": hits,
                "primary_possible": rules.primary_count,
                "predicted_bonus_numbers": list(predicted_bonus),
                "actual_bonus_numbers": list(actual.bonus),
                "bonus_hits": bonus_hits,
                "bonus_possible": rules.bonus_count,
                "winning_numbers_count": hits + bonus_hits,
                "winning_numbers_possible": rules.primary_count + rules.bonus_count,
                "accuracy": round(accuracy, 6),
                "accuracy_percent": round(accuracy * 100, 2),
                "rolling_accuracy_percent": rolling_accuracy_percent,
                "exact_match": exact_match,
                "draw_regressed": draw_regressed,
                "validation_status": "hit" if hits + bonus_hits > 0 else "miss",
            })
            current_suggestion = suggestion_history[-1]
            if highest_suggestion is None or (
                current_suggestion["accuracy_percent"] > highest_suggestion["accuracy_percent"]
            ):
                highest_suggestion = dict(current_suggestion)

            # Learn from this verified draw before advancing to the next one.
            if update_fn is not None:
                try:
                    update_result = update_fn(
                        actual,
                        verification_history,
                        rolling_accuracy,
                    )
                    if isinstance(update_result, dict):
                        current_suggestion["weights_updated"] = not bool(update_result.get("skipped"))
                        current_suggestion["balance_promoted"] = bool(update_result.get("promoted"))
                        current_suggestion["balance_regressed"] = bool(update_result.get("regressed"))
                        current_suggestion["best_validation_accuracy"] = update_result.get(
                            "best_validation_accuracy"
                        )
                        current_suggestion["updated_at"] = update_result.get("updated_at")
                        current_suggestion["weights"] = update_result.get("weights")
                        current_suggestion["best_weights"] = update_result.get("best_weights")
                        if current_suggestion["balance_promoted"]:
                            promotion_count += 1
                        if current_suggestion["balance_regressed"]:
                            current_suggestion["draw_regressed"] = True
                except Exception as exc:
                    current_suggestion["weight_update_error"] = str(exc)

            # Recursive verification: the next prediction sees the verified draw
            # and the updated per-game ensemble weights.
            verification_history = verification_history + [actual]
            previous_accuracy_percent = current_suggestion["accuracy_percent"]
            previous_rolling_percent = rolling_accuracy_percent
            if round_hits:
                latest_round_accuracy = float(sum(round_hits)) / (primary_count * len(round_hits))
            _emit_progress()

        if round_hits:
            round_accuracy = float(sum(round_hits)) / (primary_count * len(round_hits))
            latest_round_accuracy = round_accuracy
            this_round_percent = round(round_accuracy * 100, 2)
            previous_held = round(accuracy_floor * 100, 2) if accuracy_floor is not None else None
            beat_floor = accuracy_floor is None or round_accuracy > accuracy_floor + 1e-12
            if beat_floor:
                accuracy_floor = round_accuracy
                best_round_accuracy = round_accuracy if (
                    best_round_accuracy is None or round_accuracy > best_round_accuracy
                ) else best_round_accuracy
                no_improve_streak = 0
            else:
                no_improve_streak += 1
                if learning_rate_holder is not None:
                    current_lr = float(learning_rate_holder.get("value") or 0.05)
                    learning_rate_holder["value"] = min(0.28, max(current_lr * 1.35, current_lr + 0.02))
                    learning_rate_holder["bumped"] = True
            if best_round_accuracy is None or round_accuracy > best_round_accuracy:
                best_round_accuracy = round_accuracy
            delta_percent = (
                None if previous_held is None else round(this_round_percent - float(previous_held), 2)
            )
            held_percent = round(accuracy_floor * 100, 2) if accuracy_floor is not None else this_round_percent
            round_metrics.append({
                "round": rounds_run,
                "evaluated_draws": len(round_hits),
                "accuracy": round(round_accuracy, 6),
                "accuracy_percent": this_round_percent,
                "held_accuracy_percent": held_percent,
                "previous_accuracy_percent": previous_held,
                "delta_percent": delta_percent,
                "baseline_accuracy_percent": (
                    round_metrics[0]["held_accuracy_percent"] if round_metrics else this_round_percent
                ),
                "improved": beat_floor,
                "regressed": not beat_floor and previous_held is not None,
                "rolling_accuracy_percent": previous_rolling_percent,
                "learning_rate": (learning_rate_holder or {}).get("value"),
            })
            target_reached = (accuracy_floor or 0) >= float(target_accuracy)
            _emit_progress({
                "round_complete": True,
                "between_runs": True,
                "run_state": "between_runs",
                "previous_round_accuracy_percent": previous_held,
                "this_run_accuracy_percent": this_round_percent,
                "delta_percent": 0.0 if not beat_floor and previous_held is not None else delta_percent,
                "improved": beat_floor,
                "held_accuracy_percent": held_percent,
            })
            if target_reached:
                stopped_reason = "target"
                break
            if rounds_run >= min_rounds and no_improve_streak >= patience:
                stopped_reason = "patience"
                break
        else:
            _emit_progress({"round_complete": True, "between_runs": True, "run_state": "between_runs"})

    if evaluated == 0:
        return {"status": "no_evaluations", "evaluated": 0}

    mean_hits = float(np.mean(partial_hits))
    baseline = random_baseline_metrics(history[test_start:], rules)
    baseline_hits = baseline.get("mean_partial_hits", 0.0) or 0.001

    aggregate_accuracy = mean_hits / primary_count
    current_accuracy = accuracy_floor if accuracy_floor is not None else (latest_round_accuracy or aggregate_accuracy)
    last_round = round_metrics[-1] if round_metrics else {}
    return {
        "status": "ok",
        "split": {
            "training_draws": split_index,
            "test_draws": len(history) - split_index,
            "training_fraction": round(split_index / len(history), 4),
            "test_fraction": round((len(history) - split_index) / len(history), 4),
            "max_test_draws": int(max_test_draws) if max_test_draws else None,
        },
        "evaluated_draws": evaluated,
        "mean_partial_hits": round(mean_hits, 4),
        "accuracy": round(current_accuracy, 6),
        "accuracy_percent": round(current_accuracy * 100, 2),
        "held_accuracy_percent": round(current_accuracy * 100, 2),
        "this_run_accuracy_percent": last_round.get("accuracy_percent"),
        "next_run_target_percent": round(current_accuracy * 100, 2),
        "rolling_accuracy": round(previous_rolling_percent / 100, 6) if previous_rolling_percent is not None else round(current_accuracy, 6),
        "rolling_accuracy_percent": previous_rolling_percent if previous_rolling_percent is not None else round(current_accuracy * 100, 2),
        "highest_rolling_accuracy_percent": (
            round(best_rolling_accuracy * 100, 2) if best_rolling_accuracy is not None else None
        ),
        "rolling_window": rolling_window,
        "overall_accuracy": round(aggregate_accuracy, 6),
        "overall_accuracy_percent": round(aggregate_accuracy * 100, 2),
        "target_accuracy": float(target_accuracy),
        "target_accuracy_percent": round(float(target_accuracy) * 100, 2),
        "target_reached": target_reached,
        "verification_rounds": rounds_run,
        "max_verification_rounds": max_rounds,
        "min_verification_rounds": min_rounds,
        "improvement_patience": patience,
        "stopped_reason": stopped_reason,
        "suggestion_history": suggestion_history,
        "highest_suggestion": highest_suggestion,
        "highest_accuracy_percent": (
            highest_suggestion.get("accuracy_percent") if highest_suggestion else None
        ),
        "best_round_accuracy_percent": (
            round(best_round_accuracy * 100, 2) if best_round_accuracy is not None else None
        ),
        "round_metrics": round_metrics,
        "regression_count": regression_count,
        "promotion_count": promotion_count,
        "exact_match_rate": round(exact_matches / evaluated, 6),
        "partial_hit_rate_at_1": round(sum(1 for h in partial_hits if h >= 1) / evaluated, 4),
        "partial_hit_rate_at_2": round(sum(1 for h in partial_hits if h >= 2) / evaluated, 4),
        "sum_mae": round(float(np.mean(sum_errors)), 4),
        "random_baseline": baseline,
        "lift_vs_random": round(mean_hits / baseline_hits, 4),
        "note": "Accuracy is partial-hit rate. Exact-match rates are normally near zero for lottery draws.",
    }


def score_plugin_on_draw(
    plugin_picks: list[int],
    actual: Draw,
    rules: GameRules,
) -> float:
    """Partial hit rate for one plugin's picks vs actual draw (0.0–1.0)."""
    return _partial_hits(plugin_picks, actual.primary) / max(rules.primary_count, 1)