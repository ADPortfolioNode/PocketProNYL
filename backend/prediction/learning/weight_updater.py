"""Update ensemble weights after each real draw."""

from __future__ import annotations

import time
from typing import Callable

from prediction.core.types import Draw, GameRules, StrategyOutput, WeightState
from prediction.metrics.evaluator import score_plugin_on_draw
from prediction.state.weight_store import WeightStore


class WeightUpdater:
    """Nudge strategy/agent weights toward plugins that scored well on the actual draw."""

    def __init__(self, store: WeightStore | None = None):
        self.store = store or WeightStore()

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
        weights = dict(state.best_weights or state.weights)

        if not weights and initial_weights:
            weights = dict(initial_weights)

        if not weights:
            weights = {output.name: 1.0 / max(len(outputs), 1) for output in outputs}

        scores: dict[str, float] = {}
        for output in outputs:
            picks = output.picks or []
            scores[output.name] = score_plugin_on_draw(picks, actual, rules)

        if not scores:
            return state

        mean_score = sum(scores.values()) / len(scores)
        for name, score in scores.items():
            current = weights.get(name, min_weight)
            weights[name] = max(min_weight, current + learning_rate * (score - mean_score))

        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        state.weights = weights
        promoted = False
        if validation_accuracy is not None:
            score = float(validation_accuracy)
            if state.best_validation_accuracy is None or score >= state.best_validation_accuracy:
                state.best_weights = dict(weights)
                state.best_validation_accuracy = score
                state.best_validation_at = time.time()
                promoted = True
        state.updated_at = time.time()
        state.last_draw_id = actual.draw_id
        state.history.append({
            "timestamp": state.updated_at,
            "draw_id": actual.draw_id,
            "plugin_scores": scores,
            "mean_score": round(mean_score, 4),
            "validation_accuracy": validation_accuracy,
            "promoted": promoted,
        })
        self.store.save(state)
        return state