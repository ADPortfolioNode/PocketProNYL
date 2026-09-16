"""Verify the optimized tuning workflow is wired end to end."""

import inspect
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import WeightState
from prediction.engine import LotteryPredictionEngine
from prediction.state.weight_store import WeightStore
from services.game_tuner import GameTuner
from services.tuning_assistant import EARLY_EXIT_MARGIN_PERCENT, OPTIMIZE_TRIALS, TuningAssistant
from utils.chat_tools import _tool_optimize_suggestions


def test_optimize_trials_are_baseline_aggressive_stable():
    labels = [trial["label"] for trial in OPTIMIZE_TRIALS]
    assert labels == ["baseline", "aggressive", "stable"]
    rates = [trial["learning_rate"] for trial in OPTIMIZE_TRIALS]
    assert rates[1] > rates[0] > rates[2]
    assert EARLY_EXIT_MARGIN_PERCENT > 0


def test_engine_uses_challenger_weights_before_best():
    source = inspect.getsource(LotteryPredictionEngine._resolve_weights)
    assert "state.weights" in source
    assert source.find("state.weights") < source.find("state.best_weights")


def test_tune_loop_runs_assistant_optimizer():
    source = inspect.getsource(GameTuner.tune)
    assert "self.optimize_game(game_key)" in source


def test_chat_and_dashboard_share_optimize_tool():
    assert _tool_optimize_suggestions.__doc__
    assert "search" in (_tool_optimize_suggestions.__doc__ or "").lower() or "best" in (_tool_optimize_suggestions.__doc__ or "").lower()


def test_workflow_keeps_highest_held_accuracy_and_adopts_best():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        store.save(WeightState(game="take5", weights={"frequency": 1.0}, best_weights={"frequency": 1.0}))

        def run_trial(settings):
            label = settings["label"]
            held = {"baseline": 18.0, "aggressive": 16.0, "stable": 20.5}[label]
            rolling = {"baseline": 25.0, "aggressive": 28.0, "stable": 19.0}[label]
            store.save(WeightState(
                game="take5",
                weights={"frequency": 0.1, "hot_cold": 0.9},
                best_weights={"frequency": 0.4, "hot_cold": 0.6} if label == "stable" else {"frequency": 0.2, "hot_cold": 0.8},
                best_validation_accuracy=held / 100,
            ))
            return {
                "status": "ok",
                "game": "take5",
                "held_accuracy_percent": held,
                "accuracy_percent": held,
                "rolling_accuracy_percent": rolling,
            }

        result = TuningAssistant(store).optimize_game("take5", run_trial=run_trial)
        assert result["assistant"]["best_settings"]["label"] == "stable"
        assert result["held_accuracy_percent"] == 20.5
        kept = store.load("take5")
        assert kept.weights == kept.best_weights
        assert kept.best_weights["frequency"] == 0.4
