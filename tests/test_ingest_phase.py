"""Ingest phase reporting, stall detection, and the cross-platform timeout."""

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from state import ingest_phase  # noqa: E402


@pytest.fixture(autouse=True)
def clean_phases():
    ingest_phase._phases.clear()
    yield
    ingest_phase._phases.clear()


def test_phase_snapshot_and_stall():
    ingest_phase.report_phase("take5", "fetching", detail="10 of ~100 rows", rows_done=10, rows_total=100)
    snap = ingest_phase.phase_snapshot("take5")
    assert snap["phase"] == "fetching" and snap["phase_rows_done"] == 10 and snap["stalled"] is False
    later = time.time() + ingest_phase.stall_timeout_for("fetching") + 5
    assert ingest_phase.phase_snapshot("take5", now=later)["stalled"] is True
    # The model download gets a longer allowance than ordinary phases.
    assert ingest_phase.stall_timeout_for("loading_embedding_model") > ingest_phase.stall_timeout_for("fetching")
    ingest_phase.report_phase("take5", "done")
    assert ingest_phase.phase_snapshot("take5", now=later)["stalled"] is False
    assert ingest_phase.active_phase() == (None, None)


def test_startup_status_reports_phase_and_stall(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from routes import health

    ingest_phase.report_phase("pick3", "embedding_and_writing", detail="500 rows written",
                              rows_done=500, rows_total=2000)
    status = health.get_ingestion_status()
    assert status["status"] == "ingesting"
    assert status["phase_game"] == "pick3" and status["phase"] == "embedding_and_writing"
    assert status["phase_label"].startswith("Embedding") and status["stalled"] is False
    assert status["games"]["pick3"]["phase_rows_done"] == 500

    real_time = time.time
    monkeypatch.setattr(health.time, "time", lambda: real_time() + 10_000)
    stalled = health.get_ingestion_status()
    assert stalled["stalled"] is True and "no progress" in stalled["stall_message"]


def test_with_timeout_is_cross_platform():
    from services.ingest import TimeoutError as IngestTimeout, with_timeout

    @with_timeout(0.2)
    def slow():
        time.sleep(2)

    @with_timeout(2)
    def fast(x):
        return x * 2

    @with_timeout(2)
    def broken():
        raise ValueError("boom")

    with pytest.raises(IngestTimeout):
        slow()
    assert fast(21) == 42
    with pytest.raises(ValueError):
        broken()
