"""Real-world accuracy: append-only ledger, grader, exact stats, report and routes."""

import json
import sys
from datetime import date, datetime, timedelta
from itertools import product
from math import comb
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from services import accuracy_stats, game_odds  # noqa: E402
from services.accuracy_report import build_accuracy_report, pooled_stats  # noqa: E402
from services.draw_schedule import next_draw  # noqa: E402
from services.suggestion_grader import grade_entries  # noqa: E402
from services.suggestion_ledger import SuggestionLedger, suggestions_path  # noqa: E402

ET = ZoneInfo("America/New_York")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LEDGER_DIR", raising=False)
    return SuggestionLedger()


def _result(game, main, bonus=None):
    return {"status": "success", "game": game, "predicted_main_numbers": main,
            "predicted_bonus_numbers": bonus or [], "model_strategy": "ensemble",
            "engine": "modular", "strategy_weights": {"frequency": 1.0}}


# ---------------------------------------------------------------- stats ---

def test_hypergeometric_matches_brute_force():
    rules = game_rules_from_config("take5")
    pmf = accuracy_stats.hit_distribution([1, 2, 3, 4, 5], rules)
    assert sum(pmf) == pytest.approx(1.0)
    assert accuracy_stats.expected_hits(pmf) == pytest.approx(25 / 39)
    assert pmf[5] == pytest.approx(1 / comb(39, 5))


def test_digit_game_distribution_is_exact():
    rules = game_rules_from_config("pick3")
    pmf = accuracy_stats.hit_distribution([1, 2, 3], rules)
    brute = [0] * 4
    for outcome in product(range(10), repeat=3):
        brute[len({1, 2, 3} & set(outcome))] += 1
    assert pmf == pytest.approx([c / 1000 for c in brute])
    # A ticket with a repeated digit has only two distinct values.
    assert len(accuracy_stats.hit_distribution([7, 7, 1], rules)) == 3


def test_binomial_tail_and_pooled_p_value():
    tail = accuracy_stats.binomial_upper_tail(3, 10, 0.2)
    brute = sum(comb(10, i) * 0.2 ** i * 0.8 ** (10 - i) for i in range(3, 11))
    assert tail == pytest.approx(brute)
    pmf = [0.5, 0.5]
    assert accuracy_stats.pooled_total_p_value([pmf, pmf], 2) == pytest.approx(0.25)
    assert accuracy_stats.pooled_total_p_value([pmf, pmf], 0) == pytest.approx(1.0)


@pytest.mark.parametrize("game", ["powerball", "megamillions", "millionaireforlife", "take5", "nylotto", "pick10"])
def test_computed_odds_match_published_odds(game):
    rows = game_odds.computed_odds(game)
    assert rows
    for row in rows:
        assert row["one_in"] == pytest.approx(row["published_one_in"], rel=0.01), (game, row["tier"])


def test_prize_tiers():
    assert game_odds.tier_for("powerball", [1, 2, 3, 4, 5], [1, 2, 3, 9, 10], bonus_hit=True)["prize"] == 100
    assert game_odds.tier_for("powerball", [1, 2, 3, 4, 5], [1, 2, 3, 9, 10], bonus_hit=False)["prize"] == 7
    assert game_odds.tier_for("take5", [1, 2, 3, 4, 5], [6, 7, 8, 9, 10])["tier"] == "No prize"
    assert game_odds.tier_for("take5", [1, 2, 3, 4, 5], [1, 2, 3, 9, 10])["tier"] == "Match 3"
    assert game_odds.tier_for("win4", [1, 2, 3, 4], [1, 2, 3, 4])["tier"] == "Straight"
    box = game_odds.tier_for("pick3", [1, 2, 3], [3, 2, 1])
    assert box["tier"] == "Box (6-way)" and box["prize"] == 0 and box["box_prize_if_boxed"] == 80
    # A 20-number Pick 10 suggestion is not a Pick 10 play.
    assert game_odds.tier_for("pick10", list(range(1, 21)), list(range(1, 21)))["tier"] == "n/a"


# ------------------------------------------------------------- schedule ---

def test_next_draw_uses_sessions_and_draw_days():
    assert next_draw("take5", datetime(2026, 10, 21, 15, 0, tzinfo=ET)) == {
        "draw_date": "2026-10-21", "draw_session": "evening",
        "draw_time": "2026-10-21T22:30:00-04:00", "basis": "schedule_time"}
    assert next_draw("take5", datetime(2026, 10, 21, 23, 0, tzinfo=ET))["draw_session"] == "midday"
    # Tuesday -> Wednesday's Powerball at 22:59 ET.
    assert next_draw("powerball", datetime(2026, 10, 20, 12, 0, tzinfo=ET))["draw_date"] == "2026-10-21"
    # No published time: never target today.
    assert next_draw("pick10", datetime(2026, 10, 21, 1, 0, tzinfo=ET))["draw_date"] == "2026-10-22"


