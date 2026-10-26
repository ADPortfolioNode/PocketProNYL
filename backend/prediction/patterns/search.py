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

import math
import re
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


# Adaptive search: windowed signal families. ``<base>_w<W>`` (W draws) or
# ``<base>_wall`` (expanding window over all prior draws). Legacy names above are
# fixed points of the same families, so callers and stored variants keep working.
WINDOWED_BASES = ("hot", "cold", "positional", "pair_cooccurrence", "triplet_cooccurrence", "day_of_week",
                  "sum_band", "odd_even", "high_low", "bonus_frequency")
FIXED_BASES = ("gap_overdue", "repeat_last")
LEGACY = {
    "hot_w20": ("hot", 20), "hot_w50": ("hot", 50), "hot_w200": ("hot", 200), "cold_w50": ("cold", 50),
    "positional": ("positional", POSITIONAL_WINDOW), "pair_cooccurrence": ("pair_cooccurrence", PAIR_LOOKBACK),
    "triplet_cooccurrence": ("triplet_cooccurrence", PAIR_LOOKBACK), "day_of_week": ("day_of_week", DOW_WINDOW),
    "sum_band": ("sum_band", CLASS_WINDOW), "odd_even": ("odd_even", CLASS_WINDOW),
    "high_low": ("high_low", CLASS_WINDOW), "bonus_frequency": ("bonus_frequency", BONUS_WINDOW),
    "gap_overdue": ("gap_overdue", None), "repeat_last": ("repeat_last", None),
}
BASE_DESCRIPTIONS = {
    "hot": "Most frequent numbers", "cold": "Least frequent numbers",
    "positional": "Most frequent number per (sorted) position",
    "pair_cooccurrence": "Numbers that co-occur most with the last draw",
    "triplet_cooccurrence": "Numbers seen with 2+ numbers of the last draw",
    "day_of_week": "Most frequent numbers on the target draw's weekday",
    "sum_band": "Modal sum band (null quintiles)", "odd_even": "Modal odd count",
    "high_low": "Modal high-number count", "bonus_frequency": "Most frequent bonus ball",
    "gap_overdue": "Numbers with the longest gap since last drawn", "repeat_last": "Repeat numbers from the last draw",
}
_NAME_RE = re.compile(r"^([a-z_]+?)_w(all|\d+)$")


def parse_signal(name: str) -> tuple[str, int | None]:
    """Signal name -> (base, window). Window 0 = all prior draws; None = no window."""
    if name in LEGACY:
        return LEGACY[name]
    m = _NAME_RE.match(str(name))
    if m and m.group(1) in WINDOWED_BASES:
        return m.group(1), 0 if m.group(2) == "all" else int(m.group(2))
    raise KeyError(name)


def signal_name(base: str, window: int | None) -> str:
    if window is None:
        return base
    return f"{base}_w{'all' if window == 0 else int(window)}"


def signal_family(name: str) -> str:
    base, _ = parse_signal(name)
    if base in ("sum_band", "odd_even", "high_low"):
        return "class"
    return "bonus" if base == "bonus_frequency" else "number"


def describe_signal(name: str) -> str:
    if name in DESCRIPTIONS:
        return DESCRIPTIONS[name]
    base, w = parse_signal(name)
    text = BASE_DESCRIPTIONS.get(base, base)
    if w is None:
        return text
    return f"{text} ({'all prior draws' if w == 0 else f'last {w} draws'})"


def parse_windows(raw) -> list[int]:
    """'25,50,all' / [25, 'all'] -> sorted unique ints (0 = all)."""
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out = set()
    for item in items:
        text = str(item).strip().lower()
        if not text:
            continue
        if text in ("all", "0"):
            out.add(0)
        elif text.isdigit() and int(text) > 0:
            out.add(int(text))
    return sorted(out, key=lambda w: (w == 0, w))


