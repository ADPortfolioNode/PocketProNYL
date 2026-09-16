"""Tests for honest metrics."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config
from prediction.core.draw_loader import draws_from_lists
from prediction.core.types import PredictionTicket
from prediction.metrics.evaluator import (
    _boosted_rolling,
    _rolling_mean,
    random_baseline_metrics,
    resolve_test_start,
    walk_forward_backtest,
)


def test_random_baseline_is_modest():
    rules = game_rules_from_config("take5")
    history = draws_from_lists(
        [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15], [16, 17, 18, 19, 20]] * 20,
        "take5",
    )
    baseline = random_baseline_metrics(history, rules, trials=200)
    assert baseline["mean_partial_hits"] < 2.0
    assert baseline["exact_match_rate"] < 0.01


def test_walk_forward_returns_lift():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(80)], "pick3")

    def predict_fn(train_hist):
        last = train_hist[-1].primary
        return PredictionTicket(game="pick3", primary=last)

    result = walk_forward_backtest(history, rules, predict_fn, window=50, min_draws=30)
    assert result["status"] == "ok"
    assert "lift_vs_random" in result
    assert result["exact_match_rate"] < 0.5


def test_boosted_rolling_is_at_least_window_mean():
    rates = [0.0, 0.4, 0.2, 0.6, 0.0, 0.4, 0.2, 0.8, 0.0, 0.4, 0.6, 0.2]
    boosted = _boosted_rolling(rates, 8)
    mean = _rolling_mean(rates, 8)
    assert boosted >= mean
    assert boosted > 0


def test_walk_forward_marks_accuracy_regression():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(80)], "pick3")
    calls = {"n": 0}

    def predict_fn(train_hist):
        calls["n"] += 1
        if calls["n"] > 8:
            return PredictionTicket(game="pick3", primary=[9, 8, 7])
        return PredictionTicket(game="pick3", primary=train_hist[-1].primary)

    result = walk_forward_backtest(history, rules, predict_fn, window=50, min_draws=30, rolling_window=12)
    assert result["status"] == "ok"
    assert result["rolling_accuracy_percent"] is not None
    assert result["regression_count"] >= 1
    assert any(item.get("draw_regressed") for item in result["suggestion_history"])


def test_walk_forward_held_accuracy_only_increases():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(80)], "pick3")
    events = []

    def predict_fn(train_hist):
        return PredictionTicket(game="pick3", primary=train_hist[-1].primary)

    result = walk_forward_backtest(
        history,
        rules,
        predict_fn,
        window=50,
        min_draws=30,
        target_accuracy=0.99,
        verification_rounds=3,
        min_verification_rounds=2,
        improvement_patience=2,
        progress_callback=events.append,
    )
    assert result["status"] == "ok"
    assert result["held_accuracy_percent"] == result["accuracy_percent"]
    between = [event for event in events if event.get("between_runs")]
    assert between
    assert all(event.get("run_state") == "between_runs" for event in between)
    held_series = [event["held_accuracy_percent"] for event in between if event.get("held_accuracy_percent") is not None]
    assert held_series == sorted(held_series)


def test_walk_forward_keeps_iterating_until_patience():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(80)], "pick3")

    def predict_fn(train_hist):
        return PredictionTicket(game="pick3", primary=train_hist[-1].primary)

    result = walk_forward_backtest(
        history,
        rules,
        predict_fn,
        window=50,
        min_draws=30,
        target_accuracy=0.99,
        verification_rounds=4,
        min_verification_rounds=2,
        improvement_patience=2,
    )
    assert result["status"] == "ok"
    assert result["verification_rounds"] >= 2
    assert result["stopped_reason"] in {"patience", "max_rounds", "target"}
    assert result["round_metrics"]
    assert "accuracy_percent" in result["round_metrics"][0]
    if len(result["round_metrics"]) >= 2:
        assert "delta_percent" in result["round_metrics"][1]
        held = [item.get("held_accuracy_percent", item["accuracy_percent"]) for item in result["round_metrics"]]
        assert held == sorted(held)
        assert result["accuracy_percent"] == max(held)


def test_resolve_test_start_caps_recent_draws():
    # 250 draws, last third is 84; cap at 80 -> train 170 / test 80.
    assert resolve_test_start(250, min_draws=50, max_test_draws=80) == 170
    # Cap larger than the last third leaves the 2/3 split alone.
    assert resolve_test_start(90, min_draws=30, max_test_draws=80) == 60
    # No cap keeps the last third.
    assert resolve_test_start(90, min_draws=30, max_test_draws=None) == 60


def test_walk_forward_caps_test_draws():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(90)], "pick3")
    calls = {"n": 0}

    def predict_fn(train_hist):
        calls["n"] += 1
        return PredictionTicket(game="pick3", primary=train_hist[-1].primary)

    result = walk_forward_backtest(
        history,
        rules,
        predict_fn,
        window=50,
        min_draws=30,
        verification_rounds=1,
        max_test_draws=8,
    )
    assert result["status"] == "ok"
    assert result["split"]["test_draws"] == 8
    assert result["evaluated_draws"] == 8
    assert calls["n"] == 8