"""Accuracy history: bucketed by suggestion_date (services/accuracy_history.py).
No network: draws are injected.
"""

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import Draw  # noqa: E402
from services.accuracy_history import build_accuracy_history  # noqa: E402

DRAWS = [
    Draw(primary=[1, 2, 3, 33, 34], date=date(2026, 10, 21),
         metadata={"draw_session": "evening"}),
    Draw(primary=[9, 9, 9, 9, 9], date=date(2026, 10, 22),
         metadata={"draw_session": "evening"}),
]


def _entry(served, target_date, session="evening", basis="schedule_time"):
    return {
        "primary": [1, 2, 3, 4, 5], "bonus": [],
        "served_at": served, "target_draw_date": target_date,
        "target_draw_session": session, "target_basis": basis,
        "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }


ENTRIES = [
    _entry("2026-10-21T10:00:00-04:00", "2026-10-21"),
    _entry("2026-10-22T10:00:00-04:00", "2026-10-22"),
    # Served Nov 4, no Nov draw in the fixture set -> pending, excluded.
    _entry("2026-11-04T10:00:00-05:00", "2026-11-04"),
]


def test_monthly_buckets_and_cumulative(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    history = build_accuracy_history("take5", "monthly",
                                     entries=ENTRIES, experiment_entries=[],
                                     draws=DRAWS)
    months = history["ledger"]
    assert [m["period"] for m in months] == ["2026-10"]
    october = months[0]
    assert october["n"] == 2
    assert october["mean_hits"] == 1.5
    assert october["expected_mean_hits_by_chance"] == 0.641
    assert october["lift_vs_chance"] == round(1.5 / 0.641, 4)
    assert october["cumulative_n"] == 2
    assert history["experiments"] == []


def test_weekly_buckets_group_same_week(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    history = build_accuracy_history("take5", "weekly",
                                     entries=ENTRIES, experiment_entries=[],
                                     draws=DRAWS)
    weeks = history["ledger"]
    assert len(weeks) == 1
    assert weeks[0]["period"] == "2026-W43"
    assert weeks[0]["n"] == 2


def test_experiment_series_is_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    exp_entries = [
        {"primary": [1, 2, 3, 4, 5], "bonus": [],
         "served_at": "2026-10-21T10:00:00-04:00", "target_basis": "experiment",
         "model": "ensemble", "recent_k": 10,
         "strategy_weights": {}, "strategy_contributions": {}},
    ]
    history = build_accuracy_history("take5", "monthly",
                                     entries=[], experiment_entries=exp_entries,
                                     draws=DRAWS)
    assert history["ledger"] == []
    assert len(history["experiments"]) == 1
    assert history["experiments"][0]["n"] == 1
    assert history["experiments"][0]["mean_hits"] == 3.0