# --------------------------------------------------------------- ledger ---

def test_ledger_is_append_only_and_per_game(ledger, tmp_path):
    served = datetime(2026, 10, 21, 15, 0, tzinfo=ET)
    a = ledger.record_served("take5", _result("take5", [1, 2, 3, 4, 5]), source="test", now=served)
    b = ledger.record_served("pick3", _result("pick3", [1, 2, 3]), source="test", now=served)
    assert a and b and a["game"] == "take5" and b["game"] == "pick3"
    assert Path(suggestions_path("take5")).parent != Path(suggestions_path("pick3")).parent
    # A reply for another game is never recorded under this one.
    assert ledger.record_served("take5", _result("pick3", [1, 2, 3]), source="test") is None
    ledger.record_served("take5", _result("take5", [6, 7, 8, 9, 10]), source="test", now=served)
    with open(suggestions_path("take5"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"game": "pick3", "primary": [9, 9, 9]}) + "\n")
        handle.write("not json\n")
    take5 = ledger.read_suggestions("take5")
    assert [e["primary"] for e in take5] == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]]
    assert all(e["target_draw_date"] == "2026-10-21" and e["target_draw_session"] == "evening" for e in take5)
    assert take5[0]["model"] == "ensemble" and take5[0]["model_version"]
    assert [e["primary"] for e in ledger.read_suggestions("pick3")] == [[1, 2, 3]]
    with pytest.raises(ValueError):
        suggestions_path("../etc")


def _draw(game, numbers, day, session=None, bonus=None):
    meta = {"draw_session": session} if session else {}
    return Draw(primary=numbers, bonus=bonus or [], date=day, metadata=meta, draw_id=f"{game}-{day}-{session}")


def test_grader_matches_target_draw_only_and_blocks_leakage(ledger):
    served = datetime(2026, 10, 21, 15, 0, tzinfo=ET)
    ledger.record_served("take5", _result("take5", [1, 2, 3, 4, 5]), source="test", now=served)
    late = datetime(2026, 10, 21, 23, 0, tzinfo=ET)  # after the evening draw -> targets next midday
    ledger.record_served("take5", _result("take5", [1, 2, 3, 4, 5]), source="test", now=late)
    entries = ledger.read_suggestions("take5")
    day = date(2026, 10, 21)
    draws = [
        _draw("take5", [1, 2, 30, 31, 32], day, "midday"),
        _draw("take5", [1, 2, 3, 33, 34], day, "evening"),
    ]
    graded, _ = grade_entries("take5", entries, draws=draws)
    assert graded[0]["status"] == "graded"
    assert graded[0]["hits"] == 3 and graded[0]["matched_numbers"] == [1, 2, 3]
    assert graded[0]["actual_draw_session"] == "evening"
    assert graded[0]["tier"]["tier"] == "Match 3"
    assert graded[0]["p_value_at_least_these_hits"] == pytest.approx(
        sum(accuracy_stats.hit_distribution([1, 2, 3, 4, 5], game_rules_from_config("take5"))[3:]), rel=1e-4)
    assert graded[1]["status"] == "pending"  # next midday is not ingested yet

    # Tampered entry that claims a draw that had already happened is never graded.
    leaked = {**entries[0], "served_at": "2026-10-22T03:00:00+00:00"}
    graded, _ = grade_entries("take5", [leaked], draws=draws)
    assert graded[0]["status"] == "not_gradable"


def test_grader_loads_only_its_own_game(ledger):
    served = datetime(2026, 10, 21, 15, 0, tzinfo=ET)
    ledger.record_served("pick3", _result("pick3", [1, 2, 3]), source="test", now=served)
    asked = []

    def loader(game):
        asked.append(game)
        return {"pick3": [_draw("pick3", [1, 2, 3], date(2026, 10, 21), "evening")]}.get(game, [])

    graded, _ = grade_entries("pick3", ledger.read_suggestions("pick3"), loader=loader)
    assert asked == ["pick3"]
    assert graded[0]["tier"]["tier"] == "Straight"


