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


# --- Anti-overfitting: three-way split, significance tests, honest reporting ---
# The most recent draws form a final holdout that is scored once per kept
# configuration and never drives a selection (round retries, trial choice, or
# weight commits all happen on the validation window before it).
DEFAULT_HOLDOUT_FRACTION = 0.15
DEFAULT_MAX_HOLDOUT_DRAWS = 60
MIN_HOLDOUT_DRAWS = 5
MIN_VALIDATION_DRAWS = 5
DEFAULT_SIGNIFICANCE_ALPHA = 0.05
# Validation minus holdout accuracy (percentage points) that flags overfitting.
OVERFIT_GAP_WARN_PERCENT = 2.0
HOLDOUT_P_VALUE_REPLICATES = 2000
CHAMPION_PERMUTATIONS = 5000

# Fields added by the anti-overfit work; callers pass these through unchanged.
HONEST_METRIC_KEYS: tuple[str, ...] = (
    "holdout_accuracy_percent",
    "validation_accuracy_percent",
    "best_round_accuracy_percent",
    "best_round_accuracy_is_optimistic",
    "best_round_accuracy_label",
    "overfit_gap_percent",
    "holdout_lift_vs_random",
    "holdout_p_value",
    "holdout_p_value_method",
    "holdout_mean_partial_hits",
    "holdout_evaluated_draws",
    "holdout_random_baseline",
    "trials_tested",
    "overfit_warning",
    "overfit_warning_reasons",
    "accuracy_source",
    "champion_challenger",
    "weights_committed",
)


def resolve_holdout_size(
    history_len: int,
    min_draws: int = 50,
    holdout_draws: int | None = None,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    max_holdout_draws: int = DEFAULT_MAX_HOLDOUT_DRAWS,
) -> int:
    """Size of the final untouched holdout (most recent draws).

    ``holdout_draws=None`` picks ``min(max_holdout_draws, ~holdout_fraction)``;
    ``0`` (or negative) disables the holdout. The holdout shrinks so the train
    prefix keeps ``min_draws`` and validation keeps a few draws; below
    ``MIN_HOLDOUT_DRAWS`` it is disabled rather than reporting a noisy number.
    """
    if holdout_draws is not None and int(holdout_draws) <= 0:
        return 0
    if holdout_draws is None:
        size = min(int(max_holdout_draws), int(round(history_len * float(holdout_fraction))))
    else:
        size = int(holdout_draws)
    room = history_len - (int(min_draws) + 1) - MIN_VALIDATION_DRAWS
    size = min(size, room)
    return size if size >= MIN_HOLDOUT_DRAWS else 0


def random_hits_p_value(
    observed_total_hits: int,
    draws: list[Draw],
    rules: GameRules,
    replicates: int = HOLDOUT_P_VALUE_REPLICATES,
    seed: int = 7,
) -> float | None:
    """One-sided Monte Carlo p-value: P(random tickets hit >= observed).

    Each replicate scores one uniform random ticket (same generator semantics
    as ``random_baseline_metrics``) on every draw, so the null distribution
    matches the random baseline exactly for any game rules.
    """
    if not draws:
        return None
    pool = int(rules.primary_max) - int(rules.primary_min) + 1
    k = max(int(rules.primary_count), 1)
    if pool <= 0:
        return None
    rng = np.random.default_rng(seed)
    reps = max(1, int(replicates))
    totals = np.zeros(reps, dtype=np.int64)
    rows = np.arange(reps)[:, None]
    for draw in draws:
        mask = np.zeros(pool, dtype=bool)
        for number in set(draw.primary):
            offset = int(number) - int(rules.primary_min)
            if 0 <= offset < pool:
                mask[offset] = True
        if rules.primary_unique and k <= pool:
            picks = rng.random((reps, pool)).argsort(axis=1)[:, :k]
        else:
            picks = rng.integers(0, pool, size=(reps, k))
        onehot = np.zeros((reps, pool), dtype=bool)
        onehot[rows, picks] = True
        totals += (onehot & mask).sum(axis=1)
    exceed = int(np.sum(totals >= int(observed_total_hits)))
    return round((exceed + 1) / (reps + 1), 6)


def paired_permutation_p_value(
    challenger_hits: list[float],
    champion_hits: list[float],
    permutations: int = CHAMPION_PERMUTATIONS,
    seed: int = 11,
) -> float:
    """One-sided paired sign-flip permutation test that challenger > champion."""
    diffs = np.asarray(challenger_hits, dtype=float) - np.asarray(champion_hits, dtype=float)
    if diffs.size == 0 or np.allclose(diffs, 0.0):
        return 1.0
    observed = float(diffs.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(max(1, int(permutations)), diffs.size))
    permuted = (signs * diffs).mean(axis=1)
    exceed = int(np.sum(permuted >= observed - 1e-12))
    return round((exceed + 1) / (permuted.size + 1), 6)


