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
