"""Dynamic pattern search: auto-rerun, debounce, adaptive windows + FDR family, drift,
promote / demote loop, config, and API status."""

import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.chain import champion_store  # noqa: E402
from prediction.chain.stats import benjamini_hochberg  # noqa: E402
from prediction.patterns import auto  # noqa: E402
from prediction.patterns.search import (  # noqa: E402
    FIXED_BASES, WINDOWED_BASES, candidate_signals, drift, parse_signal, run_pattern_search)
from prediction.patterns.settings import load_settings  # noqa: E402
from services import pattern_scheduler  # noqa: E402
from test_pattern_search import make_history  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PREDICTION_STATE_DIR", str(tmp_path / "prediction"))
    yield tmp_path
    pattern_scheduler.set_scheduler(None)


class Recorder:
    def __init__(self, hold: threading.Event | None = None):
        self.calls, self.hold = [], hold

    def __call__(self, game, trigger, limit):
        self.calls.append((game, trigger))
        if self.hold is not None:
            self.hold.wait(5)


# ------------------------------------------------------------ 1. auto-rerun + debounce

def test_ingest_adding_draws_triggers_one_background_rerun(state, monkeypatch):
    monkeypatch.setenv("PATTERN_RERUN_DEBOUNCE_SECONDS", "0.05")
    rec = Recorder()
    sched = pattern_scheduler.PatternScheduler(runner=rec)
    pattern_scheduler.set_scheduler(sched)
    assert pattern_scheduler.notify_ingest("take5", {"added": 0}) is False  # nothing new -> no scan
    assert pattern_scheduler.notify_ingest("take5", {"added": 4}) is True
    assert sched.wait_idle("take5", 5)
    assert rec.calls == [("take5", "ingest:+4")]
    monkeypatch.setenv("PATTERN_AUTO_ENABLED", "0")
    assert pattern_scheduler.notify_ingest("take5", {"added": 4}) is False


def test_fetch_and_sync_notifies_the_scheduler(state, monkeypatch):
    from services.ingest import ingest_service

    seen = []
    monkeypatch.setattr(ingest_service, "_fetch_and_sync", lambda *a, **k: {"status": "success", "added": 7})
    monkeypatch.setattr(pattern_scheduler, "notify_ingest", lambda game, result, source="ingest": seen.append(
        (game, result["added"])))
    assert ingest_service.fetch_and_sync("take5")["added"] == 7
    assert seen == [("take5", 7)]


def test_debounce_collapses_bursts_and_reruns_once_after_a_running_scan(state, monkeypatch):
    monkeypatch.setenv("PATTERN_RERUN_DEBOUNCE_SECONDS", "0.2")
    hold = threading.Event()
    rec = Recorder(hold)
    sched = pattern_scheduler.PatternScheduler(runner=rec)
    for _ in range(5):
        sched.notify_ingest("take5", 3)
        time.sleep(0.02)
    assert sched.status("take5")["scan_status"] == "running" and rec.calls == []  # still debouncing
    deadline = time.time() + 3
    while not rec.calls and time.time() < deadline:
        time.sleep(0.02)
    assert len(rec.calls) == 1
    sched.notify_ingest("take5", 2)  # lands mid-run -> pending, not a second concurrent scan
    sched.notify_ingest("take5", 2)
    hold.set()
    assert sched.wait_idle("take5", 5)
    assert len(rec.calls) == 2
    sched.notify_ingest("powerball", 1)  # games are scheduled independently
    assert sched.wait_idle("powerball", 5)
    assert [c[0] for c in rec.calls] == ["take5", "take5", "powerball"]


# ------------------------------------------------------------ 2. adaptive windows + FDR family

def test_every_window_counts_in_the_fdr_family():
    report = run_pattern_search("take5", make_history(400, seed=3), windows=[25, 50, "all", 5000])
    assert report["adaptive"] and report["status"] == "ok"
    tested = [r for r in report["signals"] if r["status"] != "not_applicable"]
    assert report["family_size"] == report["tested"] == len(tested)
    windows = {r["window"] for r in tested if r["window"] is not None}
    assert windows == {25, 50, "all"}  # 5000 > draws available before validation: dropped
    skipped = len(report["signals"]) - len(tested)
    assert len(report["signals"]) == len(FIXED_BASES) + len(WINDOWED_BASES) * 3
    assert len(tested) == len(FIXED_BASES) + len(WINDOWED_BASES) * 3 - skipped
    q, _ = benjamini_hochberg([r["validation"]["p_value"] for r in tested], report["fdr_q"])
    assert np.allclose([r["q_value"] for r in tested], q, atol=2e-3)  # q uses the full family size
    assert report["replicates"] >= 4 * len(tested) / report["fdr_q"] - 1
    assert report["passed_holdout"] == []  # fair draws: the wider search finds nothing
    assert set(report["best_params"]) <= set(WINDOWED_BASES)


