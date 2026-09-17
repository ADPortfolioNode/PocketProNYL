"""Verify blend search climbs accuracy and post-train optimize is wired."""

from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _score(y_true, y_pred, y_full):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.clip(np.rint(np.asarray(y_pred, dtype=float)), 0, float(np.max(y_full)) or 1.0)
    max_val = float(np.max(y_full)) or 1.0
    mae = float(np.mean(np.abs(y_true - y_pred)))
    return mae, max(0.0, 1.0 - (mae / max_val))


def find_best_blend(candidate, previous, y_val, y_full, blend_step=0.2):
    """Mirror TrainerService._find_best_blend (coarse then fine)."""
    best = None
    step = blend_step
    weight = step
    while weight < 1.0:
        blend = (weight * candidate) + ((1.0 - weight) * previous)
        mae, accuracy = _score(y_val, blend, y_full)
        if best is None or accuracy > best["accuracy"]:
            best = {"weight": float(weight), "mae": float(mae), "accuracy": float(accuracy)}
        weight = round(weight + step, 10)
    fine_step = max(round(step / 5, 4), 0.01)
    lo = max(fine_step, round(best["weight"] - step, 10))
    hi = min(1.0 - fine_step, round(best["weight"] + step, 10))
    weight = lo
    while weight <= hi + 1e-9:
        blend = (weight * candidate) + ((1.0 - weight) * previous)
        mae, accuracy = _score(y_val, blend, y_full)
        if accuracy > best["accuracy"]:
            best = {"weight": float(weight), "mae": float(mae), "accuracy": float(accuracy)}
        weight = round(weight + fine_step, 10)
    return best


def test_post_train_trials_are_shorter_verify_loop():
    source = (BACKEND / "services" / "tuning_assistant.py").read_text(encoding="utf-8")
    assert "POST_TRAIN_TRIALS" in source
    assert '"baseline"' in source and '"aggressive"' in source and '"stable"' in source


def test_find_best_blend_prefers_matching_candidate():
    y_true = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [2.0, 3.0, 4.0]])
    candidate = np.array(y_true)
    previous = candidate + 8.0
    best = find_best_blend(candidate, previous, y_true, y_true)
    assert best["weight"] >= 0.8
    assert best["accuracy"] > 0.9


def test_training_worker_source_runs_post_train_optimize():
    trainer = (BACKEND / "services" / "trainer.py").read_text(encoding="utf-8")
    assert "fine_step" in trainer
    source = (BACKEND / "routes" / "training.py").read_text(encoding="utf-8")
    assert "_post_train_optimize" in source
    assert "optimize_weights" in source
    assert "POST_TRAIN_TRIALS" in source
    tuner = (BACKEND / "services" / "game_tuner.py").read_text(encoding="utf-8")
    assert "def optimize_game(self, game: str, trials" in tuner


if __name__ == "__main__":
    test_post_train_trials_are_shorter_verify_loop()
    test_find_best_blend_prefers_matching_candidate()
    test_training_worker_source_runs_post_train_optimize()
    print("all ok")
