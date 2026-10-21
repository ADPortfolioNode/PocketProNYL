"""Exact chance baselines and p-values for graded suggestions.

Hits are counted the same way the backtest counts them (``len(set(ticket) &
set(draw))``), so the real and simulated numbers are comparable.

* Unique-number games (Take 5, Lotto, Powerball whites, Pick 10, ...): the
  number of hits of a ``t``-number ticket against ``d`` numbers drawn from
  ``N`` is hypergeometric.
* Digit games (Pick 3 / Numbers, Win 4): digits are drawn with replacement, so
  the distribution depends on how many distinct digits the ticket holds; it is
  computed exactly by enumerating every possible draw (at most 10^4).

All p-values are one-sided ("did the suggestions hit at least this much more
than chance?") and exact; nothing is simulated.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import product
from math import comb, exp, lgamma, log

from prediction.core.types import GameRules


def hypergeom_distribution(pool: int, drawn: int, picked: int) -> list[float]:
    """pmf[h] = P(h of ``picked`` ticket numbers are among ``drawn`` of ``pool``)."""
    total = comb(pool, drawn)
    pmf = []
    for h in range(0, min(picked, drawn) + 1):
        if picked - h > pool - drawn:
            pmf.append(0.0)
            continue
        pmf.append(comb(picked, h) * comb(pool - picked, drawn - h) / total)
    return pmf


@lru_cache(maxsize=64)
def _with_replacement_distribution(pool: int, draws: int, distinct: int) -> tuple[float, ...]:
    """Exact pmf of |set(ticket) & set(draw)| when ``draws`` values are i.i.d. uniform."""
    counts = [0] * (distinct + 1)
    ticket = set(range(distinct))
    for outcome in product(range(pool), repeat=draws):
        counts[len(ticket & set(outcome))] += 1
    total = pool ** draws
    return tuple(c / total for c in counts)


def hit_distribution(ticket: list[int], rules: GameRules) -> list[float]:
    """Chance distribution of hits for this ticket under a fair draw."""
    pool = int(rules.primary_max) - int(rules.primary_min) + 1
    drawn = int(rules.primary_count)
    distinct = len(set(int(n) for n in ticket))
    if rules.primary_unique:
        return hypergeom_distribution(pool, drawn, distinct)
    if pool ** drawn > 200_000:
        raise ValueError("draw space too large for exact enumeration")
    return list(_with_replacement_distribution(pool, drawn, distinct))


def expected_hits(pmf: list[float]) -> float:
    return float(sum(h * p for h, p in enumerate(pmf)))


def upper_tail(pmf: list[float], observed: int) -> float:
    """P(X >= observed)."""
    observed = max(0, int(observed))
    return float(min(1.0, sum(pmf[observed:])))


def convolve(a: list[float], b: list[float]) -> list[float]:
    out = [0.0] * (len(a) + len(b) - 1)
    for i, pa in enumerate(a):
        if pa == 0:
            continue
        for j, pb in enumerate(b):
            out[i + j] += pa * pb
    return out


def pooled_total_p_value(pmfs: list[list[float]], observed_total: int) -> float | None:
    """Exact P(sum of independent per-suggestion hits >= observed total)."""
    if not pmfs:
        return None
    dist = [1.0]
    for pmf in pmfs:
        dist = convolve(dist, pmf)
    return upper_tail(dist, observed_total)


def binomial_upper_tail(k: int, n: int, p: float) -> float:
    """Exact P(Binomial(n, p) >= k), computed in log space."""
    if n <= 0 or k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    log_p, log_q = log(p), log(1 - p)
    terms = []
    for i in range(k, n + 1):
        terms.append(lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1) + i * log_p + (n - i) * log_q)
    peak = max(terms)
    return float(min(1.0, exp(peak) * sum(exp(t - peak) for t in terms)))


def binomial_two_sided(k: int, n: int, p: float) -> float:
    """Two-sided exact binomial p-value (sum of outcomes no more likely than k)."""
    if n <= 0:
        return 1.0

    def logpmf(i: int) -> float:
        if p <= 0:
            return 0.0 if i == 0 else float("-inf")
        if p >= 1:
            return 0.0 if i == n else float("-inf")
        return lgamma(n + 1) - lgamma(i + 1) - lgamma(n - i + 1) + i * log(p) + (n - i) * log(1 - p)

    target = logpmf(k)
    total = 0.0
    for i in range(n + 1):
        lp = logpmf(i)
        if lp <= target + 1e-9:
            total += exp(lp)
    return float(min(1.0, total))
