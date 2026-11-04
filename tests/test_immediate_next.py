"""Immediate-next-draw grading (services/suggestion_grader.py).

A suggestion grades against the first draw after it was served, not
blindly against its recorded target date. Covered here with injected
draws only (no network: explicit draws are a closed world).
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from services.suggestion_grader import grade_entries  # noqa: E402


def _entry(served, target_date, session, target_time, basis="schedule_time"):
    return {"game": "take5", "primary": [1, 2, 3, 4, 5],
            "served_at": served, "target_draw_date": target_date,
            "target_draw_session": session, "target_draw_time": target_time,
            "target_basis": basis, "model": "ensemble", "source": "test"}


def _draw(numbers, day, session):
    return Draw(primary=numbers, date=day,
                metadata={"draw_session": session} if session else {})


RULES = game_rules_from_config("take5")


def test_skipped_target_grades_against_next_real_draw():
    # Served Mon, target Tue (draw skipped, no Tue row), Wed draw exists.
    entry = _entry("2026-10-19T15:00:00-04:00", "2026-10-20", "evening",
                   "2026-10-20T22:30:00-04:00")
    draws = [_draw([1, 2, 30, 31, 32], date(2026, 10, 21), "evening")]
    graded, info = grade_entries("take5", [entry], draws=draws)
    assert info["verified_draws_fetched"] == 0
    assert graded[0]["status"] == "graded"
    assert graded[0]["graded_against"] == "immediate_next"
    assert graded[0]["actual_draw_date"] == "2026-10-21"
    assert graded[0]["hits"] == 2


def test_session_mismatch_skips_earlier_session():
    # Served 15:00 (after midday 14:30), target evening; only midday Oct 21
    # and midday Oct 22 exist: the next draw after serving is Oct 22 midday.
    entry = _entry("2026-10-21T15:00:00-04:00", "2026-10-21", "evening",
                   "2026-10-21T22:30:00-04:00")
    draws = [_draw([1, 2, 30, 31, 32], date(2026, 10, 21), "midday"),
             _draw([1, 2, 3, 33, 34], date(2026, 10, 22), "midday")]
    graded, _ = grade_entries("take5", [entry], draws=draws)
    assert graded[0]["status"] == "graded"
    assert graded[0]["actual_draw_date"] == "2026-10-22"
    assert graded[0]["actual_draw_session"] == "midday"
    assert graded[0]["hits"] == 3


def test_served_after_target_stays_not_gradable():
    entry = _entry("2026-10-21T15:00:00-04:00", "2026-10-21", "evening",
                   "2026-10-21T22:30:00-04:00")
    leaked = {**entry, "served_at": "2026-10-21T23:00:00-04:00"}
    draws = [_draw([1, 2, 3, 33, 34], date(2026, 10, 21), "evening")]
    graded, _ = grade_entries("take5", [leaked], draws=draws)
    assert graded[0]["status"] == "not_gradable"
    assert graded[0]["reason"] == "served_after_draw"


def test_requested_target_is_honored_over_immediate_next():
    # Explicitly requested a later draw: an intervening draw must not steal it.
    entry = _entry("2026-10-19T15:00:00-04:00", "2026-10-21", "evening",
                   "2026-10-21T22:30:00-04:00", basis="requested")
    draws = [_draw([9, 9, 9, 9, 9], date(2026, 10, 20), "evening"),
             _draw([1, 2, 3, 33, 34], date(2026, 10, 21), "evening")]
    graded, _ = grade_entries("take5", [entry], draws=draws)
    assert graded[0]["status"] == "graded"
    assert graded[0]["graded_against"] == "target"
    assert graded[0]["actual_draw_date"] == "2026-10-21"


def test_explicit_draws_never_fetch():
    entry = _entry("2026-10-19T15:00:00-04:00", "2026-10-20", "evening",
                   "2026-10-20T22:30:00-04:00")
    graded, info = grade_entries("take5", [entry], draws=[])
    assert graded[0]["status"] == "pending"
    assert info["verified_draws_fetched"] == 0
