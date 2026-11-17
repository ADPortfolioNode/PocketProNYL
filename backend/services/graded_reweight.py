"""Reweight ensemble strategies from graded served suggestions.

Small, noted, accumulated increments only. Every move is capped, shrunk
toward the current weights by how much graded evidence exists, and gated:

- at least MIN_GRADED unique graded tickets for the game, or the weights
  do not move at all (with 3 graded suggestions any move is noise);
- at least one strategy with a significant per-strategy lift (exact
  upper tail p < 0.05) and a material lift (outside 0.8..1.2), or hold;
- the candidate is a blend, never a jump: candidate = (1-a) * current +
  a * target with a = MIX_CAP * n / (n + SHRINKAGE_K), and no strategy
  ever drops below MIN_WEIGHT, so exploration survives;
- the move is recorded in the WeightStore history with its evidence
  (previous weights kept in the entry, so a bad move can be reverted by
  hand from the stored record).

Only ensemble tickets count for strategy credit: chained-ensemble tickets
keep the model name "chained_ensemble", and their recorded weights describe
the ensemble blend, not the chained pick that was actually served, so
crediting them would move the wrong weights. They still count for
ticket-level accuracy, just not for strategy credit.

Per-strategy credit comes from the ledger's strategy_contributions (the
numbers each strategy contributed to the served ticket), not from the
ticket as a whole: a strategy earns hits only for its own numbers.
Expected hits for k contributed numbers are k * primary_count / universe
(random baseline); the p-value is EXACT, not binomial: each entry's
strategy hits follow a hypergeometric distribution (k distinct numbers
drawn without replacement from a drum with primary_count winners), and
the total across entries is the convolution of those per-entry
distributions. The old binomial tail treated every picked number as an
independent coin flip, overstating the variance and hiding small real
edges — the exact test is what lets this loop detect the subtle.

This module never touches backtest holdout metrics: the overfit gate owns
promotion on backtest data, and this loop owns promotion on live served
data. A move here is deliberately small, so a wrong move costs at most
one capped increment, and the next graded batch can move it back.
"""

from __future__ import annotations

import math
import time

from prediction.config.loader import game_rules_from_config

MIN_GRADED = 30
MIN_STRATEGY_ENTRIES = 5
MIX_CAP = 0.25
SHRINKAGE_K = 30
MIN_WEIGHT = 0.01
LIFT_LOW = 0.8
LIFT_HIGH = 1.2
P_THRESHOLD = 0.05
LIFT_CLIP = (0.5, 2.0)


def dedupe_graded(graded: list[dict]) -> list[dict]:
    """Unique graded tickets: same draw + same numbers count once."""
    seen: set = set()
    unique: list[dict] = []
    for item in graded or []:
        if item.get("status") != "graded":
            continue
        key = (item.get("actual_draw_date"), item.get("actual_draw_session"),
               tuple(item.get("numbers") or []))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _binomial_upper_tail(trials: int, p: float, at_least: int) -> float:
    """Binomial upper tail. Kept as a utility; strategy credit no longer
    uses it (see _exact_upper_tail above)."""
    if trials <= 0 or at_least <= 0:
        return 1.0
    if at_least > trials:
        return 0.0
    total = 0.0
    for k in range(at_least, trials + 1):
        total += math.comb(trials, k) * (p ** k) * ((1.0 - p) ** (trials - k))
    return min(max(total, 0.0), 1.0)


def _hypergeometric_pmf(universe: int, winners: int, picks: int) -> list[float]:
    """P(X = k) for k distinct picks from a drum with `winners` winners."""
    if picks < 0 or winners < 0 or universe <= 0 or picks > universe:
        return []
    total = math.comb(universe, picks)
    pmf = []
    for k in range(min(picks, winners) + 1):
        if picks - k > universe - winners:
            pmf.append(0.0)
        else:
            pmf.append(math.comb(winners, k) * math.comb(universe - winners, picks - k) / total)
    return pmf


def _exact_upper_tail(per_entry: list[tuple[int, int, int]], at_least: int) -> float:
    """Exact P(total hits >= at_least) for independent per-entry draws.

    Each entry contributes one hypergeometric draw (universe, winners,
    picks); the total is their convolution. This replaces the binomial
    tail, which wrongly treated picks within one ticket as independent
    coin flips and overstated the variance.
    """
    if at_least <= 0:
        return 1.0
    dist = [1.0]
    for universe, winners, picks in per_entry:
        pmf = _hypergeometric_pmf(universe, winners, picks)
        if not pmf:
            return 1.0
        new = [0.0] * (len(dist) + len(pmf) - 1)
        for i, p in enumerate(dist):
            if p == 0.0:
                continue
            for j, q in enumerate(pmf):
                new[i + j] += p * q
        dist = new
    if at_least >= len(dist):
        return 0.0
    return min(max(sum(dist[at_least:]), 0.0), 1.0)


