"""Weighted ensemble blending of strategy and agent outputs."""

from __future__ import annotations

import numpy as np

from prediction.core.types import GameRules, PredictionTicket, StrategyOutput


def _normalize_scores(scores: list[float]) -> np.ndarray:
    arr = np.asarray(scores, dtype=float)
    if arr.size == 0:
        return arr
    arr = np.maximum(arr, 0.0)
    total = float(arr.sum())
    if total <= 0:
        return np.ones_like(arr) / len(arr)
    return arr / total


def _top_k_from_distribution(
    dist: np.ndarray,
    rules: GameRules,
    count: int,
) -> list[int]:
    """Select top numbers from probability distribution respecting uniqueness."""
    numbers = list(range(rules.primary_min, rules.primary_max + 1))
    if len(numbers) != len(dist):
        # Pad or trim distribution to match universe
        size = rules.primary_universe_size
        if len(dist) < size:
            dist = np.pad(dist, (0, size - len(dist)))
        dist = dist[:size]

    ranked = sorted(
        zip(numbers, dist),
        key=lambda item: item[1],
        reverse=True,
    )

    picks: list[int] = []
    for number, _score in ranked:
        if rules.primary_unique and number in picks:
            continue
        picks.append(number)
        if len(picks) >= count:
            break

    while len(picks) < count:
        candidate = rules.primary_min
        while candidate in picks and candidate <= rules.primary_max:
            candidate += 1
        picks.append(min(candidate, rules.primary_max))

    return picks[:count]


def blend_outputs(
    outputs: list[StrategyOutput],
    weights: dict[str, float],
    rules: GameRules,
    nn_scores: list[float] | None = None,
    nn_weight: float = 0.0,
) -> tuple[list[int], list[int], dict[str, list[int]], dict[str, float]]:
    """
    Blend strategy/agent score vectors into primary and bonus picks.

    Returns: primary picks, bonus picks, per-plugin top picks, weights used.
    """
    size = rules.primary_universe_size
    blended = np.zeros(size, dtype=float)
    used_weights: dict[str, float] = {}
    contributions: dict[str, list[int]] = {}

    active_total = 0.0
    for output in outputs:
        weight = float(weights.get(output.name, 0.0))
        if weight <= 0:
            continue
        dist = _normalize_scores(output.scores[:size] if output.scores else [])
        if dist.size == 0:
            continue
        blended += weight * dist
        active_total += weight
        used_weights[output.name] = weight
        contributions[output.name] = output.picks or _top_k_from_distribution(
            dist, rules, rules.primary_count
        )

    if nn_scores and nn_weight > 0:
        nn_dist = _normalize_scores(nn_scores[:size])
        if nn_dist.size:
            blended += nn_weight * nn_dist
            active_total += nn_weight
            used_weights["nn"] = nn_weight
            contributions["nn"] = _top_k_from_distribution(nn_dist, rules, rules.primary_count)

    if active_total > 0:
        blended /= active_total

    primary = _top_k_from_distribution(blended, rules, rules.primary_count)

    bonus: list[int] = []
    if rules.bonus_count > 0:
        bonus_size = (rules.bonus_max - rules.bonus_min) + 1
        bonus_blended = np.zeros(bonus_size, dtype=float)
        bonus_active = 0.0
        for output in outputs:
            weight = float(weights.get(output.name, 0.0))
            if weight <= 0 or not output.bonus_scores:
                continue
            bdist = _normalize_scores(output.bonus_scores[:bonus_size])
            if bdist.size:
                bonus_blended += weight * bdist
                bonus_active += weight
        if bonus_active > 0:
            bonus_blended /= bonus_active
            bonus_numbers = list(range(rules.bonus_min, rules.bonus_max + 1))
            ranked_bonus = sorted(
                zip(bonus_numbers, bonus_blended),
                key=lambda x: x[1],
                reverse=True,
            )
            bonus = [n for n, _ in ranked_bonus[: rules.bonus_count]]

    return primary, bonus, contributions, used_weights


def build_ticket(
    game: str,
    outputs: list[StrategyOutput],
    weights: dict[str, float],
    rules: GameRules,
    nn_scores: list[float] | None = None,
    nn_weight: float = 0.0,
    metrics: dict | None = None,
) -> PredictionTicket:
    primary, bonus, contributions, used = blend_outputs(
        outputs, weights, rules, nn_scores=nn_scores, nn_weight=nn_weight
    )
    return PredictionTicket(
        game=game,
        primary=primary,
        bonus=bonus,
        strategy_contributions=contributions,
        weights_used=used,
        metrics=metrics or {},
        strategy_outputs=list(outputs),
    )