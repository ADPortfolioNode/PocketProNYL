"""Pattern search over one game's own draws.

Working assumption (Deo, Oct 2026): draws may carry exploitable structure, so we
look for it. The guard decides what is real:

* Every signal predicts draw ``i`` from ``history[:i]`` only (walk-forward).
* It is scored on the validation window against the chance baseline: the
  hypergeometric expected hits for unique-ball games, ``k/10`` positional hits
  for digit games, and the null probability of the predicted class for
  sum/odd-even/high-low/bonus signals.
* The p-value is a ball-relabel permutation test (Monte Carlo): under fair draws
  any relabelling of the ball universe is equally likely, so the null total
  hits are sampled exactly (hypergeometric / binomial / Bernoulli).
* Benjamini-Hochberg FDR across all signals tested for the game.
* A signal that passes validation is scored ONCE on the untouched holdout. Only
  ``passed_holdout`` signals are labelled as real; nothing is promoted on
  backtest fit.

Games are strictly isolated: a ``PatternMatrix`` is built from one game's draw
list and the game's own rules, nothing else.
"""

from __future__ import annotations

import time
from collections import Counter

import numpy as np

from prediction.chain.stats import benjamini_hochberg
from prediction.core.types import Draw, GameRules
from prediction.metrics.evaluator import DEFAULT_SIGNIFICANCE_ALPHA, three_way_split

NULL_REPLICATES = 2000
CLASS_WINDOW = 60
POSITIONAL_WINDOW = 200
PAIR_LOOKBACK = 400
DOW_WINDOW = 200
BONUS_WINDOW = 100
MIN_PRIOR = 20
NULL_SIMULATIONS = 20000
SUM_BANDS = 5
# Signals are cheap, so they get a longer validation window than the chain's
# config default (more power); the untouched holdout is the same either way.
PATTERN_VALIDATION_DRAWS = 600

NUMBER_SIGNALS = (
    "hot_w20", "hot_w50", "hot_w200", "cold_w50", "gap_overdue", "positional",
    "pair_cooccurrence", "triplet_cooccurrence", "repeat_last", "day_of_week",
)
CLASS_SIGNALS = ("sum_band", "odd_even", "high_low")
BONUS_SIGNALS = ("bonus_frequency",)
SIGNAL_NAMES = NUMBER_SIGNALS + CLASS_SIGNALS + BONUS_SIGNALS

DESCRIPTIONS = {
    "hot_w20": "Most frequent numbers in the last 20 draws",
    "hot_w50": "Most frequent numbers in the last 50 draws",
    "hot_w200": "Most frequent numbers in the last 200 draws",
    "cold_w50": "Least frequent numbers in the last 50 draws",
    "gap_overdue": "Numbers with the longest gap since last drawn",
    "positional": "Most frequent number per (sorted) position",
    "pair_cooccurrence": "Numbers that co-occur most with the last draw",
    "triplet_cooccurrence": "Numbers seen with 2+ numbers of the last draw",
    "repeat_last": "Repeat numbers from the last draw",
    "day_of_week": "Most frequent numbers on the target draw's weekday",
    "sum_band": "Modal sum band (null quintiles) of recent draws",
    "odd_even": "Modal odd count of recent draws",
    "high_low": "Modal high-number count of recent draws",
    "bonus_frequency": "Most frequent bonus ball in the last 100 draws",
}


def _onehot_rows(values: np.ndarray, size: int) -> np.ndarray:
    """(n, k) int offsets (-1 = missing) -> (n, k, size) one-hot."""
    n, k = values.shape
    out = np.zeros((n, k, size), dtype=float)
    rows, cols = np.nonzero(values >= 0)
    out[rows, cols, values[rows, cols]] = 1.0
    return out


