"""Rebalance ensemble weights toward plugins that hit, and keep mixes that raise accuracy."""

from __future__ import annotations

import math
import time

from prediction.core.types import Draw, GameRules, StrategyOutput, WeightState
from prediction.metrics.evaluator import score_plugin_on_draw
from prediction.state.weight_store import WeightStore

_IMPROVEMENT_EPS = 1e-12
_REGRESSION_MARGIN = 0.01
_MAX_LEARNING_RATE = 0.45
_RECENT_WINDOW = 8


class WeightUpdater:
    """Shift strategy/agent mass toward plugins that scored well on real draws."""

    def __init__(self, store: WeightStore | None = None):
        self.store = store or WeightStore()

    def _adaptive_mix(self, state: WeightState, learning_rate: float, min_weight: float) -> float:
        """Larger mix when recent promotions land; smaller mix after regressions."""
        recent = state.history[-6:]
        mix = min(max(float(learning_rate) * 2.4, 0.08), 0.55)
        if recent:
            regressions = sum(1 for item in recent if item.get("regressed"))
            promotions = sum(1 for item in recent if item.get("promoted"))
            if promotions >= 2 and regressions == 0:
                mix = min(0.65, mix * 1.25)
            elif regressions >= 2:
                mix = max(0.08, mix * (0.75 ** min(regressions - 1, 3)))
        return min(max(mix, float(min_weight)), _MAX_LEARNING_RATE)

    def _normalize(self, weights: dict[str, float], min_weight: float) -> dict[str, float]:
        clipped = {name: max(float(min_weight), float(value)) for name, value in weights.items() if value is not None}
        total = sum(clipped.values())
        if total <= 0:
            n = max(len(clipped), 1)
            return {name: 1.0 / n for name in clipped}
        return {name: value / total for name, value in clipped.items()}

    def _recent_plugin_scores(self, state: WeightState, current: dict[str, float]) -> dict[str, float]:
        """Average this draw with recent plugin hits so one miss does not dominate."""
        totals: dict[str, float] = {name: float(score) for name, score in current.items()}
        counts: dict[str, int] = {name: 1 for name in current}
        for item in state.history[-_RECENT_WINDOW:]:
            plugin_scores = item.get("plugin_scores") or {}
            if not isinstance(plugin_scores, dict):
                continue
            for name, score in plugin_scores.items():
                try:
                    totals[name] = totals.get(name, 0.0) + float(score)
                    counts[name] = counts.get(name, 0) + 1
                except (TypeError, ValueError):
                    continue
        return {name: totals[name] / max(counts.get(name, 1), 1) for name in totals}

    def _hit_weighted_balance(self, scores: dict[str, float], min_weight: float) -> dict[str, float]:
        """Put more mass on plugins that actually matched the draw (softmax of hits)."""
        if not scores:
            return {}
        peak = max(scores.values())
        temperature = 0.18
        weighted: dict[str, float] = {}
        for name, score in scores.items():
            # Square the hit rate so a plugin that matched 2/5 beats one that matched 0.
            strength = max(float(score), 0.0) ** 2
            weighted[name] = math.exp(strength / temperature) if peak > 0 else 1.0
        return self._normalize(weighted, min_weight)

    def _blend(
        self,
        base: dict[str, float],
        target: dict[str, float],
        mix: float,
        min_weight: float,
    ) -> dict[str, float]:
        names = set(base) | set(target)
        combined = {
            name: (1.0 - mix) * float(base.get(name, min_weight)) + mix * float(target.get(name, min_weight))
            for name in names
        }
        return self._normalize(combined, min_weight)

    def update(
        self,
        game: str,
        actual: Draw,
        outputs: list[StrategyOutput],
        rules: GameRules,
        learning_rate: float = 0.05,
        min_weight: float = 0.01,
        initial_weights: dict[str, float] | None = None,
        validation_accuracy: float | None = None,
    ) -> WeightState:
        state = self.store.load(game, initial_weights=initial_weights)
        base = dict(state.best_weights or state.weights)

        if not base and initial_weights:
            base = dict(initial_weights)

        if not base:
            base = {output.name: 1.0 / max(len(outputs), 1) for output in outputs}

        scores: dict[str, float] = {}
        for output in outputs:
            picks = output.picks or []
            scores[output.name] = score_plugin_on_draw(picks, actual, rules)
            if output.name not in base:
                base[output.name] = min_weight

        if not scores:
            return state

        base = self._normalize(base, min_weight)
        recent = self._recent_plugin_scores(state, scores)
        target = self._hit_weighted_balance(recent, min_weight)
        mix = self._adaptive_mix(state, learning_rate, min_weight)
        candidate = self._blend(base, target, mix, min_weight)

        promoted = False
        regressed = False
        held = False
        if validation_accuracy is None:
            state.weights = candidate
        else:
            score = float(validation_accuracy)
            best = state.best_validation_accuracy
            if best is None or score > float(best) + _IMPROVEMENT_EPS:
                state.weights = candidate
                state.best_weights = dict(candidate)
                state.best_validation_accuracy = score
                state.best_validation_at = time.time()
                promoted = True
            elif best is not None and score < float(best) - _REGRESSION_MARGIN:
                if state.best_weights:
                    state.weights = dict(state.best_weights)
                else:
                    state.weights = base
                regressed = True
            else:
                # Keep the challenger mix live so the next draw can test it.
                state.weights = candidate
                held = True
        state.updated_at = time.time()
        state.last_draw_id = actual.draw_id
        state.history.append({
            "timestamp": state.updated_at,
            "draw_id": actual.draw_id,
            "plugin_scores": scores,
            "mean_score": round(sum(scores.values()) / max(len(scores), 1), 4),
            "validation_accuracy": validation_accuracy,
            "learning_rate": round(mix, 6),
            "promoted": promoted,
            "regressed": regressed,
            "held": held,
            "challenger_weights": dict(candidate),
        })
        self.store.save(state)
        return state
