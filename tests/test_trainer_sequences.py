"""Regression test: the legacy trainer path parsed winning numbers through an
undefined name (flake8 F821), so it raised NameError on every row."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_extract_winning_sequences_parses_rows():
    from services.trainer import TrainerService

    trainer = TrainerService()
    rows = [
        {"draw_date": "2026-01-01", "evening_winning_numbers": "02 09 17 30 39"},
        {"draw_date": "2026-01-02", "evening_winning_numbers": "01 05 11 22 38"},
        "not-a-dict",
    ]
    sequences = trainer._extract_winning_sequences(rows, "take5")
    assert len(sequences) == 2
    assert all(len(seq) == 5 for seq in sequences)
    assert sequences[0] == [2, 9, 17, 30, 39]


def test_game_profiles_default_to_bounded_model_capacity():
    from utils.training_defaults import GAME_TRAINING_DEFAULTS, get_game_training_defaults

    assert GAME_TRAINING_DEFAULTS
    for game, profile in GAME_TRAINING_DEFAULTS.items():
        defaults = get_game_training_defaults(game)
        assert defaults["n_estimators"] <= 200
        assert defaults["max_depth"] <= 12
        assert defaults["train_size"] >= 0.30
        assert defaults["overfit_gap_limit_percent"] == 2.0
        assert profile["max_iterations"] <= 18
