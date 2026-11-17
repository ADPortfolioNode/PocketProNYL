"""Suggestion-date vs expected-draw-date labels (services/suggestion_dates.py
and the grader rows that carry them). No network: draws are injected.
"""

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import Draw  # noqa: E402
from services.experiment_accuracy import experiment_to_entry  # noqa: E402
from services.suggestion_dates import expected_draw, suggestion_date  # noqa: E402
from services.suggestion_grader import grade_entries  # noqa: E402


def test_suggestion_date_uses_lottery_timezone():
    assert suggestion_date("2026-10-21T23:30:00-04:00") == "2026-10-21"
    assert suggestion_date("2026-10-22T00:30:00-04:00") == "2026-10-22"
    # 03:30 UTC is 23:30 ET the previous evening.
    assert suggestion_date("2026-10-22T03:30:00Z") == "2026-10-21"
    assert suggestion_date(None) is None


def test_expected_draw_session_games():
    assert expected_draw("take5", "2026-10-21T10:00:00-04:00") == ("2026-10-21", "midday")
    assert expected_draw("take5", "2026-10-21T15:00:00-04:00") == ("2026-10-21", "evening")
    assert expected_draw("take5", "2026-10-21T23:00:00-04:00") == ("2026-10-22", "midday")


def test_expected_draw_weekly_games():
    # Tue 2026-11-03 draws that night; after it, the next draw is Friday.
    assert expected_draw("megamillions", "2026-11-03T10:00:00-05:00") == ("2026-11-03", None)
    assert expected_draw("megamillions", "2026-11-03T23:30:00-05:00") == ("2026-11-06", None)


def test_stored_ledger_target_wins_and_experiment_serve_date_ignored():
    assert expected_draw("take5", "2026-10-21T10:00:00-04:00",
                         target_date="2026-12-25", target_session="evening",
                         target_basis="schedule_time") == ("2026-12-25", "evening")
    # An experiment's next_draw_date is its serve date, not a draw date:
    # the schedule still decides, even though a target date is stored.
    assert expected_draw("take5", "2026-10-21T15:00:00-04:00",
                         target_date="2026-10-21", target_basis="experiment") == ("2026-10-21", "evening")
    # ...unless a newer record carries an explicit expected draw.
    assert expected_draw("take5", "2026-10-21T15:00:00-04:00",
                         target_basis="experiment",
                         expected_date="2026-10-22",
                         expected_session="midday") == ("2026-10-22", "midday")


def _ledger_entry(served, target_date, session):
    return {
        "primary": [1, 2, 3, 4, 5], "bonus": [],
        "served_at": served, "target_draw_date": target_date,
        "target_draw_session": session, "target_basis": "schedule_time",
        "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }


DRAWS = [
    Draw(primary=[1, 2, 3, 33, 34], date=date(2026, 10, 21),
         metadata={"draw_session": "evening"}),
    Draw(primary=[9, 9, 9, 9, 9], date=date(2026, 10, 22),
         metadata={"draw_session": "evening"}),
]


def test_grader_rows_carry_both_dates_graded_and_pending():
    graded, _ = grade_entries("take5", [
        _ledger_entry("2026-10-21T10:00:00-04:00", "2026-10-21", "evening"),
    ], draws=DRAWS)
    row = graded[0]
    assert row["status"] == "graded"
    assert row["suggestion_date"] == "2026-10-21"
    assert row["expected_draw_date"] == "2026-10-21"
    assert row["expected_draw_session"] == "evening"
    assert row["actual_draw_date"] == "2026-10-21"

    pending, _ = grade_entries("take5", [
        {"primary": [1, 2, 3, 4, 5], "bonus": [],
         "served_at": "2026-10-24T15:00:00-04:00", "target_basis": "experiment",
         "model": "ensemble", "recent_k": 10,
         "strategy_weights": {}, "strategy_contributions": {}},
    ], draws=DRAWS)
    prow = pending[0]
    assert prow["status"] == "pending"
    assert prow["suggestion_date"] == "2026-10-24"
    assert prow["expected_draw_date"] == "2026-10-24"
    assert prow["expected_draw_session"] == "evening"


def test_experiment_adapt_uses_computed_expected_when_unlabeled():
    exp = {
        "experiment_id": "predict-take5-old", "game": "take5",
        "timestamp_iso": "2026-10-21T10:00:00-04:00",
        "status": "COMPLETED", "type": "prediction", "recent_k": 10,
        "prediction": {"predicted_main_numbers": [1, 2, 3, 4, 5],
                       "predicted_bonus_numbers": []},
    }
    entry = experiment_to_entry(exp, "take5")
    graded, _ = grade_entries("take5", [entry], draws=DRAWS)
    # Served 10:00 -> immediate-next is the midday draw, but the fixture
    # only has evening draws, so it grades against the evening draw;
    # the EXPECTED label is still the scheduled next draw: midday.
    assert graded[0]["suggestion_date"] == "2026-10-21"
    assert graded[0]["expected_draw_date"] == "2026-10-21"
    assert graded[0]["expected_draw_session"] == "midday"