def candidate_signals(windows, available_prior: int) -> list[str]:
    """Every (base, window) pair worth testing, windows capped by the draws available."""
    usable = [w for w in parse_windows(windows) if w == 0 or MIN_PRIOR <= w <= max(available_prior, MIN_PRIOR)]
    if not usable:
        usable = [0]
    names = list(FIXED_BASES)
    for base in WINDOWED_BASES:
        names += [signal_name(base, w) for w in usable]
    return names


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
        return self.cum_incl[i] - self.cum_incl[0 if w <= 0 else max(0, i - w)]

    def _window_pos(self, i: int, w: int) -> np.ndarray:
        return self.cum_pos[i] - self.cum_pos[0 if w <= 0 else max(0, i - w)]

    def _pooled(self, scores: np.ndarray) -> np.ndarray:
        """Digit games: the same pooled score on every position."""
        return np.tile(scores, (self.k, 1)) if self.digit else scores

    # ---------------------------------------------------------------- signals
    def scores(self, name: str, i: int) -> np.ndarray:
        """Signal scores for draw ``i`` from ``history[:i]``: (size,) or (k, size) for digit games."""
        base, w = parse_signal(name)
        if base == "hot":
            return self._pooled(self._window(i, w))
        if base == "cold":
            return self._pooled(-self._window(i, w))
        if base == "gap_overdue":
            return self.gaps_pos[i].copy() if self.digit else self.gaps[i].copy()
        if base == "positional":
            counts = self._window_pos(i, w)
            return counts if self.digit else counts.max(axis=0)
        if base in ("pair_cooccurrence", "triplet_cooccurrence"):
            return self._cooccurrence(i, triplet=base.startswith("triplet"), lookback=w)
        if base == "repeat_last":
            if i < 1:
                return self._pooled(np.zeros(self.size))
            tiebreak = self._window(i, 50) / 1000.0
            if self.digit:
                last = _onehot_rows(self.pos[i - 1: i], self.size)[0]
                return last + np.tile(tiebreak, (self.k, 1))
            return self.incl[i - 1] + tiebreak
        if base == "day_of_week":
            wd = int(self.weekday[i]) if i < self.n else -1
            if wd < 0:
                return self._pooled(self._window(i, w))
            rows = np.nonzero(self.weekday[:i] == wd)[0]
            if w > 0:
                rows = rows[-w:]
            if self.digit:
                onehot = _onehot_rows(self.pos[rows], self.size) if rows.size else np.zeros((1, self.k, self.size))
                return onehot.sum(axis=0)
            return self.incl[rows].sum(axis=0) if rows.size else np.zeros(self.size)
        raise KeyError(name)

    def _cooccurrence(self, i: int, triplet: bool, lookback: int = PAIR_LOOKBACK) -> np.ndarray:
        if i < (3 if triplet else 2):
            return self._pooled(np.zeros(self.size))
        start = 0 if lookback <= 0 else max(0, i - 1 - lookback)
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
    base, _ = parse_signal(name)
    if base == "bonus_frequency":
        if pm.rules.bonus_count <= 0:
            return False, "Game has no bonus ball."
        if pm.n == 0 or np.mean(pm.bonus >= 0) < 0.8:
            return False, "Bonus ball missing from most stored draws."
    if base == "day_of_week":
        known = pm.weekday[pm.weekday >= 0]
        if known.size < 0.8 * max(pm.n, 1) or np.unique(known).size < 2:
            return False, "Needs dated draws on 2+ weekdays."
    return True, ""


def _since(i: int, w: int) -> int:
    return 0 if w <= 0 else max(0, i - w)


def _score(pm: PatternMatrix, name: str, indices: list[int], classes_cache: dict) -> dict:
    """Per-draw hits, chance expectation, and the null sampler parameters."""
    base, w = parse_signal(name)
    if base in CLASS_SIGNALS:
        labels = classes_cache.setdefault(base, pm.draw_classes(base))
        probs = pm.null_model()[base]
        hits, expected = [], []
        for i in indices:
            recent = labels[_since(i, w): i]
            if recent.size == 0:
                continue
            counts = Counter(int(v) for v in recent)
            pred = max(counts, key=lambda c: (counts[c], probs.get(c, 0.0), -c))
            hits.append(1.0 if int(labels[i]) == pred else 0.0)
            expected.append(float(probs.get(pred, 0.0)))
        return {"hits": np.array(hits), "expected": np.array(expected), "null": "bernoulli"}
    if base == "bonus_frequency":
        hits, expected = [], []
        p0 = 1.0 / max(pm.bonus_size, 1)
        for i in indices:
            if pm.bonus[i] < 0:
                continue
            past = pm.bonus[_since(i, w): i]
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