def test_report_labels_simulated_until_real_history(ledger):
    served = datetime(2026, 10, 21, 15, 0, tzinfo=ET)
    for numbers in ([1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15], [16, 17, 18, 19, 20]):
        ledger.record_served("take5", _result("take5", numbers), source="test", now=served)
        served += timedelta(minutes=1)
    metrics = {"holdout_mean_partial_hits": 0.7, "holdout_random_baseline": {"mean_partial_hits": 0.64}}
    report = build_accuracy_report("take5", 3, ledger=ledger, draws=[], backtest_metrics=metrics)
    assert report["data_label"] == "Simulated"
    assert report["backtest"]["label"] == "Simulated"
    expected_order = [[16, 17, 18, 19, 20], [11, 12, 13, 14, 15], [6, 7, 8, 9, 10]]
    assert [s["numbers"] for s in report["suggestions"]] == expected_order
    assert all(s["status"] == "pending" for s in report["suggestions"])

    draws = [_draw("take5", [1, 6, 11, 16, 39], date(2026, 10, 21), "evening")]
    report = build_accuracy_report("take5", 3, ledger=ledger, draws=draws, backtest_metrics=metrics)
    assert report["data_label"] == "Real"
    pooled = report["pooled_last"]
    assert pooled["graded"] == 3 and pooled["total_hits"] == 3
    assert pooled["expected_total_hits_by_chance"] == pytest.approx(3 * 25 / 39, rel=1e-3)
    assert 0 < pooled["p_value_exact"] <= 1
    assert "far too few" in pooled["verdict"]
    assert report["pooled_all_time"]["graded"] == 4
    assert report["backtest"]["gap_real_minus_simulated"] == pytest.approx(1.0 - 0.7)


def test_pooled_stats_dedupes_repeated_serving():
    item = {"status": "graded", "target_draw_date": "2026-10-21", "target_draw_session": "evening",
            "numbers": [1, 2, 3, 4, 5], "hits": 2, "tier": {"wins": False},
            "hit_distribution_by_chance": accuracy_stats.hit_distribution(
                [1, 2, 3, 4, 5], game_rules_from_config("take5"))}
    assert pooled_stats([item, dict(item)], "take5")["graded"] == 1


# --------------------------------------------------------------- routes ---

@pytest.fixture
def client(ledger, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import accuracy as accuracy_routes
    from routes import predictions as prediction_routes

    monkeypatch.setenv("PREDICTION_ENGINE", "modular")
    monkeypatch.setattr(
        prediction_routes.prediction_adapter, "predict_next_draw",
        lambda game, recent_k=10: _result(game, [3, 9, 17, 25, 33]),
    )
    monkeypatch.setattr(prediction_routes.exp_store, "save_experiment", lambda record: None)
    import services.suggestion_grader as grader

    monkeypatch.setattr(grader, "default_draw_loader", lambda game, limit=3000: [])
    import services.accuracy_report as report_module

    monkeypatch.setattr(report_module, "_backtest_metrics", lambda game: None)
    app = FastAPI()
    app.include_router(prediction_routes.router)
    app.include_router(accuracy_routes.router)
    return TestClient(app)


def test_predict_persists_and_feedback_grades(client):
    served = client.post("/api/predict", json={"game": "take5"}).json()
    assert served["status"] == "COMPLETED" and served["ledger_id"]
    ledger_rows = client.get("/api/suggestion_ledger", params={"game": "take5"}).json()
    assert ledger_rows["total"] == 1 and ledger_rows["entries"][0]["primary"] == [3, 9, 17, 25, 33]

    report = client.get("/api/accuracy_report", params={"game": "take5", "last": 3}).json()
    assert report["status"] == "ok" and report["suggestions"][0]["status"] == "pending"
    assert "change the odds" in report["disclaimer"]

    entry = ledger_rows["entries"][0]
    feedback = client.post("/api/prediction/feedback", json={
        "game": "take5", "primary": [3, 9, 17, 1, 2], "draw_date": entry["target_draw_date"],
        "draw_session": entry["target_draw_session"],
    }).json()
    assert feedback["status"] == "ok" and feedback["weights_updated"] is False
    assert feedback["graded_suggestions"][0]["hits"] == 3

    bad = client.post("/api/prediction/feedback", json={"game": "take5", "primary": [1, 2, 3]})
    assert bad.status_code == 400
    assert client.get("/api/accuracy_report", params={"game": "nope"}).status_code == 400
    # pick3's report never sees take5's suggestion or reported draw.
    other = client.get("/api/accuracy_report", params={"game": "pick3"}).json()
    assert other["ledger_size"] == 0 and other["suggestions"] == []
