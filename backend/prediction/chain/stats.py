"""Statistics for the chained ensemble: uniformity, per-number tests, BH-FDR.

Pure Python / numpy (no scipy). The permutation and Monte Carlo tests reuse
the anti-overfit machinery in ``prediction.metrics.evaluator`` (PR #13).
"""

from __future__ import annotations

from collections import Counter
from math import exp, lgamma, log

from prediction.core.types import Draw, GameRules
from services.accuracy_stats import binomial_two_sided


def _gamma_series(a: float, x: float) -> float:
    total = 1.0 / a
    term = total
    n = a
    for _ in range(1000):
        n += 1
        term *= x / n
        total += term
        if abs(term) < abs(total) * 1e-14:
            break
    return total * exp(-x + a * log(x) - lgamma(a))


def _gamma_continued_fraction(a: float, x: float) -> float:
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return exp(-x + a * log(x) - lgamma(a)) * h


def regularized_upper_gamma(a: float, x: float) -> float:
    """Q(a, x) = 1 - P(a, x), the chi-square survival function building block."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        return max(0.0, min(1.0, 1.0 - _gamma_series(a, x)))
    return max(0.0, min(1.0, _gamma_continued_fraction(a, x)))


def chi_square_sf(statistic: float, dof: int) -> float:
    return regularized_upper_gamma(dof / 2.0, statistic / 2.0)


def chi_square_uniformity(history: list[Draw], rules: GameRules) -> dict:
    """Are the numbers drawn uniformly? (Pearson chi-square on ball counts.)

    Unique-number games: counts of each ball over all draws (expected n*k/N each;
    the without-replacement dependence makes the test slightly conservative).
    Digit games: counts of each digit over all positions (exact multinomial).
    """
    size = rules.primary_max - rules.primary_min + 1
    counts = Counter()
    total = 0
    for draw in history:
        for number in draw.primary:
            if rules.primary_min <= number <= rules.primary_max:
                counts[number] += 1
                total += 1
    if total == 0 or size < 2:
        return {"status": "insufficient_data"}
    expected = total / size
    statistic = sum((counts.get(rules.primary_min + i, 0) - expected) ** 2 / expected for i in range(size))
    dof = size - 1
    p_value = chi_square_sf(statistic, dof)
    return {
        "status": "ok",
        "draws": len(history),
        "balls_counted": total,
        "chi_square": round(statistic, 4),
        "dof": dof,
        "p_value": round(p_value, 6),
        "uniform_at_0_05": p_value >= 0.05,
        "interpretation": (
            "No evidence the draws are non-uniform." if p_value >= 0.05
            else "Ball counts deviate from uniform more than chance usually allows; check data quality "
                 "(duplicates, mixed games, format changes) before reading anything into it."
        ),
    }


def benjamini_hochberg(p_values: list[float], q: float = 0.05) -> tuple[list[float], list[bool]]:
    """BH-FDR adjusted p-values (q-values) and rejections at level ``q``."""
    m = len(p_values)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * m / rank)
        adjusted[index] = min(1.0, running)
    return adjusted, [a <= q for a in adjusted]


def number_frequency_tests(history: list[Draw], rules: GameRules, q: float = 0.05) -> dict:
    """Per-number exact binomial test of "drawn more/less often than chance", BH-FDR corrected."""
    n = len(history)
    if n == 0:
        return {"status": "insufficient_data"}
    size = rules.primary_max - rules.primary_min + 1
    if rules.primary_unique:
        p0 = rules.primary_count / size
    else:
        p0 = 1.0 - (1.0 - 1.0 / size) ** rules.primary_count
    appearances = Counter()
    for draw in history:
        for number in set(draw.primary):
            appearances[number] += 1
    numbers = list(range(rules.primary_min, rules.primary_max + 1))
    raw = [binomial_two_sided(appearances.get(num, 0), n, p0) for num in numbers]
    adjusted, rejected = benjamini_hochberg(raw, q)
    flagged = [
        {"number": num, "appearances": appearances.get(num, 0), "expected": round(n * p0, 2),
         "q_value": round(adjusted[i], 6)}
        for i, num in enumerate(numbers) if rejected[i]
    ]
    return {
        "status": "ok",
        "draws": n,
        "tests": len(numbers),
        "fdr_level": q,
        "min_raw_p_value": round(min(raw), 6),
        "significant_after_fdr": flagged,
        "interpretation": (
            "No number is drawn more or less often than chance after FDR correction."
            if not flagged else
            f"{len(flagged)} number(s) deviate after FDR correction; with fair machines this is most "
            "likely a data artefact, and past frequency still does not predict the next draw."
        ),
    }