class PatternMatrix:
    """Pre-computed, leak-free views of one game's history (oldest first)."""

    def __init__(self, history: list[Draw], rules: GameRules):
        self.history = list(history)
        self.rules = rules
        self.lo = int(rules.primary_min)
        self.size = int(rules.primary_max) - self.lo + 1
        self.k = int(rules.primary_count)
        self.digit = not bool(rules.primary_unique)
        n = len(self.history)
        self.n = n
        incl = np.zeros((n, self.size), dtype=float)
        pos = np.full((n, self.k), -1, dtype=int)
        spos = np.full((n, self.k), -1, dtype=int)
        for row, draw in enumerate(self.history):
            offsets = [int(v) - self.lo for v in draw.primary]
            valid = [o for o in offsets if 0 <= o < self.size]
            for o in set(valid):
                incl[row, o] = 1.0
            for p, o in enumerate(offsets[: self.k]):
                if 0 <= o < self.size:
                    pos[row, p] = o
            for p, o in enumerate(sorted(set(valid))[: self.k]):
                spos[row, p] = o
        self.incl = incl
        self.pos = pos
        self.cum_incl = np.vstack([np.zeros(self.size), np.cumsum(incl, axis=0)])
        positional = pos if self.digit else spos
        self.cum_pos = np.concatenate([np.zeros((1, self.k, self.size)),
                                       np.cumsum(_onehot_rows(positional, self.size), axis=0)])
        self.drawn = np.array([int(r.sum()) for r in incl], dtype=int)
        # Gaps (draws since last seen) known before draw i, pooled and per position.
        gaps = np.zeros((n + 1, self.size))
        last = np.full(self.size, -1.0)
        gaps_pos = np.zeros((n + 1, self.k, self.size))
        last_pos = np.full((self.k, self.size), -1.0)
        for row in range(n + 1):
            gaps[row] = np.where(last < 0, row + 1.0, row - last)
            gaps_pos[row] = np.where(last_pos < 0, row + 1.0, row - last_pos)
            if row < n:
                last = np.where(incl[row] > 0, float(row), last)
                for p in range(self.k):
                    if pos[row, p] >= 0:
                        last_pos[p, pos[row, p]] = float(row)
        self.gaps = gaps
        self.gaps_pos = gaps_pos
        self.weekday = np.array([d.draw_date.weekday() if d.draw_date else -1 for d in self.history], dtype=int)
        bmin, bmax = int(rules.bonus_min), int(rules.bonus_max)
        self.bonus_size = bmax - bmin + 1
        self.bonus = np.array([
            (int(d.bonus[0]) - bmin) if d.bonus and bmin <= int(d.bonus[0]) <= bmax else -1
            for d in self.history
        ], dtype=int)
        self._null_cache: dict | None = None

    # ---------------------------------------------------------------- windows
    def _window(self, i: int, w: int) -> np.ndarray:
        return self.cum_incl[i] - self.cum_incl[max(0, i - w)]

    def _window_pos(self, i: int, w: int) -> np.ndarray:
        return self.cum_pos[i] - self.cum_pos[max(0, i - w)]

    def _pooled(self, scores: np.ndarray) -> np.ndarray:
        """Digit games: the same pooled score on every position."""
        return np.tile(scores, (self.k, 1)) if self.digit else scores

    # ---------------------------------------------------------------- signals
    def scores(self, name: str, i: int) -> np.ndarray:
        """Signal scores for draw ``i`` from ``history[:i]``: (size,) or (k, size) for digit games."""
        if name.startswith("hot_w"):
            return self._pooled(self._window(i, int(name[5:])))
        if name == "cold_w50":
            return self._pooled(-self._window(i, 50))
        if name == "gap_overdue":
            return self.gaps_pos[i].copy() if self.digit else self.gaps[i].copy()
        if name == "positional":
            counts = self._window_pos(i, POSITIONAL_WINDOW)
            return counts if self.digit else counts.max(axis=0)
        if name in ("pair_cooccurrence", "triplet_cooccurrence"):
            return self._cooccurrence(i, triplet=name.startswith("triplet"))
        if name == "repeat_last":
            if i < 1:
                return self._pooled(np.zeros(self.size))
            tiebreak = self._window(i, 50) / 1000.0
            if self.digit:
                last = _onehot_rows(self.pos[i - 1: i], self.size)[0]
                return last + np.tile(tiebreak, (self.k, 1))
            return self.incl[i - 1] + tiebreak
        if name == "day_of_week":
            wd = int(self.weekday[i]) if i < self.n else -1
            if wd < 0:
                return self.scores("hot_w200", i)
            rows = np.nonzero(self.weekday[:i] == wd)[0][-DOW_WINDOW:]
            if self.digit:
                onehot = _onehot_rows(self.pos[rows], self.size) if rows.size else np.zeros((1, self.k, self.size))
                return onehot.sum(axis=0)
            return self.incl[rows].sum(axis=0) if rows.size else np.zeros(self.size)
        raise KeyError(name)

    def _cooccurrence(self, i: int, triplet: bool) -> np.ndarray:
        if i < (3 if triplet else 2):
            return self._pooled(np.zeros(self.size))
        start = max(0, i - 1 - PAIR_LOOKBACK)
        if self.digit:
            # Digit games: per-position transition (pair) / second-order (triplet) counts.
            out = np.zeros((self.k, self.size))
            for p in range(self.k):
                col = self.pos[start:i, p]
                if triplet:
                    a, b = self.pos[i - 2, p], self.pos[i - 1, p]
                    mask = (col[:-2] == a) & (col[1:-1] == b)
                    nxt = col[2:][mask]
                else:
                    mask = col[:-1] == self.pos[i - 1, p]
                    nxt = col[1:][mask]
                nxt = nxt[nxt >= 0]
                if nxt.size:
                    out[p] = np.bincount(nxt, minlength=self.size)[: self.size]
            return out
        prev = self.incl[start: i - 1]
        last = self.incl[i - 1]
        shared = prev @ last
        weights = (shared >= 2).astype(float) if triplet else shared
        return weights @ prev

    def number_scores(self, name: str, i: int) -> np.ndarray:
        """Pooled (size,) scores, used as ensemble features."""
        s = self.scores(name, i)
        return s.mean(axis=0) if s.ndim == 2 else s

    # ---------------------------------------------------------------- tickets
    def ticket(self, name: str, i: int) -> np.ndarray:
        s = np.asarray(self.scores(name, i), dtype=float)
        jitter = np.random.default_rng([int(i), 7]).random(s.shape) * 1e-6
        s = s + jitter
        if self.digit:
            return np.argmax(s, axis=1)
        return np.argsort(-s, kind="stable")[: self.k]

    def hits(self, ticket: np.ndarray, i: int) -> int:
        if self.digit:
            return int(np.sum((ticket == self.pos[i]) & (self.pos[i] >= 0)))
        return int(self.incl[i][ticket].sum())

    # ---------------------------------------------------------------- classes
    def _class_values(self, offsets: np.ndarray, kind: str) -> np.ndarray:
        """offsets: (m, k) ints (-1 missing). Returns class label per row."""
        values = np.where(offsets >= 0, offsets + self.lo, 0)
        valid = offsets >= 0
        if kind == "odd_even":
            return np.sum((values % 2 == 1) & valid, axis=1)
        if kind == "high_low":
            mid = (self.rules.primary_min + self.rules.primary_max) / 2.0
            return np.sum((values > mid) & valid, axis=1)
        return np.sum(values * valid, axis=1)  # raw sum; banded below

    def null_model(self) -> dict:
        if self._null_cache is not None:
            return self._null_cache
        rng = np.random.default_rng(101)
        drawn = int(np.median(self.drawn)) if self.n else self.k
        if self.digit:
            sims = rng.integers(0, self.size, size=(NULL_SIMULATIONS, self.k))
        else:
            width = max(1, min(drawn, self.size))
            sims = np.argsort(rng.random((NULL_SIMULATIONS, self.size)), axis=1)[:, :width]
        sums = self._class_values(sims, "sum")
        edges = np.unique(np.quantile(sums, np.linspace(0, 1, SUM_BANDS + 1)[1:-1]))
        out = {"edges": edges}
        for kind in CLASS_SIGNALS:
            labels = np.digitize(sums, edges) if kind == "sum_band" else self._class_values(sims, kind)
            counts = Counter(int(v) for v in labels)
            out[kind] = {c: counts[c] / len(labels) for c in counts}
        self._null_cache = out
        return out

    def draw_classes(self, kind: str) -> np.ndarray:
        source = self.pos if self.digit else self._sorted_offsets()
        raw = self._class_values(source, kind if kind != "sum_band" else "sum")
        if kind == "sum_band":
            return np.digitize(raw, self.null_model()["edges"])
        return raw

    def _sorted_offsets(self) -> np.ndarray:
        width = max(1, int(self.drawn.max()) if self.n else self.k)
        out = np.full((self.n, width), -1, dtype=int)
        for row in range(self.n):
            idx = np.nonzero(self.incl[row])[0]
            out[row, : idx.size] = idx
        return out


