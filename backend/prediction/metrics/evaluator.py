"""Honest performance metrics — no inflated accuracy percentages."""

from __future__ import annotations

import random
from typing import Callable

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


def walk_forward_backtest(
    history: list[Draw],
    rules: GameRules,
    predict_fn: Callable[[list[Draw]], PredictionTicket | list[int]],
    window: int = 200,
    min_draws: int = 50,
    target_accuracy: float = 0.98,
    verification_rounds: int = 1,
    update_fn: Callable[[Draw, list[Draw], float], dict | None] | None = None,
) -> dict:
    """
    Chronological verification: train on the first two-thirds, then recursively
    predict the final third with an expanding history. Each scored draw is
    retained with its date and partial-hit accuracy percentage.

    Returns honest metrics always shown alongside random baseline.
    """
    if len(history) < min_draws + 1:
        return {
            "status": "insufficient_data",
            "draws_available": len(history),
            "min_required": min_draws + 1,
        }

    split_index = max(1, (len(history) * 2) // 3)
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

    max_rounds = max(1, int(verification_rounds or 1))
    rounds_run = 0
    target_reached = False
    for round_index in range(max_rounds):
        rounds_run += 1
        verification_history = history[:test_start]
        for index in range(test_start, len(history)):
            train_hist = verification_history
            actual = history[index]
            try:
                ticket = predict_fn(train_hist)
                predicted = ticket.primary if isinstance(ticket, PredictionTicket) else ticket
            except Exception:
                verification_history = verification_history + [actual]
                continue

            hits = _partial_hits(predicted, actual.primary)
            partial_hits.append(hits)
            exact_match = _exact_match(predicted, actual.primary)
            if exact_match:
                exact_matches += 1
            sum_errors.append(abs(sum(predicted) - sum(actual.primary)))
            evaluated += 1
            accuracy = hits / max(rules.primary_count, 1)
            suggestion_history.append({
                "round": round_index + 1,
                "iteration": evaluated,
                "validates_iteration": evaluated,
                "previous_iteration_accuracy_percent": previous_accuracy_percent,
                "draw_index": index,
                "draw_id": actual.draw_id,
                "draw_date": actual.draw_date.isoformat() if actual.draw_date else None,
                "draw_datetime": actual.metadata.get("draw_datetime") if actual.metadata else None,
                "predicted_numbers": list(predicted),
                "actual_numbers": list(actual.primary),
                "partial_hits": hits,
                "accuracy": round(accuracy, 6),
                "accuracy_percent": round(accuracy * 100, 2),
                "exact_match": exact_match,
                "validation_status": "hit" if hits > 0 else "miss",
            })
            current_suggestion = suggestion_history[-1]
            if highest_suggestion is None or (
                current_suggestion["accuracy_percent"] > highest_suggestion["accuracy_percent"]
            ):
                highest_suggestion = dict(current_suggestion)

            # Learn from this verified draw before advancing to the next one.
            if update_fn is not None:
                try:
                    cumulative_accuracy = sum(partial_hits) / (
                        max(rules.primary_count, 1) * len(partial_hits)
                    )
                    update_result = update_fn(
                        actual,
                        verification_history,
                        cumulative_accuracy,
                    )
                    if isinstance(update_result, dict):
                        current_suggestion["weights_updated"] = not bool(update_result.get("skipped"))
                        current_suggestion["balance_promoted"] = bool(update_result.get("promoted"))
                        current_suggestion["best_validation_accuracy"] = update_result.get(
                            "best_validation_accuracy"
                        )
                        current_suggestion["updated_at"] = update_result.get("updated_at")
                except Exception as exc:
                    current_suggestion["weight_update_error"] = str(exc)

            # Recursive verification: the next prediction sees the verified draw
            # and the updated per-game ensemble weights.
            verification_history = verification_history + [actual]
            previous_accuracy_percent = current_suggestion["accuracy_percent"]

        if partial_hits:
            target_reached = (float(np.mean(partial_hits)) / max(rules.primary_count, 1)) >= float(target_accuracy)
            if target_reached:
                break

    if evaluated == 0:
        return {"status": "no_evaluations", "evaluated": 0}

    mean_hits = float(np.mean(partial_hits))
    baseline = random_baseline_metrics(history[test_start:], rules)
    baseline_hits = baseline.get("mean_partial_hits", 0.0) or 0.001

    aggregate_accuracy = mean_hits / max(rules.primary_count, 1)
    return {
        "status": "ok",
        "split": {
            "training_draws": split_index,
            "test_draws": len(history) - split_index,
            "training_fraction": round(split_index / len(history), 4),
            "test_fraction": round((len(history) - split_index) / len(history), 4),
        },
        "evaluated_draws": evaluated,
        "mean_partial_hits": round(mean_hits, 4),
        "accuracy": round(aggregate_accuracy, 6),
        "accuracy_percent": round(aggregate_accuracy * 100, 2),
        "target_accuracy": float(target_accuracy),
        "target_accuracy_percent": round(float(target_accuracy) * 100, 2),
        "target_reached": target_reached,
        "verification_rounds": rounds_run,
        "max_verification_rounds": max_rounds,
        "suggestion_history": suggestion_history,
        "highest_suggestion": highest_suggestion,
        "highest_accuracy_percent": (
            highest_suggestion.get("accuracy_percent") if highest_suggestion else None
        ),
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