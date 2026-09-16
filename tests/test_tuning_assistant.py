"""Tests for the tuning assistant search."""

import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.state.weight_store import WeightStore
from prediction.core.types import WeightState
from services.tuning_assistant import EARLY_EXIT_MARGIN_PERCENT, TuningAssistant, result_score


def test_result_score_prefers_held_floor():
    assert result_score({"held_accuracy_percent": 21.0, "rolling_accuracy_percent": 30.0}) == 21.0
    assert result_score({"accuracy_percent": 18}) == 18
    assert result_score({"rolling_accuracy_percent": 12}) == 12
    assert result_score({}) == -1.0


def test_assistant_keeps_best_trial_and_restores_regression():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        store.save(WeightState(game="pick3", weights={"frequency": 1.0}, best_weights={"frequency": 1.0}))
        scores = {"baseline": 18.0, "aggressive": 21.5, "stable": 19.0}
        events = []

        def run_trial(settings):
            store.save(WeightState(
                game="pick3",
                weights={"frequency": 0.2, "delta": 0.8},
                best_weights={"frequency": 0.2, "delta": 0.8},
                best_validation_accuracy=scores[settings["label"]] / 100,
            ))
            return {
                "status": "ok",
                "game": "pick3",
                "accuracy_percent": scores[settings["label"]],
                "rolling_accuracy_percent": scores[settings["label"]],
            }

        assistant = TuningAssistant(store, on_trial=events.append)
        result = assistant.optimize_game("pick3", run_trial=run_trial)
        assert result["assistant"]["best_settings"]["label"] == "aggressive"
        assert result["rolling_accuracy_percent"] == 21.5
        kept = store.load("pick3")
        assert kept.best_weights["delta"] == 0.8
        assert any(item.get("kept") is False for item in result["assistant"]["trials"])
        assert len(events) >= 3
        assert result["assistant"]["early_exit"] is False
        assert result["assistant"]["stopped_reason"] == "all_trials"


def test_assistant_early_exits_when_baseline_beats_held_floor():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        store.save(WeightState(
            game="take5",
            weights={"frequency": 1.0},
            best_weights={"frequency": 1.0},
            best_validation_accuracy=0.20,
        ))
        ran = []

        def run_trial(settings):
            ran.append(settings["label"])
            held = {"baseline": 20.0 + EARLY_EXIT_MARGIN_PERCENT + 0.2, "aggressive": 30.0, "stable": 31.0}[settings["label"]]
            store.save(WeightState(
                game="take5",
                weights={"frequency": 0.3, "delta": 0.7},
                best_weights={"frequency": 0.3, "delta": 0.7},
                best_validation_accuracy=held / 100,
            ))
            return {
                "status": "ok",
                "game": "take5",
                "held_accuracy_percent": held,
                "accuracy_percent": held,
            }

        result = TuningAssistant(store).optimize_game("take5", run_trial=run_trial)
        assert ran == ["baseline"]
        assert result["assistant"]["early_exit"] is True
        assert result["assistant"]["stopped_reason"] == "baseline_improved"
        assert result["assistant"]["best_settings"]["label"] == "baseline"
        assert len(result["assistant"]["trials"]) == 1


def test_assistant_keeps_existing_mix_when_no_trial_beats_floor():
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        store.save(WeightState(
            game="pick3",
            weights={"frequency": 1.0},
            best_weights={"frequency": 1.0},
            best_validation_accuracy=0.22,
        ))

        def run_trial(settings):
            store.save(WeightState(
                game="pick3",
                weights={"delta": 1.0},
                best_weights={"delta": 1.0},
                best_validation_accuracy=0.10,
            ))
            return {
                "status": "ok",
                "game": "pick3",
                "held_accuracy_percent": 10.0,
                "accuracy_percent": 10.0,
            }

        result = TuningAssistant(store).optimize_game("pick3", run_trial=run_trial)
        assert result["assistant"]["kept_existing"] is True
        assert result["assistant"]["early_exit"] is False
        kept = store.load("pick3")
        assert kept.best_weights == {"frequency": 1.0}
        assert result["held_accuracy_percent"] == 22.0
