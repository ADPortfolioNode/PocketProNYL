"""All-games accuracy process (build_all_games_accuracy in
services/accuracy_report.py): every game graded from its merged
history at once, with a plain reason for every Simulated game.
No network: draws are injected for every game.
"""

import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from config import GAME_CONFIGS  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from services.accuracy_report import build_all_games_accuracy  # noqa: E402


class _StubLedger:
    def __init__(self, entries):
        self._entries = entries

    def read_suggestions(self, game):
        return list(self._entries)

    def read_feedback(self, game):
        return []


def _epoch(iso):
    return datetime.fromisoformat(iso).timestamp()


def _ledger_entry(served, target_date, session, numbers):
    return {
        "id": f"ledger-{served}", "game": "take5", "primary": numbers, "bonus": [],
        "served_at": served, "served_at_epoch": _epoch(served),
        "target_draw_date": target_date, "target_draw_session": session,
        "target_basis": "schedule_time", "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }


def _exp_entry(served, numbers, bonus=None):
    return {
        "id": f"predict-test-{served}", "primary": numbers, "bonus": bonus or [],
        "served_at": served, "served_at_epoch": _epoch(served),
        "target_basis": "experiment", "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }


def test_all_games_labels_and_reasons(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    t5_draws = [Draw(primary=[1, 2, 3, 33, 34], date=date(2026, 10, 21),
                     metadata={"draw_session": "evening"})]
    pb_draws = [Draw(primary=[1, 2, 3, 33, 34], bonus=[6], date=date(2026, 10, 21))]
    ledgers = {g: _StubLedger([]) for g in GAME_CONFIGS}
    ledgers["take5"] = _StubLedger([
        _ledger_entry("2026-10-21T10:00:00-04:00", "2026-10-21", "evening", [1, 2, 3, 4, 5]),
    ])
    draws = {g: [] for g in GAME_CONFIGS}
    draws["take5"] = t5_draws
    draws["powerball"] = pb_draws
    experiments = {g: [] for g in GAME_CONFIGS}
    experiments["powerball"] = [_exp_entry("2026-10-21T10:00:00-04:00", [1, 2, 3, 4, 5], [6])]
    # pick3 has a suggestion but no matching draw: Simulated, ungraded.
    ledgers["pick3"] = _StubLedger([
        {**_ledger_entry("2026-10-21T10:00:00-04:00", "2026-10-21", "evening", [1, 2, 3]),
         "game": "pick3"},
    ])
    result = build_all_games_accuracy(
        ledgers_by_game=ledgers, draws_by_game=draws, experiments_by_game=experiments)
    games = result["games"]
    assert games["take5"]["data_label"] == "Real"
    assert games["take5"]["graded"] == 1
    assert games["powerball"]["data_label"] == "Real"
    assert games["powerball"]["graded"] == 1
    assert games["powerball"]["reason"] is None
    assert games["megamillions"]["data_label"] == "Simulated"
    assert "No suggestions" in games["megamillions"]["reason"]
    assert games["pick3"]["data_label"] == "Simulated"
    assert "none graded" in games["pick3"]["reason"]
    assert games["quickdraw"]["data_label"] == "Excluded"
    assert result["real_games"] == ["powerball", "take5"]
    assert result["total_graded"] == 2


def test_all_games_isolates_per_game_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    class _BoomLedger(_StubLedger):
        def read_suggestions(self, game):
            raise RuntimeError("boom")

    ledgers = {g: _StubLedger([]) for g in GAME_CONFIGS}
    ledgers["take5"] = _BoomLedger([])
    draws = {g: [] for g in GAME_CONFIGS}
    experiments = {g: [] for g in GAME_CONFIGS}
    result = build_all_games_accuracy(
        ledgers_by_game=ledgers, draws_by_game=draws, experiments_by_game=experiments)
    assert result["games"]["take5"]["status"] == "error"
    assert result["games"]["megamillions"]["data_label"] == "Simulated"
    assert result["status"] == "ok"