# -------------------------------------------------------------------- scoring

def _applicable(pm: PatternMatrix, name: str) -> tuple[bool, str]:
    if name == "bonus_frequency":
        if pm.rules.bonus_count <= 0:
            return False, "Game has no bonus ball."
        if pm.n == 0 or np.mean(pm.bonus >= 0) < 0.8:
            return False, "Bonus ball missing from most stored draws."
    if name == "day_of_week":
        known = pm.weekday[pm.weekday >= 0]
        if known.size < 0.8 * max(pm.n, 1) or np.unique(known).size < 2:
            return False, "Needs dated draws on 2+ weekdays."
    return True, ""


def _score(pm: PatternMatrix, name: str, indices: list[int], classes_cache: dict) -> dict:
    """Per-draw hits, chance expectation, and the null sampler parameters."""
    if name in CLASS_SIGNALS:
        labels = classes_cache.setdefault(name, pm.draw_classes(name))
        probs = pm.null_model()[name]
        hits, expected = [], []
        for i in indices:
            recent = labels[max(0, i - CLASS_WINDOW): i]
            if recent.size == 0:
                continue
            counts = Counter(int(v) for v in recent)
            pred = max(counts, key=lambda c: (counts[c], probs.get(c, 0.0), -c))
            hits.append(1.0 if int(labels[i]) == pred else 0.0)
            expected.append(float(probs.get(pred, 0.0)))
        return {"hits": np.array(hits), "expected": np.array(expected), "null": "bernoulli"}
    if name == "bonus_frequency":
        hits, expected = [], []
        p0 = 1.0 / max(pm.bonus_size, 1)
        for i in indices:
            if pm.bonus[i] < 0:
                continue
            past = pm.bonus[max(0, i - BONUS_WINDOW): i]
            past = past[past >= 0]
            if past.size == 0:
                continue
            pred = int(np.argmax(np.bincount(past, minlength=pm.bonus_size)))
            hits.append(1.0 if int(pm.bonus[i]) == pred else 0.0)
            expected.append(p0)
        return {"hits": np.array(hits), "expected": np.array(expected), "null": "bernoulli"}
    hits, expected, drawn = [], [], []
    for i in indices:
        ticket = pm.ticket(name, i)
        hits.append(float(pm.hits(ticket, i)))
        if pm.digit:
            expected.append(float(np.sum(pm.pos[i] >= 0)) / pm.size)
        else:
            k_drawn = int(pm.drawn[i])
            drawn.append(k_drawn)
            expected.append(pm.k * k_drawn / pm.size)
    return {"hits": np.array(hits), "expected": np.array(expected),
            "null": "binomial" if pm.digit else "hypergeometric", "drawn": np.array(drawn, dtype=int)}


