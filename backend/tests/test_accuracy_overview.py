"""All-games overview + pooled bonus balls (no network, no live Chroma).

Every game gets injected draws (empty for the games without fixtures),
so nothing here touches a live loader.
"""

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import Draw  # noqa: E402
from services.accuracy_overview import build_accuracy_overview  # noqa: E402
from services.accuracy_report import pooled_stats  # noqa: E402

PMF = [0.64, 0.30, 0.055, 0.004, 0.001, 0.0]


def _graded_row(numbers, actual_date, session, hits, bonus_hit, recent_k="10"):
    return {
        "status": "graded",
        "numbers": numbers,
        "actual_draw_date": actual_date,
        "actual_draw_session": session,
        "hits": hits,
        "bonus_hit": bonus_hit,
        "expected_hits_by_chance": 0.357,
        "hit_distribution_by_chance": PMF,
        "tier": {},
        "model": "ensemble",
        "recent_k": recent_k,
    }


def test_pooled_bonus_balls():
    rows = [_graded_row([1, 2, 3, 4, 5], f"2026-10-{20 + i}", "evening", 1,
                        i < 3) for i in range(10)]
    pooled = pooled_stats(rows, "megamillions")
    assert pooled["graded"] == 10
    bonus = pooled["bonus"]
    assert bonus["n"] == 10
    assert bonus["hits"] == 3
    assert bonus["expected_hits"] == round(10 / 24, 4)
    # Binomial P(X >= 3; n=10, p=1/24) ≈ 0.0066.
    assert 0.005 < bonus["p_value"] < 0.009


def test_pooled_bonus_absent_without_bonus_games():
    rows = [_graded_row([1, 2, 3, 4, 5], "2026-10-21", "evening", 2, None)]
    assert pooled_stats(rows, "take5")["bonus"] is None


def test_overview_hold_and_breakdowns(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    draws = [
        Draw(primary=[1, 2, 3, 33, 34], date=date(2026, 10, 21),
             metadata={"draw_session": "evening"}),
        Draw(primary=[9, 9, 9, 9, 9], date=date(2026, 10, 22),
             metadata={"draw_session": "evening"}),
    ]
    entries = [
        {"primary": [1, 2, 3, 4, 5], "bonus": [],
         "served_at": "2026-10-21T10:00:00-04:00", "served_at_epoch": 1793172000,
         "target_draw_date": "2026-10-21", "target_draw_session": "evening",
         "model": "ensemble", "recent_k": 10,
         "strategy_weights": {}, "strategy_contributions": {}},
        {"primary": [1, 2, 3, 4, 5], "bonus": [],
         "served_at": "2026-10-22T10:00:00-04:00", "served_at_epoch": 1793258400,
         "target_draw_date": "2026-10-22", "target_draw_session": "evening",
         "model": "ensemble", "recent_k": 5,
         "strategy_weights": {}, "strategy_contributions": {}},
    ]
    games = ["take5", "pick3", "powerball", "megamillions", "pick10",
             "cash4life", "nylotto", "numbers", "win4", "millionaireforlife"]
    overview = build_accuracy_overview(
        entries_by_game={"take5": entries, **{g: [] for g in games[1:]}},
        draws_by_game={"take5": draws, **{g: [] for g in games[1:]}},
        weights_by_game={g: {} for g in games},
    )
    take5 = overview["games"]["take5"]
    assert take5["graded_unique"] == 2
    assert take5["reweight_verdict"] == "hold"
    assert take5["by_model"]["ensemble"]["n"] == 2
    assert take5["by_recent_k"]["10"]["n"] == 1
    assert take5["by_recent_k"]["5"]["n"] == 1
    assert take5["by_model"]["ensemble"]["mean_hits"] == 1.5
    assert overview["games"]["quickdraw"]["excluded"] is True
    assert overview["ready_to_reweight"] == []
    assert overview["total_graded_unique"] == 2