DRIFT_MIN_CHUNK = 25
DRIFT_CHUNKS = 6
DRIFT_ALPHA = 0.10


def drift(scored: dict, *, alpha: float = DRIFT_ALPHA) -> dict:
    """Descriptive drift of a signal's lift across rolling chunks of its scored window.

    Lift per consecutive chunk, plus a Welch test of (hits - chance) in the recent
    half vs the earlier half. ``strengthening`` / ``fading`` only when that
    difference is significant at ``alpha``; otherwise ``stable``. Never used to
    select or promote; the FDR + holdout guard stays the judge.
    """
    hits, expected = np.asarray(scored.get("hits", []), float), np.asarray(scored.get("expected", []), float)
    n = hits.size
    if n < 2 * DRIFT_MIN_CHUNK:
        return {"trend": "insufficient", "rolling_lift": [], "chunk": None}
    chunk = max(DRIFT_MIN_CHUNK, n // DRIFT_CHUNKS)
    rolling = []
    for start in range(0, n - chunk + 1, chunk):
        e = expected[start:start + chunk].sum()
        rolling.append(round(float(hits[start:start + chunk].sum() / e), 4) if e > 0 else None)
    excess = hits - expected
    half = n // 2
    a, b = excess[:half], excess[half:]
    se = math.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size) if a.size > 1 and b.size > 1 else 0.0
    diff = float(b.mean() - a.mean())
    if se <= 0:
        p = 1.0 if abs(diff) < 1e-12 else 0.0
    else:
        p = math.erfc(abs(diff / se) / math.sqrt(2.0))
    ea, eb = expected[:half].sum(), expected[half:].sum()
    early = float(hits[:half].sum() / ea) if ea > 0 else None
    recent = float(hits[half:].sum() / eb) if eb > 0 else None
    xs = np.arange(len([r for r in rolling if r is not None]), dtype=float)
    ys = np.array([r for r in rolling if r is not None], dtype=float)
    slope = float(np.polyfit(xs, ys, 1)[0]) if ys.size >= 2 else 0.0
    trend = "stable"
    if p <= alpha:
        trend = "strengthening" if diff > 0 else "fading"
    return {"trend": trend, "rolling_lift": rolling, "chunk": int(chunk),
            "early_lift": None if early is None else round(early, 4),
            "recent_lift": None if recent is None else round(recent, 4),
            "slope_per_chunk": round(slope, 5), "p_value": round(float(p), 6)}


