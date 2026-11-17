"""Accuracy report grades the full suggestion history: ledger AND
experiments (services/accuracy_report.py). A game whose ledger is empty
must not report "no suggestions served" while experiment suggestions
sit ungraded — and a pick recorded in both places must count once.
No network: draws are injected.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import Draw  # noqa: E402
from services.accuracy_report import build_accuracy_report  # noqa: E402


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _exp_entry(served_iso, primary, bonus=None):
    return {
        "id": f"predict-test-{served_iso}",
        "primary": primary, "bonus": bonus or [],
        "served_at": served_iso, "served_at_epoch": _epoch(served_iso),
        "target_basis": "experiment", "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }


PB_DRAWS = [
    Draw(primary=[1, 2, 3, 33, 34], bonus=[6], date=date(2026, 10, 21)),
]
T5_DRAWS = [
    Draw(primary=[1, 2, 3, 33, 34], date=date(2026, 10, 21),
         metadata={"draw_session": "evening"}),
]


def test_report_grades_experiments_when_ledger_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    exp = _exp_entry("2026-10-21T10:00:00-04:00", [1, 2, 3, 4, 5], [6])
    report = build_accuracy_report(
        "powerball", last=3, draws=PB_DRAWS, include_backtest=False,
        experiment_entries=[exp])
    assert report["data_label"] == "Real"
    assert report["ledger_size"] == 0
    assert report["experiments_size"] == 1
    assert len(report["suggestions"]) == 1
    row = report["suggestions"][0]
    assert row["status"] == "graded"
    assert row["hits"] == 3
    assert row["bonus_hit"] is True
    assert row["suggestion_date"] == "2026-10-21"
    assert report["pooled_all_time"]["graded"] == 1


def test_report_dedupes_ledger_experiment_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ledger_dir = tmp_path / "ledger" / "take5"
    ledger_dir.mkdir(parents=True)
    served = "2026-10-21T10:00:00-04:00"
    ledger_entry = {
        "id": "ledger-1", "game": "take5", "primary": [1, 2, 3, 4, 5], "bonus": [],
        "served_at": served, "served_at_epoch": _epoch(served),
        "target_draw_date": "2026-10-21", "target_draw_session": "evening",
        "target_basis": "schedule_time", "model": "ensemble", "recent_k": 10,
        "strategy_weights": {}, "strategy_contributions": {},
    }
    (ledger_dir / "suggestions.jsonl").write_text(json.dumps(ledger_entry) + "\n")
    # Same serve event, 20 seconds later, recorded as an experiment.
    dup = _exp_entry("2026-10-21T10:00:20-04:00", [1, 2, 3, 4, 5])
    report = build_accuracy_report(
        "take5", last=5, draws=T5_DRAWS, include_backtest=False,
        experiment_entries=[dup])
    assert report["ledger_size"] == 1
    assert report["experiments_size"] == 1
    assert len(report["suggestions"]) == 1
    assert report["pooled_all_time"]["graded"] == 1
    assert report["suggestions"][0]["hits"] == 3


def test_report_keeps_distinct_experiment_picks(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    exps = [
        _exp_entry("2026-10-21T10:00:00-04:00", [1, 2, 3, 4, 5]),
        _exp_entry("2026-10-21T15:00:00-04:00", [6, 7, 8, 9, 10]),
    ]
    report = build_accuracy_report(
        "take5", last=5, draws=T5_DRAWS, include_backtest=False,
        experiment_entries=exps)
    assert len(report["suggestions"]) == 2
    assert report["pooled_all_time"]["graded"] == 2


def test_report_still_simulated_when_both_sources_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    report = build_accuracy_report(
        "take5", last=3, draws=T5_DRAWS, include_backtest=False,
        experiment_entries=[])
    assert report["data_label"] == "Simulated"
    assert report["suggestions"] == []
    assert report["pooled_all_time"]["graded"] == 0
