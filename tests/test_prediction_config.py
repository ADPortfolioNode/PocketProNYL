"""Tests for prediction config loading."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.config.loader import game_rules_from_config, list_configured_games, load_game_config
from utils.game_data_parser import _extract_primary_candidate, _extract_record_sequence


def test_list_configured_games():
    games = list_configured_games()
    assert "take5" in games
    assert "pick3" in games
    assert "powerball" in games
    assert len(games) >= 5


def test_load_win4_config():
    cfg = load_game_config("win4")
    assert cfg.game == "win4"
    assert "last_digit" in cfg.enabled_agents
    assert cfg.metrics.backtest_window >= 200
    assert sum(cfg.ensemble.initial_weights.values()) == pytest.approx(1.0, abs=0.05)


def test_load_take5_config():
    cfg = load_game_config("take5")
    assert cfg.game == "take5"
    assert "frequency" in cfg.enabled_strategies
    assert cfg.ensemble.enabled is True
    assert cfg.metrics.max_test_draws == 80
    assert sum(cfg.ensemble.initial_weights.values()) == pytest.approx(1.0, abs=0.05)


def test_game_rules_from_config():
    rules = game_rules_from_config("pick3")
    assert rules.primary_count == 3
    assert rules.primary_min == 0
    assert rules.primary_max == 9
    assert rules.primary_unique is False


def test_win4_history_parses_source_fields():
    assert _extract_primary_candidate({"midday_win_4": "1234"}, "win4") == [1, 2, 3, 4]
    assert _extract_primary_candidate({"evening_win_4": "9876"}, "win4") == [9, 8, 7, 6]
    assert _extract_record_sequence({"midday_win_4": "0012"}, "win4") == [0, 0, 1, 2]
    assert _extract_primary_candidate({"winning_numbers": "1234"}, "win4") == [1, 2, 3, 4]
    assert _extract_primary_candidate({"winning_numbers": "1,2,3,4"}, "win4") == [1, 2, 3, 4]
    # Shared Daily Numbers payload: Pick 3 in winning_numbers, Win 4 in its own fields.
    assert _extract_primary_candidate(
        {"winning_numbers": "123", "midday_win_4": "0987"},
        "win4",
    ) == [0, 9, 8, 7]


def test_win4_draw_loader_reads_compact_history():
    from prediction.core.draw_loader import metadata_to_draw

    compact = metadata_to_draw(
        {"draw_date": "2026-09-11", "draw_session": "midday", "winning_numbers": "0012"},
        "win4",
        draw_id="win4-midday",
    )
    assert compact is not None
    assert compact.primary == [0, 0, 1, 2]
    assert compact.draw_date.isoformat() == "2026-09-11"

    csv = metadata_to_draw(
        {"draw_date": "2026-09-11", "draw_session": "evening", "winning_numbers": "9,8,7,6"},
        "win4",
        draw_id="win4-evening",
    )
    assert csv is not None
    assert csv.primary == [9, 8, 7, 6]

    source = metadata_to_draw(
        {"draw_date": "2026-09-11", "midday_win_4": "4567", "evening_daily": "123"},
        "win4",
    )
    assert source is not None
    assert source.primary == [4, 5, 6, 7]