def _p_value(pm: PatternMatrix, scored: dict, reps: int, seed: int) -> float:
    hits = scored["hits"]
    if hits.size == 0:
        return 1.0
    observed = float(hits.sum())
    rng = np.random.default_rng(seed)
    v = hits.size
    if scored["null"] == "hypergeometric":
        good = np.broadcast_to(scored["drawn"], (reps, v))
        null = rng.hypergeometric(good, pm.size - good, pm.k).sum(axis=1)
    elif scored["null"] == "binomial":
        null = rng.binomial(pm.k, 1.0 / pm.size, size=(reps, v)).sum(axis=1)
    else:
        null = (rng.random((reps, v)) < scored["expected"][None, :]).sum(axis=1)
    exceed = int(np.sum(null >= observed - 1e-9))
    return (exceed + 1) / (reps + 1)


def _summary(scored: dict, p: float | None) -> dict:
    hits, expected = scored["hits"], scored["expected"]
    if hits.size == 0:
        return {"draws": 0}
    mean, base = float(hits.mean()), float(expected.mean())
    return {
        "draws": int(hits.size),
        "mean_hits": round(mean, 4),
        "chance_hits": round(base, 4),
        "lift": round(mean / base, 4) if base > 0 else None,
        "p_value": None if p is None else round(float(p), 6),
    }