def strategy_credit(graded: list[dict], game: str) -> dict[str, dict]:
    """Per-strategy hits vs chance, from ledger contributions only."""
    rules = game_rules_from_config(game)
    universe = max(rules.primary_max - rules.primary_min + 1, 1)
    winners = rules.primary_count
    p_hit = winners / universe
    credit: dict[str, dict] = {}
    draws: dict[str, list[tuple[int, int, int]]] = {}
    for item in dedupe_graded(graded):
        if item.get("model") == "chained_ensemble":
            continue
        actual = set(item.get("actual_numbers") or [])
        contributions = item.get("strategy_contributions") or {}
        if not isinstance(contributions, dict):
            continue
        for name, numbers in contributions.items():
            try:
                picks = [int(n) for n in (numbers or [])]
            except (TypeError, ValueError):
                continue
            if not picks:
                continue
            row = credit.setdefault(str(name), {"entries": 0, "numbers": 0, "hits": 0})
            row["entries"] += 1
            row["numbers"] += len(picks)
            row["hits"] += len(set(picks) & actual)
            draws.setdefault(str(name), []).append((universe, winners, len(picks)))
    for name, row in credit.items():
        expected = row["numbers"] * p_hit
        row["expected_hits"] = round(expected, 4)
        row["lift"] = round(row["hits"] / expected, 4) if expected > 0 else None
        row["p_value"] = round(
            _exact_upper_tail(draws.get(name, []), row["hits"]), 6)
        row["p_value_method"] = "exact convolution of per-entry hypergeometrics"
    return credit


def _normalize(weights: dict[str, float], min_weight: float = MIN_WEIGHT) -> dict[str, float]:
    clipped = {name: max(float(min_weight), float(value))
               for name, value in weights.items() if value is not None}
    total = sum(clipped.values())
    if total <= 0:
        n = max(len(clipped), 1)
        return {name: 1.0 / n for name in clipped}
    return {name: round(value / total, 6) for name, value in clipped.items()}


def propose_reweight(game: str, graded: list[dict], current_weights: dict,
                     *, min_weight: float = MIN_WEIGHT) -> dict:
    """Candidate weights from graded evidence, or a hold verdict with reasons."""
    unique = dedupe_graded(graded)
    n = len(unique)
    credit = strategy_credit(graded, game)
    base = {
        "game": game,
        "verdict": "hold",
        "n_graded_unique": n,
        "min_graded": MIN_GRADED,
        "strategies": credit,
        "current_weights": dict(current_weights or {}),
        "candidate_weights": None,
        "alpha": 0.0,
        "reasons": [],
    }
    if n < MIN_GRADED:
        base["reasons"].append(f"only {n} unique graded tickets; need {MIN_GRADED}")
        return base
    current = _normalize(dict(current_weights or {}), min_weight)
    if not current:
        base["reasons"].append("no current weights to move from")
        return base
    significant = {
        name: row for name, row in credit.items()
        if row["entries"] >= MIN_STRATEGY_ENTRIES
        and row["p_value"] is not None and row["p_value"] < P_THRESHOLD
        and row["lift"] is not None and (row["lift"] >= LIFT_HIGH or row["lift"] <= LIFT_LOW)
    }
    if not significant:
        base["reasons"].append("no strategy with a significant material lift")
        return base
    target: dict[str, float] = {}
    for name in set(current) | set(credit):
        row = credit.get(name)
        lift = row["lift"] if row else 1.0
        if lift is None:
            lift = 1.0
        target[name] = min(max(lift, LIFT_CLIP[0]), LIFT_CLIP[1])
    target = _normalize(target, min_weight)
    alpha = MIX_CAP * n / (n + SHRINKAGE_K)
    candidate = _normalize(
        {name: (1.0 - alpha) * current.get(name, min_weight) + alpha * target.get(name, min_weight)
         for name in set(current) | set(target)}, min_weight)
    max_move = max((abs(candidate.get(k, 0.0) - current.get(k, 0.0)) for k in candidate), default=0.0)
    if max_move < 0.01:
        base["reasons"].append("candidate move is negligible (<0.01)")
        return base
    base["verdict"] = "promote"
    base["candidate_weights"] = candidate
    base["alpha"] = round(alpha, 4)
    base["significant_strategies"] = sorted(significant)
    return base


def apply_reweight(game: str, graded: list[dict], *, store=None) -> dict:
    """Apply a proposed move only when every gate passes; else hold untouched.

    Returns the proposal plus ``applied``. On promote, the previous weights
    are stored inside the history entry, so the move is reversible from the
    stored record alone.
    """
    from prediction.state.weight_store import WeightStore

    store = store or WeightStore()
    state = store.load(game)
    proposal = propose_reweight(game, graded, state.weights)
    proposal["applied"] = False
    if proposal["verdict"] != "promote" or not proposal.get("candidate_weights"):
        return proposal
    previous = dict(state.weights or {})
    state.weights = dict(proposal["candidate_weights"])
    state.updated_at = time.time()
    state.history.append({
        "timestamp": state.updated_at,
        "source": "graded_ledger",
        "n_graded_unique": proposal["n_graded_unique"],
        "alpha": proposal["alpha"],
        "previous_weights": previous,
        "candidate_weights": dict(proposal["candidate_weights"]),
        "strategy_lifts": {name: row["lift"] for name, row in proposal["strategies"].items()},
        "promoted": True,
    })
    store.save(state)
    proposal["applied"] = True
    return proposal
