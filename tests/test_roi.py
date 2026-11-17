"""ROI levers: anti-popular filter, EV ranking, verified wheels, per-game bankroll."""

import sys
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config  # noqa: E402
from services.roi import bankroll  # noqa: E402
from services.roi.ev_ranking import ev_ranking, game_ev  # noqa: E402
from services.roi.popularity import anti_popular_adjust, popularity_score  # noqa: E402
from services.roi.wheeling import build_wheel, wheel_for_game  # noqa: E402


def test_popularity_flags_common_choices():
    rules = game_rules_from_config("take5")
    assert popularity_score([1, 2, 3, 4, 5], rules)["score"] >= 80
    assert popularity_score([7, 14, 21, 28, 35], rules)["score"] >= 25  # evenly spaced, multiples of 7
    assert popularity_score([4, 17, 26, 33, 38], rules)["score"] < 20


def test_anti_popular_only_for_pari_mutuel_and_keeps_a_valid_ticket():
    take5 = game_rules_from_config("take5")
    out = anti_popular_adjust("take5", [1, 2, 3, 4, 5], take5)
    assert out["applied"] is True and out["popularity"]["score"] < out["original_popularity"]["score"]
    assert len(set(out["numbers"])) == 5 and all(1 <= n <= 39 for n in out["numbers"])
    assert "unchanged" in out["note"] and "change the odds" in out["disclaimer"]
    fixed = anti_popular_adjust("pick10", list(range(1, 21)), game_rules_from_config("pick10"))
    assert fixed["applied"] is False and "never split" in fixed["note"]


def test_ev_ranking_values_and_order():
    assert game_ev("pick3")["ev_per_dollar"] == pytest.approx(0.5)
    assert game_ev("win4")["ev_per_dollar"] == pytest.approx(0.5)
    assert game_ev("take5")["ev_per_dollar"] == pytest.approx(0.5 / (1 - 0.10393), rel=1e-3)
    pick10 = game_ev("pick10")["ev_per_dollar"]
    assert pick10 == pytest.approx(0.4959, abs=0.002)
    pb = game_ev("powerball")
    assert pb["fixed_tiers_ev_per_play"] == pytest.approx(0.32, abs=0.01)
    assert game_ev("powerball", 500_000_000)["ev_per_dollar"] > pb["ev_per_dollar"]
    ranking = ev_ranking()
    values = [r["ev_per_dollar"] for r in ranking["ranking"]]
    assert values == sorted(values, reverse=True) and all(v < 1 for v in values)
    names = {r["game"] for r in ranking["not_ranked"]}
    assert {"cash4life", "quickdraw"} <= names


def test_wheel_guarantee_is_verified_exhaustively():
    pool = [1, 5, 9, 13, 17, 21, 25, 29, 33]
    wheel = build_wheel(pool, 5, 3, 4)
    assert wheel["guarantee_verified"] is True
    assert wheel["ticket_count"] < wheel["full_wheel_ticket_count"]
    tickets = [set(t) for t in wheel["tickets"]]
    for subset in combinations(pool, 4):
        assert any(len(t & set(subset)) >= 3 for t in tickets)
    full = build_wheel([1, 2, 3, 4, 5, 6], 5, 5, 6)
    assert full["ticket_count"] == 6 and full["guarantee_verified"] is True
    with pytest.raises(ValueError):
        build_wheel(list(range(1, 20)), 5, 3, 4)


def test_wheel_for_game_context():
    out = wheel_for_game("take5", [2, 6, 10, 14, 18, 22, 26, 30], 3, 4)
    assert out["cost"] == out["ticket_count"] * 1.0
    assert 0 < out["probability_condition_met"] < 0.05
    with pytest.raises(ValueError):
        wheel_for_game("pick3", [1, 2, 3, 4], 2, 3)
    with pytest.raises(ValueError):
        wheel_for_game("take5", [1, 2, 3, 4, 99], 2, 3)


def test_bankroll_is_per_game(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    bankroll.record("take5", 10, 4, on="2026-10-20")
    bankroll.record("take5", 5, 0, on="2026-10-21")
    bankroll.record("powerball", 2, 0)
    take5 = bankroll.summary("take5", ev_per_dollar=0.558)
    assert take5["entries"] == 2 and take5["total_staked"] == 15 and take5["net"] == -11
    assert take5["roi_percent"] == pytest.approx(-73.33)
    assert take5["expected_net"] == pytest.approx(15 * (0.558 - 1), abs=0.01)
    assert bankroll.summary("powerball")["entries"] == 1
    assert bankroll.summary("pick3")["entries"] == 0
    with pytest.raises(ValueError):
        bankroll.record("take5", -1)


def test_roi_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routes import roi

    app = FastAPI()
    app.include_router(roi.router)
    client = TestClient(app)
    ranking = client.get("/api/roi/ev_ranking", params={"jackpots": "powerball:267000000"}).json()
    assert ranking["ranking"][0]["ev_per_dollar"] > 0 and "change the odds" in ranking["disclaimer"]
    assert client.get("/api/roi/ev_ranking", params={"jackpots": "powerball:abc"}).status_code == 400
    filtered = client.post("/api/roi/anti_popular", json={"game": "take5", "numbers": [1, 2, 3, 4, 5]}).json()
    assert filtered["applied"] is True
    assert client.post("/api/roi/anti_popular", json={"game": "take5", "numbers": [1, 2]}).status_code == 400
    body = {"game": "take5", "pool": [1, 5, 9, 13, 17, 21, 25], "guarantee": 3, "if_match": 4}
    wheel = client.post("/api/roi/wheel", json=body).json()
    assert wheel["guarantee_verified"] is True
    added = client.post("/api/roi/bankroll", json={"game": "take5", "stake": 5, "winnings": 1}).json()
    assert added["summary"]["net"] == -4
    overall = client.get("/api/roi/bankroll").json()
    assert overall["net"] == -4 and [g["game"] for g in overall["games"]] == ["take5"]
