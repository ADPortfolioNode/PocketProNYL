"""Tests for the overfit gate (backend/prediction/metrics/overfit_gate.py).

Stdlib only. The gate reads only the evaluator's already-computed
holdout summary; it never re-scores draws.
"""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.metrics.overfit_gate import (
    evaluate_overfit_gate,
    stored_candidate_fails_gate,
)


def _clean_metrics(**overrides):
    metrics = {
        "overfit_gap_percent": 0.5,
        "overfit_boundary_percent": 2.0,
        "overfit_boundary_reached": False,
        "overfit_warning_reasons": [],
        "holdout_accuracy_percent": 30.0,
        "holdout_p_value": 0.01,
    }
    metrics.update(overrides)
    return metrics


def test_clean_holdout_is_not_blocked(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "block")
    result = evaluate_overfit_gate(_clean_metrics())
    assert result["blocked"] is False
    assert result["reasons"] == []


def test_gap_at_boundary_blocks(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "block")
    result = evaluate_overfit_gate(
        _clean_metrics(overfit_gap_percent=2.0, overfit_boundary_reached=True)
    )
    assert result["blocked"] is True
    assert "validation_beats_holdout" in result["reasons"]


def test_nonsignificant_holdout_blocks(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "block")
    result = evaluate_overfit_gate(_clean_metrics(holdout_p_value=0.20))
    assert result["blocked"] is True
    assert "holdout_not_significant_vs_random" in result["reasons"]


def test_no_holdout_blocks(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "block")
    result = evaluate_overfit_gate(
        _clean_metrics(
            holdout_accuracy_percent=None,
            holdout_p_value=None,
            overfit_warning_reasons=["no_holdout"],
        )
    )
    assert result["blocked"] is True
    assert "no_holdout" in result["reasons"]


def test_warn_mode_reports_but_does_not_block(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "warn")
    result = evaluate_overfit_gate(
        _clean_metrics(overfit_gap_percent=5.0, overfit_boundary_reached=True)
    )
    assert result["blocked"] is False
    assert "validation_beats_holdout" in result["reasons"]


def test_no_metrics_blocks_in_block_mode(monkeypatch):
    monkeypatch.setenv("OVERFIT_GATE_MODE", "block")
    result = evaluate_overfit_gate(None)
    assert result["blocked"] is True


def test_stored_candidate_with_explicit_gap_failure_is_skipped():
    assert stored_candidate_fails_gate(
        {"overfit_gap_percent": 5.0, "overfit_boundary_percent": 2.0}
    ) is True
    assert stored_candidate_fails_gate(
        {"overfit_boundary_reached": True}
    ) is True
    assert stored_candidate_fails_gate({"holdout_p_value": 0.40}) is True


def test_stored_candidate_without_holdout_data_passes_through():
    # Pre-gate stored records have no holdout fields; they are not
    # discarded, only explicitly failing ones are skipped.
    assert stored_candidate_fails_gate({"accuracy": 0.91}) is False
    assert stored_candidate_fails_gate(None) is False


def test_every_live_game_has_a_per_game_profile():
    from utils.training_defaults import GAME_TRAINING_DEFAULTS

    for game in (
        "take5",
        "pick3",
        "powerball",
        "megamillions",
        "pick10",
        "cash4life",
        "numbers",
        "win4",
    ):
        assert game in GAME_TRAINING_DEFAULTS, game
    # Numbers is the same 3-digit daily game as Pick 3.
    assert GAME_TRAINING_DEFAULTS["numbers"]["max_depth"] == 8
    assert GAME_TRAINING_DEFAULTS["numbers"]["target_accuracy"] == 0.85
