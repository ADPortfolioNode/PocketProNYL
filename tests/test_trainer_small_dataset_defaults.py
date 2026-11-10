"""Small datasets must train with the per-game profile, not an inflated forest.

Regression test for the _precision_first_defaults fallback: for <=1500
records it used to jump to 450-600 trees at depth 28-32, overriding the
game profile (pick3/win4: 100 trees, depth 8) — a forest that big on a
small dataset memorizes the training rows. The fallback now uses the game
profile from training_defaults.py as-is.
"""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def test_small_dataset_uses_game_profile_not_inflated_forest(monkeypatch, tmp_path):
    from services import chroma_client as chroma_client_module
    from services.trainer import TrainerService

    monkeypatch.setenv("PREDICTION_ENGINE", "legacy")
    trainer = TrainerService()
    trainer.models_dir = str(tmp_path / "models")
    os.makedirs(trainer.models_dir, exist_ok=True)
    # 100 records: a small dataset, with no stored candidates anywhere.
    monkeypatch.setattr(
        chroma_client_module.chroma_client, "count_documents", lambda game: 100
    )
    monkeypatch.setattr(
        trainer, "train_model",
        lambda game, progress_callback=None: {"status": "success", "accuracy": 0.5},
    )
    result = trainer._train_game("pick3")
    assert result["optimal_config"]["source"] == "precision_first_defaults"
    assert result["optimal_config"]["n_estimators"] == 100
    assert result["optimal_config"]["max_depth"] == 8
    assert trainer.n_estimators == 100
    assert trainer.max_depth == 8