def run_pattern_search(
    game: str,
    history: list[Draw],
    *,
    rules: GameRules | None = None,
    metrics_cfg=None,
    holdout_draws: int | None = None,
    max_test_draws: int | None = None,
    fdr_q: float = 0.05,
    alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
    replicates: int = NULL_REPLICATES,
    signals: tuple[str, ...] | None = None,
    seed: int = 29,
) -> dict:
    """Score every signal walk-forward on validation, BH-FDR, then the holdout once."""
    started = time.time()
    if rules is None or metrics_cfg is None:
        from prediction.config.loader import game_rules_from_config, load_game_config

        rules = rules or game_rules_from_config(game)
        metrics_cfg = metrics_cfg or load_game_config(game).metrics
    n = len(history)
    test_start, holdout_start, holdout_size = three_way_split(
        n, metrics_cfg, holdout_draws=holdout_draws,
        max_test_draws=PATTERN_VALIDATION_DRAWS if max_test_draws is None else max_test_draws)
    base = {"game": game, "draws_available": n, "fdr_q": fdr_q, "alpha": alpha,
            "method": ("Walk-forward; chance baseline = hypergeometric / positional / class null; "
                       "ball-relabel permutation p (Monte Carlo); BH-FDR across signals on validation; "
                       "passing signals scored once on the untouched holdout.")}
    validation_idx = list(range(max(test_start, MIN_PRIOR), holdout_start))
    if holdout_size == 0 or len(validation_idx) < 5:
        return {**base, "status": "insufficient_data", "signals": [], "feature_signals": [], "passed_holdout": [],
                "note": "Not enough draws for a train / validation / untouched holdout split."}
    holdout_idx = list(range(holdout_start, n))
    pm = PatternMatrix(history, rules)
    names = [s for s in (signals or SIGNAL_NAMES) if s in SIGNAL_NAMES]
    classes_cache: dict = {}
    rows, tested = [], []
    for offset, name in enumerate(names):
        ok, why = _applicable(pm, name)
        family = "number" if name in NUMBER_SIGNALS else ("class" if name in CLASS_SIGNALS else "bonus")
        row = {"signal": name, "family": family, "description": DESCRIPTIONS[name]}
        if not ok:
            rows.append({**row, "status": "not_applicable", "label": "not applicable", "note": why})
            continue
        scored = _score(pm, name, validation_idx, classes_cache)
        p = _p_value(pm, scored, replicates, seed + offset)
        row["validation"] = _summary(scored, p)
        row["_p"] = p
        rows.append(row)
        tested.append(row)
    q_values, _ = benjamini_hochberg([r["_p"] for r in tested], fdr_q)
    for row, qv in zip(tested, q_values):
        row["q_value"] = round(float(qv), 6)
        lift = row["validation"].get("lift") or 0.0
        row["passed_validation"] = bool(lift > 1.0 and qv <= fdr_q)
        row.pop("_p", None)
        if row["passed_validation"]:
            # The untouched holdout is scored once, only for validation survivors.
            scored = _score(pm, row["signal"], holdout_idx, classes_cache)
            p_hold = _p_value(pm, scored, replicates, seed + 1000 + SIGNAL_NAMES.index(row["signal"]))
            row["holdout"] = _summary(scored, p_hold)
            hl = row["holdout"].get("lift") or 0.0
            row["passed_holdout"] = bool(hl > 1.0 and p_hold <= alpha)
        else:
            row["passed_holdout"] = False
        if row["passed_holdout"]:
            row["status"], row["label"] = "passed_holdout", "passed holdout"
        elif row["passed_validation"]:
            row["status"], row["label"] = "validation_only", "passed validation, failed holdout"
        else:
            row["status"], row["label"] = "not_significant", "not significant"
    return {
        **base,
        "status": "ok",
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 2),
        "split": {"draws": n, "validation_start": validation_idx[0], "holdout_start": holdout_start,
                  "validation_draws": len(validation_idx), "holdout_draws": holdout_size},
        "signals": rows,
        "tested": len(tested),
        "feature_signals": [r["signal"] for r in tested if r["passed_validation"] and r["family"] == "number"],
        "passed_holdout": [r["signal"] for r in tested if r["passed_holdout"]],
    }
