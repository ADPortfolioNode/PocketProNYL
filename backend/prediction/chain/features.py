"""Leak-free per-draw features for the stacking meta-learner.

``features_at(i)`` describes every number in the universe using only
``history[:i]``: the normalized score of each existing strategy/agent (the
same plugins the incumbent ensemble blends), the Beta-Binomial posterior mean,
and the draws since the number last appeared. ``labels_at(i)`` marks the
numbers in ``history[i]``. A model fit on positions ``< j`` therefore never sees
draw ``j`` or anything after it.
"""

from __future__ import annotations

import numpy as np

from prediction.chain.posterior import BetaPosterior, _is_newer, chance_inclusion_rate
from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw, StrategyOutput

MIN_HISTORY = 30


class FeatureBuilder:
    def __init__(self, engine, game: str, history: list[Draw], base_posterior: BetaPosterior,
                 min_history: int = MIN_HISTORY, pattern_signals: tuple[str, ...] = ()):
        self.engine = engine
        self.game = game
        self.history = list(history)
        self.config = engine._load_config(game)
        self.rules = game_rules_from_config(game)
        self.min_history = int(min_history)
        self.size = self.rules.primary_max - self.rules.primary_min + 1
        self._outputs: dict[int, list[StrategyOutput]] = {}
        self._features: dict[int, np.ndarray] = {}
        self.plugin_names: list[str] = list(self.config.enabled_strategies) + list(self.config.enabled_agents)
        self.feature_names = [f"plugin:{n}" for n in self.plugin_names] + ["posterior_mean_ratio", "gap_ratio"]
        # Pattern-search signals that passed validation for THIS game (rank-normalized columns).
        self.pattern_signals: tuple[str, ...] = tuple(pattern_signals or ())
        self._patterns = None
        if self.pattern_signals:
            from prediction.patterns.search import PatternMatrix

            self._patterns = PatternMatrix(self.history, self.rules)
            self.feature_names += [f"pattern:{name}" for name in self.pattern_signals]
        n = len(self.history)
        incl = np.zeros((n, self.size), dtype=float)
        for row, draw in enumerate(self.history):
            for number in set(draw.primary):
                offset = int(number) - self.rules.primary_min
                if 0 <= offset < self.size:
                    incl[row, offset] = 1.0
        self.inclusion = incl
        newer = np.array([1.0 if _is_newer(d, base_posterior.as_of) else 0.0 for d in self.history])
        self._cum_hits = np.vstack([np.zeros(self.size), np.cumsum(incl * newer[:, None], axis=0)])
        self._cum_n = np.concatenate([[0.0], np.cumsum(newer)])
        self._alpha0 = np.asarray(base_posterior.alpha, dtype=float)
        self._beta0 = np.asarray(base_posterior.beta, dtype=float)
        self.p0 = chance_inclusion_rate(self.rules)
        gaps = np.zeros((n + 1, self.size), dtype=float)
        last_seen = np.full(self.size, -1.0)
        for row in range(n + 1):
            gaps[row] = np.where(last_seen < 0, row + 1.0, row - last_seen)
            if row < n:
                last_seen = np.where(incl[row] > 0, float(row), last_seen)
        self._gaps = gaps

    def outputs_at(self, i: int) -> list[StrategyOutput]:
        if i not in self._outputs:
            self._outputs[i] = self.engine._collect_outputs(self.history[:i], self.config)
        return self._outputs[i]

    def posterior_means_at(self, i: int) -> np.ndarray:
        alpha = self._alpha0 + self._cum_hits[i]
        beta = self._beta0 + (self._cum_n[i] - self._cum_hits[i])
        return alpha / (alpha + beta)

    def features_at(self, i: int) -> np.ndarray:
        if i in self._features:
            return self._features[i]
        by_name = {o.name: o for o in self.outputs_at(i)}
        columns = []
        for name in self.plugin_names:
            output = by_name.get(name)
            scores = np.zeros(self.size)
            if output is not None and output.scores:
                raw = np.maximum(np.asarray(output.scores[: self.size], dtype=float), 0.0)
                if raw.size < self.size:
                    raw = np.pad(raw, (0, self.size - raw.size))
                total = raw.sum()
                scores = raw / total * self.size if total > 0 else np.ones(self.size)
            columns.append(scores)
        columns.append(self.posterior_means_at(i) / max(self.p0, 1e-9))
        expected_gap = 1.0 / max(self.p0, 1e-9)
        columns.append(np.minimum(self._gaps[i] / expected_gap, 10.0))
        for name in self.pattern_signals:
            raw = self._patterns.number_scores(name, i)
            ranks = np.argsort(np.argsort(raw, kind="stable"), kind="stable").astype(float)
            columns.append(ranks / max(self.size - 1, 1))
        matrix = np.vstack(columns).T
        self._features[i] = matrix
        return matrix

    def labels_at(self, i: int) -> np.ndarray:
        return self.inclusion[i]
