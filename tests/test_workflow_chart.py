"""Tests for the matplotlib live workflow charts."""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.workflow_chart import render_workflow_chart
from state import predict_jobs


def _assert_png(png):
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_pipeline_idle_chart_is_png():
    _assert_png(render_workflow_chart("pipeline", startup={}, jobs={}, predict={}))


def test_pipeline_ingest_active_chart_is_png():
    png = render_workflow_chart(
        "pipeline",
        startup={
            "status": "ingesting",
            "current_game": "take5",
            "current_game_rows_fetched": 50,
            "current_game_rows_total": 100,
            "populated_games": 0,
            "total": 9,
            "percent_complete": 5.5,
        },
        jobs={},
        predict={},
    )
    _assert_png(png)


def test_pipeline_training_and_prediction_states_chart_is_png():
    png = render_workflow_chart(
        "pipeline",
        startup={"status": "completed", "populated_games": 9, "total": 9},
        jobs={
            "pick3": {
                "game": "pick3",
                "status": "running",
                "message": "Training in progress",
                "target_accuracy": 0.5,
                "max_iterations": 100,
                "updated_at": time.time(),
            }
        },
        predict={
            "status": "done",
            "games": ["take5"],
            "completed": 1,
            "total": 1,
            "finished_at": time.time() - 45,
        },
    )
    _assert_png(png)


def test_pipeline_error_chart_is_png():
    png = render_workflow_chart(
        "pipeline",
        startup={"status": "error"},
        jobs={"take5": {"game": "take5", "status": "error"}},
        predict={"status": "error", "games": ["take5"], "completed": 0, "total": 1},
    )
    _assert_png(png)


def test_startup_ingesting_chart_is_png():
    png = render_workflow_chart(
        "startup",
        startup={
            "status": "ingesting",
            "current_game": "take5",
            "current_game_rows_fetched": 500,
            "current_game_rows_total": 1000,
            "percent_complete": 12.5,
            "completed_games": 1,
            "total": 11,
            "elapsed_s": 42,
            "available_games": ["take5", "pick3", "powerball"],
            "games": {
                "take5": {"status": "ingesting", "rows_fetched": 500, "total_rows": 1000},
                "pick3": {"status": "completed", "rows_fetched": 200, "total_rows": 200},
                "powerball": {"status": "pending", "rows_fetched": 0, "total_rows": 0},
            },
        },
    )
    _assert_png(png)


def test_ingest_active_chart_is_png():
    png = render_workflow_chart(
        "ingest",
        ingest={"status": "ingesting", "rows_fetched": 250, "total_rows": 1000},
        game="take5",
    )
    _assert_png(png)


def test_ingest_not_started_chart_is_png():
    _assert_png(render_workflow_chart("ingest", ingest=None, game="take5"))


def test_train_running_chart_is_png():
    png = render_workflow_chart(
        "train",
        jobs={
            "take5": {
                "game": "take5",
                "status": "running",
                "message": "Training in progress",
                "target_accuracy": 0.6,
                "max_iterations": 200,
                "updated_at": time.time(),
            },
            "pick3": {"game": "pick3", "status": "completed", "updated_at": time.time() - 300},
        },
        game="take5",
    )
    _assert_png(png)


def test_train_empty_chart_is_png():
    _assert_png(render_workflow_chart("train", jobs={}, game=None))


def test_predict_running_chart_is_png():
    png = render_workflow_chart(
        "predict",
        predict={
            "status": "running",
            "games": ["take5", "pick3", "powerball"],
            "completed": 1,
            "total": 3,
            "current_game": "pick3",
            "errors": 0,
            "results": {"take5": "done"},
            "started_at": time.time() - 30,
        },
    )
    _assert_png(png)


def test_predict_failed_games_chart_is_png():
    png = render_workflow_chart(
        "predict",
        predict={
            "status": "error",
            "games": ["take5", "pick3"],
            "completed": 2,
            "total": 2,
            "current_game": None,
            "errors": 2,
            "results": {"take5": "error", "pick3": "error"},
            "started_at": time.time() - 20,
            "finished_at": time.time() - 5,
        },
    )
    _assert_png(png)


def test_predict_idle_chart_is_png():
    _assert_png(render_workflow_chart("predict", predict={"status": "idle", "games": []}))


def test_unknown_workflow_raises():
    with pytest.raises(ValueError):
        render_workflow_chart("nope", startup={}, jobs={}, predict={})


def test_predict_batch_tracker_transitions():
    batch = predict_jobs.start_batch(["take5", "pick3"])
    assert batch["status"] == "running"
    assert batch["total"] == 2
    assert batch["current_game"] == "take5"

    batch = predict_jobs.mark_game_done("take5", ok=True)
    assert batch["completed"] == 1
    assert batch["current_game"] == "pick3"
    assert batch["results"]["take5"] == "done"

    batch = predict_jobs.mark_game_done("pick3", ok=False)
    assert batch["completed"] == 2
    assert batch["errors"] == 1
    assert batch["results"]["pick3"] == "error"
    assert batch["current_game"] is None

    batch = predict_jobs.finish_batch("done")
    assert batch["status"] == "done"
    assert batch["finished_at"] is not None

    snapshot = predict_jobs.get_predict_batch()
    assert snapshot["status"] == "done"
    assert snapshot["completed"] == 2
