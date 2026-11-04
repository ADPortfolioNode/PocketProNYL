"""Tests for the verified-draw fallback (backend/services/verified_draws.py).

Stdlib only, network stubbed: urlopen is replaced with a fake that returns
canned Socrata rows, so no test hits the network. The fallback reads the
game's own configured dataset and grades against it only for past dates;
grading itself is covered end to end on the venv backend, not here.
"""

import io
import json
import sys
import urllib.error
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import verified_draws


class _FakeResponse:
    def __init__(self, payload):
        self._io = io.StringIO(json.dumps(payload))

    def __enter__(self):
        return self._io

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, payload, seen):
    def fake(request, timeout=None):
        seen.append(request.full_url)
        return _FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake)


def test_megamillions_row_parses(monkeypatch):
    seen = []
    row = {"draw_date": "2026-11-03T00:00:00.000",
           "winning_numbers": "04 24 31 62 70", "mega_ball": "03"}
    _stub_urlopen(monkeypatch, [row], seen)
    draws, error = verified_draws.official_draws_for_date("megamillions", date(2026, 11, 3))
    assert error is None
    assert len(draws) == 1
    assert draws[0].primary == [4, 24, 31, 62, 70]
    assert draws[0].bonus == [3]
    assert draws[0].draw_date.isoformat() == "2026-11-03"
    assert draws[0].metadata["source"] == "verified_official"
    assert draws[0].metadata["dataset"] == "5xaw-6ayf"
    assert "5xaw-6ayf" in seen[0] and "2026-11-03" in seen[0]


def test_session_game_row_keeps_session(monkeypatch):
    seen = []
    row = {"draw_date": "2026-10-28T00:00:00.000",
           "midday_winning_numbers": "01 02 03 04 05",
           "evening_winning_numbers": "08 12 17 36 39"}
    _stub_urlopen(monkeypatch, [row], seen)
    draws, error = verified_draws.official_draws_for_date("take5", date(2026, 10, 28))
    assert error is None
    assert [(d.metadata.get("draw_session"), d.primary) for d in draws] == [
        ("midday", [1, 2, 3, 4, 5]), ("evening", [8, 12, 17, 36, 39])]


def test_wrong_length_row_is_skipped(monkeypatch):
    seen = []
    row = {"draw_date": "2026-11-03T00:00:00.000", "winning_numbers": "04 24 31"}
    _stub_urlopen(monkeypatch, [row], seen)
    draws, error = verified_draws.official_draws_for_date("megamillions", date(2026, 11, 3))
    assert error is None
    assert draws == []


def test_fetch_failure_returns_error_not_exception(monkeypatch):
    def failing(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("urllib.request.urlopen", failing)
    draws, error = verified_draws.official_draws_for_date("megamillions", date(2026, 11, 3))
    assert draws == []
    assert error and "megamillions" in error and "2026-11-03" in error


def test_quickdraw_never_fetches(monkeypatch):
    calls = []

    def fake(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse([])

    monkeypatch.setattr("urllib.request.urlopen", fake)
    draws, error = verified_draws.official_draws_for_date("quickdraw", date(2026, 11, 3))
    assert draws == [] and error is None and calls == []
