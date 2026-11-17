"""NY game-rule facts: Mega Ball 1-24, Millionaire for Life nightly, Cash4Life retired."""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from config import (  # noqa: E402
    GAME_CONFIGS,
    GAME_PREDICTION_FORMATS,
    GAME_PREDICTION_SCHEDULES,
    is_game_retired,
)
from prediction.core.ensemble import history_bonus_picks  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from prediction.config.loader import game_rules_from_config  # noqa: E402
from utils.game_validation import HISTORICAL_RANGE_OVERRIDES, validate_game_record  # noqa: E402


def test_mega_ball_current_range_is_1_to_24():
    assert GAME_CONFIGS["megamillions"]["bonus_min"] == 1
    assert GAME_CONFIGS["megamillions"]["bonus_max"] == 24
    assert GAME_PREDICTION_FORMATS["megamillions"]["bonus_max"] == 24


def test_old_mega_ball_25_draws_still_validate_in_storage():
    assert HISTORICAL_RANGE_OVERRIDES["megamillions"]["bonus_max"] >= 25
    ok, reason = validate_game_record(
        "megamillions",
        {"draw_date": "2024-06-04", "winning_numbers": "05 12 33 41 60", "mega_ball": "25"},
    )
    assert ok, reason


def test_mega_ball_25_is_never_suggested():
    rules = game_rules_from_config("megamillions")
    history = [Draw(primary=[1, 2, 3, 4, 5], bonus=[25]) for _ in range(30)]
    history += [Draw(primary=[1, 2, 3, 4, 5], bonus=[7])]
    assert history_bonus_picks(history, rules) == [7]


def test_millionaire_for_life_draws_nightly_at_2315():
    schedule = GAME_PREDICTION_SCHEDULES["millionaireforlife"]
    assert schedule["daily_draws"] == 1
    assert not schedule.get("weekday_draws")
    from services.draw_schedule import next_draw, lottery_timezone

    tz = lottery_timezone()
    for day in range(19, 26):  # one full week, every weekday has a draw
        now = datetime(2026, 10, day, 12, 0, tzinfo=tz)
        nxt = next_draw("millionaireforlife", now)
        assert nxt["draw_date"] == now.date().isoformat()
        assert nxt["draw_time"].startswith(f"{now.date().isoformat()}T23:15")


def test_cash4life_is_retired_but_kept():
    assert "cash4life" in GAME_CONFIGS
    assert is_game_retired("cash4life")
    assert GAME_CONFIGS["cash4life"]["retired_date"] == "2026-02-21"
    assert not any(is_game_retired(g) for g in GAME_CONFIGS if g != "cash4life")


def test_catalog_marks_only_cash4life_retired(monkeypatch):
    import utils.game_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "_draw_counts_for", lambda games, refresh=False: {})
    catalog = catalog_mod.get_game_catalog()
    assert catalog["retired_games"] == ["cash4life"]
    entry = next(e for e in catalog["catalog"] if e["key"] == "cash4life")
    assert entry["retired"] is True and entry["status_label"] == "Retired: history only"
    other = next(e for e in catalog["catalog"] if e["key"] == "powerball")
    assert other["retired"] is False and other["status_label"] is None


def test_retired_prediction_carries_label():
    from routes.predictions import _retired_fields

    assert _retired_fields("cash4life")["status_label"] == "Retired: history only"
    assert _retired_fields("powerball") == {}


def test_history_bonus_skips_main_numbers_for_shared_drum():
    rules = game_rules_from_config("nylotto")
    history = [Draw(primary=[1, 2, 3, 4, 5, 6], bonus=[9]) for _ in range(5)]
    history += [Draw(primary=[1, 2, 3, 4, 5, 6], bonus=[12])]
    assert history_bonus_picks(history, rules, exclude=[9]) == [12]
    assert history_bonus_picks([], rules) == []
