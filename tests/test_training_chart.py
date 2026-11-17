"""Tests for the training overfit-check matplotlib graph."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.training_chart import render_training_chart


def _status(metrics, game="take5"):
    return {
        "status": "completed",
        "current_game": game,
        "metrics_by_game": {game: metrics},
    }


def test_idle_training_chart_is_png():
    png = render_training_chart({"status": "idle", "games_total": 0, "games_completed": 0})
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_training_chart_plots_rounds_gap_and_reason():
    png = render_training_chart(
        _status({
            "validation_accuracy_percent": 40.0,
            "kept_validation_accuracy_percent": 18.0,
            "holdout_accuracy_percent": 15.0,
            "overfit_gap_percent": 3.0,
            "overfit_boundary_percent": 2.0,
            "overfit_boundary_reached": True,
            "overfit_gap_significant": True,
            "holdout_noise_margin_percent": 1.5,
            "holdout_evaluated_draws": 30,
            "overfit_warning": True,
            "overfit_warning_reasons": ["validation_beats_holdout"],
            "round_metrics": [
                {"round": 1, "accuracy_percent": 30.0, "held_accuracy_percent": 30.0},
                {"round": 2, "accuracy_percent": 35.0, "held_accuracy_percent": 35.0},
                {"round": 3, "accuracy_percent": 40.0, "held_accuracy_percent": 40.0},
            ],
        }),
        game="take5",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 2000


def test_training_chart_with_no_edge_reason_is_png():
    # A holdout that merely did not beat random is its own label, not a
    # gap: the chart still renders, with the gap inside the noise margin.
    png = render_training_chart(
        _status({
            "validation_accuracy_percent": 20.0,
            "kept_validation_accuracy_percent": 16.0,
            "holdout_accuracy_percent": 15.0,
            "overfit_gap_percent": 1.0,
            "overfit_boundary_percent": 2.0,
            "overfit_boundary_reached": False,
            "overfit_gap_significant": False,
            "holdout_noise_margin_percent": 2.5,
            "holdout_p_value": 0.42,
            "holdout_evaluated_draws": 30,
            "overfit_warning": True,
            "overfit_warning_reasons": ["holdout_not_significant_vs_random"],
            "round_metrics": [
                {"round": 1, "accuracy_percent": 18.0, "held_accuracy_percent": 18.0},
                {"round": 2, "accuracy_percent": 20.0, "held_accuracy_percent": 20.0},
            ],
        }),
        game="take5",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 2000
