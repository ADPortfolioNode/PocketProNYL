"""Walk-forward retro-test (services/retro_backtest.py).

No network, no Chroma: draws are synthetic Draw objects and the engine is
a deterministic stand-in, plus one end-to-end run through the real engine.
Proves the three honesty properties of the harness:

- no peeking: a trial's pick depends only on draws before its target;
- simulated stays separate: rows land in the retro store only, every row
  labelled simulated, and the live suggestion ledger is never touched;
- the tail reserve really is held out unless explicitly included.
"""

import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import Draw  # noqa: E402
from services.retro_backtest import (  # noqa: E402
    read_simulated,
    retro_report,
    run_retro_backtest,
    simulated_path,
)
from services.suggestion_ledger import suggestions_path  # noqa: E402

GAME = "take5"  # 5 numbers from 1..39, unique


def _draws(n=100, seed=42):
    rng = random.Random(seed)
    day = date(2026, 1, 1)
    return [
        Draw(primary=sorted(rng.sample(range(1, 40), 5)), bonus=[],
             draw_id=f"d{i}", metadata={}, date=day + timedelta(days=i))
        for i in range(n)
    ]


class _FakeTicket:
    def __init__(self, primary, contributions):
        self.primary = primary
        self.bonus = []
        self.strategy_used = "ensemble"
        self.weights_used = {"frequency": 0.6, "hot_cold": 0.4}
        self.strategy_contributions = contributions
        self.metrics = {}


class _FrequencyEngine:
    """Picks the most frequent numbers in exactly the history it is given,
    and remembers every history it saw (for the no-peeking assertions)."""

    def __init__(self):
        self.seen = []

    def predict(self, game, history=None, use_nn=True):
        history = list(history or [])
        self.seen.append([list(d.primary) for d in history])
        counts = Counter(n for d in history for n in d.primary)
        picks = [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
        picks = sorted(picks)
        return _FakeTicket(picks, {"frequency": picks[:3], "hot_cold": picks[3:]})


def _run(draws, engine, tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("RETRO_DIR", str(tmp_path / "retro"))
    kwargs.setdefault("warmup", 10)
    return run_retro_backtest(GAME, draws=draws, engine=engine, **kwargs)


def test_no_peeking_engine_sees_only_the_past(tmp_path, monkeypatch):
    draws = _draws()
    engine = _FrequencyEngine()
    summary = _run(draws, engine, tmp_path, monkeypatch, tail_reserve=20)
    indices = list(range(10, 80))
    assert summary["trials"] == len(indices)
    for seen, i in zip(engine.seen, indices):
        assert seen == [list(d.primary) for d in draws[:i]]


def test_no_peeking_future_draws_cannot_change_past_trials(tmp_path, monkeypatch):
    draws = _draws()
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch,
         tail_reserve=0, include_tail=True, run_id="before")
    mutated = _draws()
    for i in range(61, len(mutated)):
        mutated[i] = Draw(primary=[1, 2, 3, 4, 5], bonus=[], draw_id=f"d{i}",
                          metadata={}, date=mutated[i].date)
    _run(mutated, _FrequencyEngine(), tmp_path, monkeypatch,
         tail_reserve=0, include_tail=True, run_id="after")
    rows = read_simulated(GAME)
    by_run = {}
    for row in rows:
        by_run.setdefault(row["run_id"], {})[row["target_draw_id"]] = row
    for i in range(10, 61):
        before = by_run["before"][f"d{i}"]
        after = by_run["after"][f"d{i}"]
        assert before["numbers"] == after["numbers"]
        assert before["hits"] == after["hits"]


def test_grading_matches_the_target_draw(tmp_path, monkeypatch):
    draws = _draws()
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch, tail_reserve=20)
    by_id = {d.draw_id: d for d in draws}
    for row in read_simulated(GAME):
        target = by_id[row["target_draw_id"]]
        assert row["actual_numbers"] == list(target.primary)
        assert row["hits"] == len(set(row["numbers"]) & set(target.primary))
        assert row["matched_numbers"] == sorted(set(row["numbers"]) & set(target.primary))
        assert row["status"] == "graded"


