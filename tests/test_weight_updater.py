"""Tests for post-draw weight updates."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config
from prediction.core.draw_loader import draws_from_lists
from prediction.core.registry import ensure_plugins_loaded, get_strategy_class
from prediction.core.types import Draw, StrategyOutput
from prediction.learning.weight_updater import WeightUpdater
from prediction.state.weight_store import WeightStore


def test_weights_shift_toward_better_plugin():
    ensure_plugins_loaded()
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        updater = WeightUpdater(store)
        rules = game_rules_from_config("pick3")
        history = draws_from_lists([[1, 2, 3], [4, 5, 6]], "pick3")
        freq = get_strategy_class("frequency")().analyze(history, rules)
        delta = get_strategy_class("delta")().analyze(history, rules)
        actual = Draw(primary=[1, 2, 3])

        state = updater.update(
            game="pick3",
            actual=actual,
            outputs=[freq, delta],
            rules=rules,
            learning_rate=0.2,
            initial_weights={"frequency": 0.5, "delta": 0.5},
        )
        assert abs(sum(state.weights.values()) - 1.0) < 0.01
        assert "frequency" in state.weights


def test_weight_updater_rejects_accuracy_regression():
    ensure_plugins_loaded()
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        updater = WeightUpdater(store)
        rules = game_rules_from_config("pick3")
        history = draws_from_lists([[1, 2, 3], [4, 5, 6]], "pick3")
        freq = get_strategy_class("frequency")().analyze(history, rules)
        delta = get_strategy_class("delta")().analyze(history, rules)
        actual = Draw(primary=[1, 2, 3], draw_id="draw-1")

        promoted = updater.update(
            game="pick3",
            actual=actual,
            outputs=[freq, delta],
            rules=rules,
            learning_rate=0.2,
            initial_weights={"frequency": 0.5, "delta": 0.5},
            validation_accuracy=0.42,
        )
        best = dict(promoted.best_weights)
        assert promoted.best_validation_accuracy == 0.42
        assert promoted.history[-1]["promoted"] is True

        rejected = updater.update(
            game="pick3",
            actual=Draw(primary=[7, 8, 9], draw_id="draw-2"),
            outputs=[freq, delta],
            rules=rules,
            learning_rate=0.2,
            initial_weights={"frequency": 0.5, "delta": 0.5},
            validation_accuracy=0.21,
        )
        assert rejected.history[-1]["regressed"] is True
        assert rejected.history[-1]["promoted"] is False
        assert rejected.best_validation_accuracy == 0.42
        assert rejected.best_weights == best
        assert rejected.weights == best


def test_weight_updater_boosts_plugin_that_hit():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        updater = WeightUpdater(store)
        rules = game_rules_from_config("pick3")
        actual = Draw(primary=[1, 2, 3], draw_id="hit-1")
        frequency = StrategyOutput(name="frequency", scores=[1, 1, 1], picks=[1, 2, 3])
        delta = StrategyOutput(name="delta", scores=[0, 0, 0], picks=[7, 8, 9])

        state = updater.update(
            game="pick3",
            actual=actual,
            outputs=[frequency, delta],
            rules=rules,
            learning_rate=0.2,
            initial_weights={"frequency": 0.5, "delta": 0.5},
        )
        assert state.weights["frequency"] > state.weights["delta"]
        assert state.weights["frequency"] > 0.5


def test_weight_updater_keeps_challenger_when_accuracy_holds():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        updater = WeightUpdater(store)
        rules = game_rules_from_config("pick3")
        frequency = StrategyOutput(name="frequency", scores=[1, 1, 1], picks=[1, 2, 3])
        delta = StrategyOutput(name="delta", scores=[0, 0, 0], picks=[7, 8, 9])

        promoted = updater.update(
            game="pick3",
            actual=Draw(primary=[1, 2, 3], draw_id="draw-1"),
            outputs=[frequency, delta],
            rules=rules,
            learning_rate=0.2,
            initial_weights={"frequency": 0.5, "delta": 0.5},
            validation_accuracy=0.40,
        )
        best = dict(promoted.best_weights)

        held = updater.update(
            game="pick3",
            actual=Draw(primary=[1, 2, 3], draw_id="draw-2"),
            outputs=[frequency, delta],
            rules=rules,
            learning_rate=0.2,
            validation_accuracy=0.396,
        )
        assert held.history[-1]["held"] is True
        assert held.best_weights == best
        assert held.weights["frequency"] >= best["frequency"]