def score_signal_on(pm: PatternMatrix, name: str, indices: list[int], *, replicates: int = NULL_REPLICATES,
                    seed: int = 77) -> dict:
    """Score one signal on arbitrary (e.g. newly arrived) draws: summary with p vs chance."""
    scored = _score(pm, name, indices, {})
    return _summary(scored, _p_value(pm, scored, replicates, seed))


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
    windows=None,
    seed: int = 29,
) -> dict:
    """Score every signal walk-forward on validation, BH-FDR, then the holdout once.

    ``windows`` (e.g. ``[25, 50, 100, 200, "all"]``) switches on the adaptive search:
    every windowed signal family is tested at each window that fits the draws
    available before validation. Every (signal, window) pair tested is one member
    of the BH-FDR family, so a wider search raises the bar instead of inflating
    false positives.
    """
    started = time.time()
    if rules is None or metrics_cfg is None:
        from prediction.config.loader import game_rules_from_config, load_game_config

        rules = rules or game_rules_from_config(game)
        metrics_cfg = metrics_cfg or load_game_config(game).metrics
    n = len(history)
    test_start, holdout_start, holdout_size = three_way_split(
        n, metrics_cfg, holdout_draws=holdout_draws,
        max_test_draws=PATTERN_VALIDATION_DRAWS if max_test_draws is None else max_test_draws)
    adaptive = windows is not None and signals is None
    base = {"game": game, "draws_available": n, "fdr_q": fdr_q, "alpha": alpha, "adaptive": adaptive,
            "method": ("Walk-forward; chance baseline = hypergeometric / positional / class null; "
                       "ball-relabel permutation p (Monte Carlo); BH-FDR across every signal x window tested "
                       "on validation; passing signals scored once on the untouched holdout.")}
    validation_idx = list(range(max(test_start, MIN_PRIOR), holdout_start))
    if holdout_size == 0 or len(validation_idx) < 5:
        return {**base, "status": "insufficient_data", "signals": [], "feature_signals": [], "passed_holdout": [],
                "note": "Not enough draws for a train / validation / untouched holdout split."}
    holdout_idx = list(range(holdout_start, n))
    pm = PatternMatrix(history, rules)
    if adaptive:
        names = candidate_signals(windows, validation_idx[0])
    else:
        names = []
        for s in (signals or SIGNAL_NAMES):
            try:
                parse_signal(s)
                names.append(s)
            except KeyError:
                continue
    classes_cache: dict = {}
    rows, tested, scored_by = [], [], {}
    for offset, name in enumerate(names):
        ok, why = _applicable(pm, name)
        sig_base, window = parse_signal(name)
        row = {"signal": name, "base": sig_base, "window": ("all" if window == 0 else window),
               "family": signal_family(name), "description": describe_signal(name)}
        if not ok:
            rows.append({**row, "status": "not_applicable", "label": "not applicable", "note": why})
            continue
        scored = _score(pm, name, validation_idx, classes_cache)
        scored_by[name] = scored
        rows.append(row)
        tested.append(row)
    m = len(tested)
    # Resolution guard: the smallest attainable permutation p must sit well under BH's
    # first threshold q/m, or a bigger family could never pass anything.
    reps = max(int(replicates), int(math.ceil(4 * max(m, 1) / max(fdr_q, 1e-6))))
    for offset, row in enumerate(tested):
        row["_p"] = _p_value(pm, scored_by[row["signal"]], reps, seed + offset)
        row["validation"] = _summary(scored_by[row["signal"]], row["_p"])
        row["drift"] = drift(scored_by[row["signal"]])
    q_values, _ = benjamini_hochberg([r["_p"] for r in tested], fdr_q)
    for offset, (row, qv) in enumerate(zip(tested, q_values)):
        row["q_value"] = round(float(qv), 6)
        lift = row["validation"].get("lift") or 0.0
        row["passed_validation"] = bool(lift > 1.0 and qv <= fdr_q)
        row.pop("_p", None)
        if row["passed_validation"]:
            # The untouched holdout is scored once, only for validation survivors.
            scored = _score(pm, row["signal"], holdout_idx, classes_cache)
            p_hold = _p_value(pm, scored, reps, seed + 1000 + offset)
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
    # Best window per signal family (descriptive: which lookback fits this game best).
    best_params = {}
    for row in tested:
        if row["window"] is None:
            continue
        lift = row["validation"].get("lift") or 0.0
        cur = best_params.get(row["base"])
        if cur is None or lift > cur["lift"]:
            best_params[row["base"]] = {"window": row["window"], "lift": lift, "q_value": row["q_value"]}
    trends = Counter(r["drift"]["trend"] for r in tested)
    return {
        **base,
        "status": "ok",
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 2),
        "split": {"draws": n, "validation_start": validation_idx[0], "holdout_start": holdout_start,
                  "validation_draws": len(validation_idx), "holdout_draws": holdout_size},
        "windows": [("all" if w == 0 else w) for w in parse_windows(windows)] if adaptive else None,
        "signals": rows,
        "tested": m,
        "family_size": m,
        "replicates": reps,
        "best_params": best_params,
        "trend_counts": dict(trends),
        "feature_signals": [r["signal"] for r in tested if r["passed_validation"] and r["family"] == "number"],
        "passed_holdout": [r["signal"] for r in tested if r["passed_holdout"]],
    }
