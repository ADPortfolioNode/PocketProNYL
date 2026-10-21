"""Sub-target chain: predict the draw's odd/even count and sum band, then build a ticket.

Both class predictions are Dirichlet-multinomial posteriors whose prior is the
exact chance distribution, so with fair draws they settle on the most common
class (e.g. 2 or 3 odd numbers in Take 5, the middle sum band). The ticket is
the highest-scoring combination of the top candidates that matches both
predicted classes, relaxing the sum band and then the odd count if nothing fits.
"""

from __future__ import annotations

from itertools import combinations
from math import comb

import numpy as np

from prediction.core.types import Draw, GameRules

MAX_TICKET_FOR_SEARCH = 8
PRIOR_STRENGTH = 20.0


def odd_count(numbers: list[int]) -> int:
    return sum(1 for n in numbers if int(n) % 2 == 1)


def odd_count_chance(rules: GameRules) -> list[float]:
    k = rules.primary_count
    numbers = range(rules.primary_min, rules.primary_max + 1)
    odd = sum(1 for n in numbers if n % 2 == 1)
    size = rules.primary_max - rules.primary_min + 1
    if rules.primary_unique:
        return [comb(odd, j) * comb(size - odd, k - j) / comb(size, k) for j in range(k + 1)]
    p = odd / size
    return [comb(k, j) * p ** j * (1 - p) ** (k - j) for j in range(k + 1)]


def sum_band_edges(prefix: list[Draw]) -> tuple[float, float] | None:
    sums = [sum(d.primary) for d in prefix if d.primary]
    if len(sums) < 9:
        return None
    low, high = np.quantile(sums, [1 / 3, 2 / 3])
    return float(low), float(high)


def sum_band(total: float, edges: tuple[float, float]) -> int:
    if total <= edges[0]:
        return 0
    if total <= edges[1]:
        return 1
    return 2


def _posterior_mode(observed: list[int], prior: list[float]) -> int:
    alpha = [PRIOR_STRENGTH * p for p in prior]
    for value in observed:
        if 0 <= value < len(alpha):
            alpha[value] += 1.0
    return int(max(range(len(alpha)), key=lambda i: (alpha[i], -i)))


def predict_classes(prefix: list[Draw], rules: GameRules, window: int = 120) -> dict:
    recent = prefix[-int(window):]
    odd = _posterior_mode([odd_count(d.primary) for d in recent], odd_count_chance(rules))
    edges = sum_band_edges(prefix)
    band = None
    if edges is not None:
        band = _posterior_mode([sum_band(sum(d.primary), edges) for d in recent], [1 / 3, 1 / 3, 1 / 3])
    return {"odd_count": odd, "sum_band": band, "sum_edges": edges}


def constrained_ticket(ranked_numbers: list[int], scores: dict[int, float], rules: GameRules,
                       classes: dict, top_m: int | None = None) -> tuple[list[int], str]:
    k = rules.primary_count
    if k > MAX_TICKET_FOR_SEARCH:
        return ranked_numbers[:k], "skipped_ticket_too_large"
    top_m = top_m or min(len(ranked_numbers), k + 5)
    pool = ranked_numbers[:top_m]
    edges = classes.get("sum_edges")
    target_odd = classes.get("odd_count")
    target_band = classes.get("sum_band")

    def best(require_band: bool, require_odd: bool):
        chosen, chosen_score = None, None
        for combo in combinations(pool, k):
            if require_odd and target_odd is not None and odd_count(combo) != target_odd:
                continue
            if require_band and edges is not None and target_band is not None:
                if sum_band(sum(combo), edges) != target_band:
                    continue
            score = sum(scores.get(n, 0.0) for n in combo)
            if chosen_score is None or score > chosen_score + 1e-12:
                chosen, chosen_score = combo, score
        return chosen

    attempts = ((True, True, "odd_and_sum"), (False, True, "odd_only"), (False, False, "unconstrained"))
    for require_band, require_odd, label in attempts:
        combo = best(require_band, require_odd)
        if combo is not None:
            ordered = [n for n in ranked_numbers if n in combo]
            return ordered, label
    return ranked_numbers[:k], "unconstrained"