def test_simulated_store_never_touches_live_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LEDGER_DIR", str(tmp_path / "ledger"))
    _run(_draws(), _FrequencyEngine(), tmp_path, monkeypatch, tail_reserve=20)
    assert Path(simulated_path(GAME)).exists()
    assert not Path(suggestions_path(GAME)).exists()
    for row in read_simulated(GAME):
        assert row["simulated"] is True
        assert row["source"] == "retro_backtest"


def test_tail_reserve_held_out_unless_included(tmp_path, monkeypatch):
    draws = _draws()
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch, tail_reserve=20, run_id="held")
    held = {r["target_draw_id"] for r in read_simulated(GAME) if r["run_id"] == "held"}
    assert held == {f"d{i}" for i in range(10, 80)}
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch,
         tail_reserve=20, include_tail=True, run_id="all")
    included = {r["target_draw_id"] for r in read_simulated(GAME) if r["run_id"] == "all"}
    assert included == {f"d{i}" for i in range(10, 100)}


def test_stride_and_max_trials_keep_the_most_recent(tmp_path, monkeypatch):
    draws = _draws()
    summary = _run(draws, _FrequencyEngine(), tmp_path, monkeypatch,
                   tail_reserve=0, include_tail=True, stride=5, max_trials=3)
    assert summary["trials"] == 3
    ids = [r["target_draw_id"] for r in read_simulated(GAME)]
    assert ids == ["d85", "d90", "d95"]


def test_report_math_credit_and_dedupe(tmp_path, monkeypatch):
    draws = _draws()
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch, tail_reserve=20, run_id="r1")
    # A second identical run doubles the stored rows but must not double
    # the unique evidence the report counts.
    _run(draws, _FrequencyEngine(), tmp_path, monkeypatch, tail_reserve=20, run_id="r2")
    report = retro_report(GAME, current_weights={"frequency": 0.6, "hot_cold": 0.4})
    assert report["simulated"] is True
    assert report["n_rows_stored"] == 140
    assert report["n_graded_unique"] == 70
    ticket = report["ticket_level"]
    rows = [r for r in read_simulated(GAME) if r["run_id"] == "r1"]
    assert ticket["trials"] == 70
    assert ticket["total_hits"] == sum(r["hits"] for r in rows)
    assert ticket["lift_vs_random"] is not None
    assert ticket["p_value_at_least_these_hits"] is not None
    credit = report["strategies"]
    freq_rows = rows  # every row carries a frequency contribution
    assert credit["frequency"]["entries"] == len(freq_rows)
    manual_hits = sum(len(set(r["strategy_contributions"]["frequency"]) & set(r["actual_numbers"]))
                      for r in freq_rows)
    assert credit["frequency"]["hits"] == manual_hits
    advisory = report["advisory_reweight"]
    assert advisory is not None
    assert advisory["applied"] is False
    assert advisory["basis"] == "simulated_retro_backtest"


def test_real_engine_end_to_end_on_synthetic_history(tmp_path, monkeypatch):
    from prediction.engine import LotteryPredictionEngine

    monkeypatch.setenv("RETRO_DIR", str(tmp_path / "retro"))
    engine = LotteryPredictionEngine(state_dir=str(tmp_path / "state"))
    summary = run_retro_backtest(
        GAME, draws=_draws(120), engine=engine,
        warmup=30, max_trials=10, tail_reserve=5)
    assert summary["trials"] == 10
    rows = read_simulated(GAME)
    assert len(rows) == 10
    for row in rows:
        assert len(row["numbers"]) == 5
        assert all(1 <= n <= 39 for n in row["numbers"])
        assert row["hits"] == len(set(row["numbers"]) & set(row["actual_numbers"]))
        assert row["strategy_contributions"]  # ensemble attribution present
