"""Experiment -> ground-truth bridge (services/experiment_accuracy.py).

No network: draws are injected, the store is a temp dir.
"""

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from experiments.store import ExperimentStore  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from services.experiment_accuracy import (  # noqa: E402
    build_experiment_accuracy,
    experiment_to_entry,
)
from services.suggestion_grader import grade_entries  # noqa: E402


def _exp(exp_id, served_iso, numbers, game="take5", type_="prediction"):
    # Real records always carry runtime timestamps (see
    # _normalize_prediction_result); the prune sorts on them.
    from datetime import datetime as _dt

    epoch = _dt.fromisoformat(served_iso).timestamp()
    return {
        "experiment_id": exp_id,
        "game": game,
        "timestamp": int(epoch * 1000),
        "timestamp_seconds": int(epoch),
        "timestamp_iso": served_iso,
        "status": "COMPLETED",
        "type": type_,
        "recent_k": 10,
        "prediction": {
            "predicted_main_numbers": numbers,
            "predicted_bonus_numbers": [],
            "model_strategy": "ensemble",
            "engine": "modular",
            "strategy_weights": {"frequency": 1.0},
            "strategy_contributions": {"frequency": numbers},
        },
    }


def _draw(numbers, day, session):
    return Draw(primary=numbers, date=day,
                metadata={"draw_session": session} if session else {})


DRAWS = [
    _draw([1, 2, 30, 31, 32], date(2026, 10, 21), "midday"),
    _draw([1, 2, 3, 33, 34], date(2026, 10, 21), "evening"),
    _draw([1, 2, 3, 4, 35], date(2026, 10, 22), "midday"),
    _draw([9, 9, 9, 9, 9], date(2026, 10, 22), "evening"),
    _draw([1, 6, 11, 16, 39], date(2026, 10, 23), "evening"),
]


def test_adapt_prediction_experiment():
    entry = experiment_to_entry(
        _exp("predict-take5-1", "2026-10-21T15:00:00-04:00", [1, 2, 3, 4, 5]), "take5")
    assert entry is not None
    assert entry["primary"] == [1, 2, 3, 4, 5]
    assert entry["target_basis"] == "experiment"
    assert entry["strategy_weights"] == {"frequency": 1.0}
    assert entry["strategy_contributions"] == {"frequency": [1, 2, 3, 4, 5]}
    assert experiment_to_entry(_exp("train-1", "2026-10-21T15:00:00-04:00",
                                     [1, 2, 3, 4, 5], type_="training"), "take5") is None
    assert experiment_to_entry(_exp("predict-pick3-1", "2026-10-21T15:00:00-04:00",
                                     [1, 2, 3], game="pick3"), "take5") is None


def test_experiment_grades_against_immediate_next_draw():
    entry = experiment_to_entry(
        _exp("predict-take5-1", "2026-10-21T15:00:00-04:00", [1, 2, 3, 4, 5]), "take5")
    graded, _ = grade_entries("take5", [entry], draws=DRAWS)
    assert graded[0]["status"] == "graded"
    assert graded[0]["graded_against"] == "immediate_next"
    assert graded[0]["actual_draw_date"] == "2026-10-21"
    assert graded[0]["actual_draw_session"] == "evening"
    assert graded[0]["hits"] == 3


def test_experiment_served_after_same_day_draw_grades_next_day():
    # No target guard for experiments: served 23:00 (after the 22:30 draw)
    # still grades against the next draw, instead of refusing.
    entry = experiment_to_entry(
        _exp("predict-take5-2", "2026-10-21T23:00:00-04:00", [1, 2, 3, 4, 5]), "take5")
    graded, _ = grade_entries("take5", [entry], draws=DRAWS)
    assert graded[0]["status"] == "graded"
    assert graded[0]["actual_draw_date"] == "2026-10-22"
    assert graded[0]["actual_draw_session"] == "midday"


def test_midnight_draw_datetime_is_not_a_draw_time():
    # Regression: ingested draws carry draw_datetime at midnight
    # ("2026-10-21T00:00:00"), which is a date placeholder, not the draw
    # time. Trusting it would sort the Oct 21 evening draw before a
    # suggestion served Oct 21 morning, wrongly grading it against Oct 22.
    midnight_draws = [
        _draw([1, 2, 3, 33, 34], date(2026, 10, 21), "evening"),
        _draw([1, 2, 3, 4, 35], date(2026, 10, 22), "midday"),
    ]
    midnight_draws[0].metadata["draw_datetime"] = "2026-10-21T00:00:00"
    midnight_draws[1].metadata["draw_datetime"] = "2026-10-22T00:00:00"
    entry = experiment_to_entry(
        _exp("predict-take5-3", "2026-10-21T10:00:00-04:00", [1, 2, 3, 4, 5]), "take5")
    graded, _ = grade_entries("take5", [entry], draws=midnight_draws)
    assert graded[0]["status"] == "graded"
    assert graded[0]["actual_draw_date"] == "2026-10-21"
    assert graded[0]["actual_draw_session"] == "evening"
    assert graded[0]["hits"] == 3


def test_store_archives_evicted_instead_of_deleting(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = ExperimentStore(str(tmp_path / "experiments" / "experiments.json"))
    for i in range(5):
        store.save_experiment(_exp(f"predict-take5-{i}",
                                   f"2026-10-2{i}T15:00:00-04:00", [1, 2, 3, 4, 5]))
    live = store.list_experiments()
    assert [e["experiment_id"] for e in live] == [
        "predict-take5-4", "predict-take5-3", "predict-take5-2"]
    history = store.list_history()
    assert [e["experiment_id"] for e in history] == [
        "predict-take5-0", "predict-take5-1"]


def test_bridge_grades_live_plus_history(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    store = ExperimentStore(str(tmp_path / "experiments" / "experiments.json"))
    served = ["2026-10-21T15:00:00-04:00", "2026-10-21T23:00:00-04:00",
              "2026-10-22T15:00:00-04:00", "2026-10-22T23:00:00-04:00",
              "2026-10-24T15:00:00-04:00"]
    for i, when in enumerate(served):
        store.save_experiment(_exp(f"predict-take5-{i}", when, [1, 2, 3, 4, 5]))
    report = build_experiment_accuracy("take5", 3, store=store, draws=DRAWS)
    assert report["experiments_total"] == 5
    assert report["experiments_graded"] == 4
    assert report["data_label"] == "Real"
    assert report["pooled_last"]["graded"] == 2  # newest 3: pending + 2 graded
    assert report["pooled_all_time"]["graded"] == 4