def champion_challenger_decision(
    challenger_hits: list[float],
    champion_hits: list[float],
    comparisons: int = 1,
    alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
    primary_count: int = 1,
) -> dict:
    """Accept a challenger only when its validation gain beats noise.

    Paired sign-flip permutation test on per-draw hits over the same validation
    draws, with a Bonferroni-tightened threshold ``alpha / comparisons`` for the
    number of configurations tried.
    """
    n = min(len(challenger_hits), len(champion_hits))
    challenger = [float(v) for v in challenger_hits[:n]]
    champion = [float(v) for v in champion_hits[:n]]
    m = max(1, int(comparisons or 1))
    alpha_adjusted = float(alpha) / m
    per = max(int(primary_count), 1)
    challenger_mean = float(np.mean(challenger)) if n else 0.0
    champion_mean = float(np.mean(champion)) if n else 0.0
    diff = challenger_mean - champion_mean
    p_value = paired_permutation_p_value(challenger, champion) if n else 1.0
    accepted = bool(n > 0 and diff > 0 and p_value <= alpha_adjusted)
    return {
        "accepted": accepted,
        "p_value": p_value,
        "alpha": float(alpha),
        "alpha_adjusted": round(alpha_adjusted, 8),
        "comparisons": m,
        "method": "paired_sign_flip_permutation_bonferroni",
        "paired_draws": n,
        "challenger_mean_hits": round(challenger_mean, 4),
        "champion_mean_hits": round(champion_mean, 4),
        "mean_hit_diff": round(diff, 4),
        "challenger_validation_accuracy_percent": round(challenger_mean / per * 100, 2) if n else None,
        "champion_validation_accuracy_percent": round(champion_mean / per * 100, 2) if n else None,
    }


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
    holdout_draws: int | None = 0,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    max_holdout_draws: int = DEFAULT_MAX_HOLDOUT_DRAWS,
    before_holdout_fn: Callable[[dict], dict | None] | None = None,
    holdout_p_value_replicates: int = HOLDOUT_P_VALUE_REPLICATES,
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

    Anti-overfitting (``holdout_draws`` != 0): the history is split into a
    train prefix, a validation window, and a final untouched holdout (the most
    recent draws; ``None`` = auto ``min(max_holdout_draws, ~holdout_fraction)``).
    All round retries and the accuracy floor run on validation only.
    ``before_holdout_fn(summary)`` runs after validation (the caller makes its
    champion/challenger decision there), then the holdout is scored exactly once
    with frozen weights (no ``update_fn``). The headline ``accuracy_percent`` is
    then the holdout accuracy; the validation floor is
    ``validation_accuracy_percent`` and the max over retries is
    ``best_round_accuracy_percent`` (optimistic). ``holdout_draws=0`` keeps the
    legacy two-way split and marks the result with ``overfit_warning``.

    Returns honest metrics always shown alongside random baseline.
    """
    if len(history) < min_draws + 1:
        return {
            "status": "insufficient_data",
            "draws_available": len(history),
            "min_required": min_draws + 1,
        }

    holdout_size = resolve_holdout_size(
        len(history),
        min_draws=min_draws,
        holdout_draws=holdout_draws,
        holdout_fraction=holdout_fraction,
        max_holdout_draws=max_holdout_draws,
    )
    holdout_start = len(history) - holdout_size
    split_index = resolve_test_start(holdout_start, min_draws=min_draws, max_test_draws=max_test_draws)
    if split_index >= holdout_start:
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
    kept_round_hits: list[tuple[int, int]] = []

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
        round_indexed_hits: list[tuple[int, int]] = []
        for index in range(test_start, holdout_start):
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
            round_indexed_hits.append((index, hits))
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
                kept_round_hits = list(round_indexed_hits)
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

    # Validation is where every selection happened: the per-draw hits of the
    # kept round (the one that set the floor) are what a champion/challenger
    # test compares. The holdout below has not been looked at yet.
    validation_summary = {
        "validation_start": test_start,
        "holdout_start": holdout_start,
        "holdout_draws": holdout_size,
        "rounds_run": rounds_run,
        "validation_draw_indices": [index for index, _ in kept_round_hits],
        "validation_hits": [hits for _, hits in kept_round_hits],
        "validation_accuracy": accuracy_floor,
    }
    selection_extra: dict = {}
    if before_holdout_fn is not None:
        selection_extra = dict(before_holdout_fn(validation_summary) or {})

    holdout_hits: list[int] = []
    holdout_exact = 0
    holdout_sum_errors: list[float] = []
    holdout_history: list[dict] = []
    holdout_scored_draws: list[Draw] = []
    for index in range(holdout_start, len(history)):
        actual = history[index]
        try:
            ticket = predict_fn(history[:index])
            predicted = ticket.primary if isinstance(ticket, PredictionTicket) else ticket
        except Exception:
            continue
        hits = _partial_hits(predicted, actual.primary)
        holdout_hits.append(hits)
        holdout_scored_draws.append(actual)
        if _exact_match(predicted, actual.primary):
            holdout_exact += 1
        holdout_sum_errors.append(abs(sum(predicted) - sum(actual.primary)))
        holdout_history.append({
            "phase": "holdout",
            "draw_index": index,
            "draw_id": actual.draw_id,
            "draw_date": actual.draw_date.isoformat() if actual.draw_date else None,
            "predicted_numbers": list(predicted),
            "actual_numbers": list(actual.primary),
            "partial_hits": hits,
            "accuracy_percent": round(hits / primary_count * 100, 2),
        })

    validation_mean_hits = float(np.mean(partial_hits))
    validation_baseline = random_baseline_metrics(history[test_start:holdout_start], rules)
    validation_baseline_hits = validation_baseline.get("mean_partial_hits", 0.0) or 0.001
    aggregate_accuracy = validation_mean_hits / primary_count
    validation_accuracy = (
        accuracy_floor if accuracy_floor is not None else (latest_round_accuracy or aggregate_accuracy)
    )
    validation_metrics = {
        "mean_partial_hits": round(validation_mean_hits, 4),
        "random_baseline": validation_baseline,
        "lift_vs_random": round(validation_mean_hits / validation_baseline_hits, 4),
        "exact_match_rate": round(exact_matches / evaluated, 6),
        "partial_hit_rate_at_1": round(sum(1 for h in partial_hits if h >= 1) / evaluated, 4),
        "partial_hit_rate_at_2": round(sum(1 for h in partial_hits if h >= 2) / evaluated, 4),
        "sum_mae": round(float(np.mean(sum_errors)), 4),
        "accuracy_percent": round(validation_accuracy * 100, 2),
        "overall_accuracy_percent": round(aggregate_accuracy * 100, 2),
    }

    holdout_ok = bool(holdout_hits)
    warning_reasons: list[str] = []
    if holdout_ok:
        holdout_n = len(holdout_hits)
        holdout_mean_hits = float(np.mean(holdout_hits))
        holdout_accuracy = holdout_mean_hits / primary_count
        baseline = random_baseline_metrics(holdout_scored_draws, rules)
        baseline_hits = baseline.get("mean_partial_hits", 0.0) or 0.001
        holdout_lift = round(holdout_mean_hits / baseline_hits, 4)
        holdout_p = random_hits_p_value(
            int(sum(holdout_hits)), holdout_scored_draws, rules, replicates=holdout_p_value_replicates
        )
        headline_accuracy = holdout_accuracy
        mean_hits = holdout_mean_hits
        exact_rate = round(holdout_exact / holdout_n, 6)
        hit_at_1 = round(sum(1 for h in holdout_hits if h >= 1) / holdout_n, 4)
        hit_at_2 = round(sum(1 for h in holdout_hits if h >= 2) / holdout_n, 4)
        sum_mae = round(float(np.mean(holdout_sum_errors)), 4)
        overfit_gap = round(round(validation_accuracy * 100, 2) - round(holdout_accuracy * 100, 2), 2)
        if overfit_gap >= OVERFIT_GAP_WARN_PERCENT:
            warning_reasons.append("validation_beats_holdout")
        if holdout_p is None or holdout_p >= DEFAULT_SIGNIFICANCE_ALPHA:
            warning_reasons.append("holdout_not_significant_vs_random")
        accuracy_source = "holdout"
    else:
        # Legacy two-way split (or too little data for a holdout): the number
        # below was selected on the same draws it is scored on.
        holdout_accuracy = None
        holdout_mean_hits = None
        baseline = validation_baseline
        holdout_lift = None
        holdout_p = None
        headline_accuracy = validation_accuracy
        mean_hits = validation_mean_hits
        exact_rate = validation_metrics["exact_match_rate"]
        hit_at_1 = validation_metrics["partial_hit_rate_at_1"]
        hit_at_2 = validation_metrics["partial_hit_rate_at_2"]
        sum_mae = validation_metrics["sum_mae"]
        overfit_gap = None
        warning_reasons.append("no_holdout")
        accuracy_source = "validation"
    baseline_hits = baseline.get("mean_partial_hits", 0.0) or 0.001
    honest_target_reached = headline_accuracy >= float(target_accuracy)

    last_round = round_metrics[-1] if round_metrics else {}
    return {
        "status": "ok",
        "split": {
            "training_draws": split_index,
            "test_draws": holdout_start - split_index,
            "training_fraction": round(split_index / len(history), 4),
            "test_fraction": round((holdout_start - split_index) / len(history), 4),
            "max_test_draws": int(max_test_draws) if max_test_draws else None,
            "validation_draws": holdout_start - split_index,
            "validation_start": split_index,
            "holdout_draws": holdout_size,
            "holdout_start": holdout_start,
            "holdout_fraction": round(holdout_size / len(history), 4),
        },
        "evaluated_draws": evaluated,
        "mean_partial_hits": round(mean_hits, 4),
        "accuracy": round(headline_accuracy, 6),
        "accuracy_percent": round(headline_accuracy * 100, 2),
        "held_accuracy_percent": round(headline_accuracy * 100, 2),
        "this_run_accuracy_percent": last_round.get("accuracy_percent"),
        "next_run_target_percent": round(validation_accuracy * 100, 2),
        "rolling_accuracy": round(previous_rolling_percent / 100, 6) if previous_rolling_percent is not None else round(validation_accuracy, 6),
        "rolling_accuracy_percent": previous_rolling_percent if previous_rolling_percent is not None else round(validation_accuracy * 100, 2),
        "highest_rolling_accuracy_percent": (
            round(best_rolling_accuracy * 100, 2) if best_rolling_accuracy is not None else None
        ),
        "rolling_window": rolling_window,
        "overall_accuracy": round(aggregate_accuracy, 6),
        "overall_accuracy_percent": round(aggregate_accuracy * 100, 2),
        "target_accuracy": float(target_accuracy),
        "target_accuracy_percent": round(float(target_accuracy) * 100, 2),
        "target_reached": honest_target_reached if holdout_ok else target_reached,
        "validation_target_reached": target_reached,
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
        "best_round_accuracy_is_optimistic": True,
        "best_round_accuracy_label": "optimistic: max over validation retries",
        "round_metrics": round_metrics,
        "regression_count": regression_count,
        "promotion_count": promotion_count,
        "exact_match_rate": exact_rate,
        "partial_hit_rate_at_1": hit_at_1,
        "partial_hit_rate_at_2": hit_at_2,
        "sum_mae": sum_mae,
        "random_baseline": baseline,
        "lift_vs_random": round(mean_hits / baseline_hits, 4),
        # --- honest out-of-sample reporting ---
        "accuracy_source": accuracy_source,
        "holdout_accuracy_percent": round(holdout_accuracy * 100, 2) if holdout_accuracy is not None else None,
        "holdout_mean_partial_hits": round(holdout_mean_hits, 4) if holdout_mean_hits is not None else None,
        "holdout_evaluated_draws": len(holdout_hits),
        "holdout_random_baseline": baseline if holdout_ok else None,
        "holdout_lift_vs_random": holdout_lift,
        "holdout_p_value": holdout_p,
        "holdout_p_value_method": "monte_carlo_random_tickets_one_sided" if holdout_ok else None,
        "holdout_history": holdout_history,
        "validation_accuracy_percent": round(validation_accuracy * 100, 2),
        "validation_metrics": validation_metrics,
        "overfit_gap_percent": overfit_gap,
        "trials_tested": rounds_run,
        "overfit_warning": bool(warning_reasons),
        "overfit_warning_reasons": warning_reasons,
        **selection_extra,
        "note": (
            "Accuracy is partial-hit rate on the final untouched holdout (most recent draws), "
            "scored once after all tuning on the validation window. Exact-match rates are normally "
            "near zero for lottery draws."
            if holdout_ok else
            "Accuracy is partial-hit rate. Exact-match rates are normally near zero for lottery draws. "
            "No holdout was available, so this number was selected on the draws it is scored on."
        ),
    }


def score_plugin_on_draw(
    plugin_picks: list[int],
    actual: Draw,
    rules: GameRules,
) -> float:
    """Partial hit rate for one plugin's picks vs actual draw (0.0–1.0)."""
    return _partial_hits(plugin_picks, actual.primary) / max(rules.primary_count, 1)