def test_adaptive_search_still_finds_a_planted_bias():
    report = run_pattern_search("take5", make_history(500, seed=5, bias=True), windows=[50, 200, "all"],
                                fdr_q=0.10)
    hot = [s for s in report["passed_holdout"] if parse_signal(s)[0] == "hot"]
    assert hot, report["passed_holdout"]


def test_candidates_cap_windows_and_parse_back():
    names = candidate_signals([25, 100, "all"], available_prior=60)
    assert "hot_w25" in names and "hot_wall" in names and "hot_w100" not in names
    assert parse_signal("pair_cooccurrence_wall") == ("pair_cooccurrence", 0)
    assert parse_signal("positional") == ("positional", 200)  # legacy names keep working


def test_drift_flags_strengthening_and_fading():
    n = 300
    expected = np.full(n, 0.5)
    rising = {"hits": np.r_[np.zeros(n // 2), np.ones(n // 2)], "expected": expected}
    falling = {"hits": np.r_[np.ones(n // 2), np.zeros(n // 2)], "expected": expected}
    flat = {"hits": np.tile([0.0, 1.0], n // 2), "expected": expected}
    assert drift(rising)["trend"] == "strengthening"
    assert drift(falling)["trend"] == "fading"
    assert drift(flat)["trend"] == "stable"
    assert drift({"hits": np.ones(10), "expected": np.ones(10)})["trend"] == "insufficient"
    report = run_pattern_search("take5", make_history(400, seed=3), windows=[50])
    assert all("drift" in r for r in report["signals"] if r["status"] != "not_applicable")


# ------------------------------------------------------------ 3. promote / demote loop

def _report(signal, passed):
    return {"status": "ok", "feature_signals": [signal] if passed else [],
            "signals": [{"signal": signal, "status": "validation_only" if passed else "not_significant",
                         "passed_validation": passed}]}


def test_promotes_only_on_a_holdout_win(state):
    history = make_history(300, seed=1)
    settings = load_settings("take5")
    calls = []

    def winner(game, hist, features, cfg):
        calls.append(features)
        return {"status": "ok", "promoted": True, "selected_variant": {"name": "v_pat1", "patterns": list(features)},
                "holdout": {"mean_hit_diff_chained_minus_incumbent": 0.2, "paired_permutation_p_value": 0.01},
                "decision_reason": "won"}

    out = auto.apply_auto_promotion("take5", history, _report("hot_w50", True), settings, challenger=winner)
    assert out["promoted"] == ["hot_w50"] and calls == [("hot_w50",)]
    assert "hot_w50" in auto.load_promoted("take5")["signals"]
    assert champion_store.current_champion("take5")[0] == champion_store.CHAINED
    events = auto.read_events("take5")
    assert events[-1]["type"] == "promote" and events[-1]["signals"] == ["hot_w50"]
    # Already promoted: not re-challenged on the next scan.
    again = auto.apply_auto_promotion("take5", history, _report("hot_w50", True), settings, challenger=winner)
    assert again["challenged"] == [] and len(calls) == 1


def test_validation_survivor_without_holdout_win_is_rejected(state):
    history = make_history(300, seed=1)
    loser = lambda *a: {"status": "ok", "promoted": False, "selected_variant": {"patterns": ["hot_w50"]},  # noqa: E731
                        "holdout": {}, "decision_reason": "Not promoted: holdout"}
    out = auto.apply_auto_promotion("take5", history, _report("hot_w50", True), load_settings("take5"),
                                    challenger=loser)
    assert out["promoted"] == [] and out["rejected"] == ["hot_w50"]
    assert auto.load_promoted("take5")["signals"] == {}
    assert champion_store.current_champion("take5")[0] == champion_store.INCUMBENT
    assert auto.read_events("take5")[-1]["type"] == "rejected"


def test_promoted_signal_failing_on_new_draws_is_demoted(state, monkeypatch):
    monkeypatch.setenv("PATTERN_DEMOTE_MIN_NEW_DRAWS", "30")
    history = make_history(300, seed=2)
    champion_store.save("take5", {"champion": champion_store.CHAINED,
                                  "champion_variant": {"name": "v_pat1", "patterns": ["hot_w50"]}})
    auto.save_promoted("take5", {"signals": {"hot_w50": {"marker": auto.draw_marker(history[249])}}})
    # Only 20 new draws: too early to judge.
    early = auto.apply_auto_promotion("take5", history[:270], _report("hot_w50", False), load_settings("take5"))
    assert early["demoted"] == [] and "hot_w50" in auto.load_promoted("take5")["signals"]
    out = auto.apply_auto_promotion("take5", history, _report("hot_w50", False), load_settings("take5"))
    assert out["demoted"] == ["hot_w50"]
    assert auto.load_promoted("take5")["signals"] == {}
    assert champion_store.current_champion("take5")[0] == champion_store.INCUMBENT
    event = auto.read_events("take5")[-1]
    assert event["type"] == "demote" and event["new_draws"] == 50 and event["champion_reset"] is True


def test_events_are_append_only_and_per_game(state):
    auto.append_event("take5", {"type": "promote", "signals": ["a"]})
    auto.append_event("powerball", {"type": "demote", "signal": "b"})
    auto.append_event("take5", {"type": "demote", "signal": "a"})
    assert [e["type"] for e in auto.read_events("take5")] == ["promote", "demote"]
    assert [e["type"] for e in auto.read_events("powerball")] == ["demote"]


# ------------------------------------------------------------ 4. config

def test_settings_from_env_with_per_game_override(monkeypatch):
    monkeypatch.setenv("PATTERN_WINDOWS", "50,all")
    monkeypatch.setenv("PATTERN_WINDOWS_TAKE5", "25,100")
    monkeypatch.setenv("PATTERN_FDR_Q", "0.2")
    monkeypatch.setenv("PATTERN_MIN_DRAWS", "200")
    monkeypatch.setenv("PATTERN_RERUN_DEBOUNCE_SECONDS", "5")
    assert load_settings("take5").windows == (25, 100)
    pb = load_settings("powerball")
    assert pb.windows == (50, 0) and pb.fdr_q == 0.2 and pb.min_draws == 200 and pb.debounce_seconds == 5
    monkeypatch.delenv("PATTERN_FDR_Q")
    assert load_settings("powerball").fdr_q == 0.10 and load_settings().enabled is True


# ------------------------------------------------------------ 5. API status

def test_api_reports_fresh_running_stale_and_history(state, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import patterns

    monkeypatch.setenv("PATTERN_AUTO_PROMOTE", "0")
    monkeypatch.setenv("PATTERN_WINDOWS", "50,all")
    count = {"n": 300}
    history = make_history(300, seed=4)
    hold = threading.Event()

    def runner(game, trigger, limit):
        hold.wait(5)
        pattern_scheduler.run_scan(game, history=history, count_fn=lambda g: count["n"], trigger=trigger)

    sched = pattern_scheduler.PatternScheduler(runner=runner, count_fn=lambda g: count["n"])
    pattern_scheduler.set_scheduler(sched)
    client = TestClient(FastAPI())
    client.app.include_router(patterns.router)
    get = lambda **p: client.get("/api/pattern_report", params={"game": "take5", **p}).json()  # noqa: E731
    first = get()
    assert first["status"] == "pending" and first["scan_status"] == "running"  # never an error while running
    hold.set()
    assert sched.wait_idle("take5", 120)
    fresh = get()
    assert fresh["scan_status"] == "fresh" and fresh["status"] == "ok" and fresh["last_run_at"]
    assert fresh["windows"] == [50, "all"] and fresh["config"]["fdr_q"] == 0.10
    assert all(r.get("drift", {}).get("trend") for r in fresh["signals"] if r["status"] != "not_applicable")
    count["n"] = 305  # an ingest added draws but no rerun yet
    assert get()["scan_status"] == "stale"
    hold.clear()
    forced = get(refresh=1)
    assert forced["scan_status"] == "running" and forced["status"] == "ok"  # last result stays visible
    hold.set()
    assert sched.wait_idle("take5", 120)
    auto.append_event("take5", {"type": "promote", "signals": ["hot_w50"]})
    done = get()
    assert done["scan_status"] == "fresh" and done["history"][-1]["type"] == "promote"
