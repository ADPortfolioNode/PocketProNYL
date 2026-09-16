"""Tests for the matplotlib tuner graph."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.tuning_chart import render_tuning_chart


def test_idle_tuning_chart_is_png():
    png = render_tuning_chart({"status": "idle", "games_total": 0, "games_completed": 0})
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_running_tuning_chart_plots_accuracy_curve():
    png = render_tuning_chart(
        {
            "status": "running",
            "games_total": 2,
            "games_completed": 1,
            "current_game": "take5",
            "current_task": "iterative_validation",
            "tuning_metrics": {
                "accuracy_percent": 22.0,
                "highest_accuracy_percent": 40.0,
                "target_accuracy_percent": 98.0,
                "regression_count": 3,
                "promotion_count": 4,
                "verification_round": 2,
                "suggestion_history": [
                    {"accuracy_percent": 20, "rolling_accuracy_percent": 20},
                    {"accuracy_percent": 40, "rolling_accuracy_percent": 30},
                    {"accuracy_percent": 0, "rolling_accuracy_percent": 20, "draw_regressed": True},
                    {"accuracy_percent": 20, "rolling_accuracy_percent": 20},
                    {"accuracy_percent": 20, "rolling_accuracy_percent": 18},
                ],
            },
        },
        game="take5",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 2000
