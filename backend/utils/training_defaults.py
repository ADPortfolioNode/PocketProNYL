"""
Central training defaults for PocketPro:NYL.

Base knobs match ``TrainerService.get_training_defaults`` / env overrides.
Per-game profiles follow the Training Optimization Guide (complexity,
draw frequency, and number-space size).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Generic baseline (RandomForest / incremental trainer contract)
# ---------------------------------------------------------------------------

DEFAULT_TRAINING_CONFIG: Dict[str, Any] = {
    "target_accuracy": 0.90,
    "max_iterations": 40,
    "train_size": 0.25,
    "n_estimators": 250,
    "max_depth": 18,
    "random_state": 42,
    "blend_step": 0.05,
    "data_limit": 0,
    "window_size": 3,
    "auto_tune": True,
    "validation_size": None,
    "model_strategy": "ensemble",
}

# Env var names that TrainerService already honors
ENV_KEY_MAP: Dict[str, str] = {
    "target_accuracy": "TRAIN_TARGET_ACCURACY",
    "max_iterations": "TRAIN_MAX_ATTEMPTS",
    "blend_step": "TRAIN_BLEND_STEP",
    "train_size": "TRAIN_SIZE",
    "n_estimators": "TRAIN_N_ESTIMATORS",
    "max_depth": "TRAIN_MAX_DEPTH",
    "random_state": "TRAIN_RANDOM_STATE",
    "data_limit": "TRAIN_DATA_LIMIT",
    "window_size": "TRAIN_WINDOW_SIZE",
}

# ---------------------------------------------------------------------------
# Game-specific overrides (merged on top of DEFAULT_TRAINING_CONFIG)
# ---------------------------------------------------------------------------

GAME_TRAINING_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "pick3": {
        "target_accuracy": 0.85,
        "max_iterations": 30,
        "train_size": 0.30,
        "n_estimators": 150,
        "max_depth": 12,
        "window_size": 2,
        "blend_step": 0.06,
        "data_limit": 0,
        "reasoning": (
            "Pick 3 is high-frequency with a small digit space; use a lower "
            "target, smaller trees, and a short window to avoid overfitting noise."
        ),
    },
    "take5": {
        "target_accuracy": 0.88,
        "max_iterations": 35,
        "train_size": 0.25,
        "n_estimators": 200,
        "max_depth": 16,
        "window_size": 3,
        "blend_step": 0.05,
        "data_limit": 0,
        "reasoning": (
            "Take 5 is medium complexity with daily draws; balanced estimators "
            "and window capture recent patterns without excess depth."
        ),
    },
    "powerball": {
        "target_accuracy": 0.92,
        "max_iterations": 50,
        "train_size": 0.20,
        "n_estimators": 350,
        "max_depth": 22,
        "window_size": 5,
        "blend_step": 0.04,
        "data_limit": 0,
        "reasoning": (
            "Powerball has a large primary/bonus space and fewer draws; use more "
            "trees, deeper depth, and a longer window for sparse signal."
        ),
    },
    "megamillions": {
        "target_accuracy": 0.92,
        "max_iterations": 50,
        "train_size": 0.20,
        "n_estimators": 350,
        "max_depth": 22,
        "window_size": 5,
        "blend_step": 0.04,
        "data_limit": 0,
        "reasoning": (
            "Mega Millions matches Powerball complexity and low frequency; apply "
            "the same conservative, higher-capacity defaults."
        ),
    },
    "pick10": {
        "target_accuracy": 0.90,
        "max_iterations": 45,
        "train_size": 0.25,
        "n_estimators": 300,
        "max_depth": 20,
        "window_size": 4,
        "blend_step": 0.05,
        "data_limit": 0,
        "reasoning": (
            "Pick 10 selects many numbers from a wide field; extra estimators and "
            "a moderate window help model high-dimensional co-occurrence."
        ),
    },
    "cash4life": {
        "target_accuracy": 0.89,
        "max_iterations": 40,
        "train_size": 0.25,
        "n_estimators": 250,
        "max_depth": 18,
        "window_size": 3,
        "blend_step": 0.05,
        "data_limit": 0,
        "reasoning": (
            "Cash4Life is medium complexity with daily draws; keep near-baseline "
            "defaults with a slight accuracy/estimator balance."
        ),
    },
    "quickdraw": {
        "target_accuracy": 0.82,
        "max_iterations": 25,
        "train_size": 0.35,
        "n_estimators": 120,
        "max_depth": 10,
        "window_size": 1,
        "blend_step": 0.08,
        "data_limit": 100_000,
        "reasoning": (
            "Quick Draw is extremely high frequency; focus on the most recent "
            "window, cap data volume, and use a lower target to limit overfitting."
        ),
    },
    "nylotto": {
        "target_accuracy": 0.91,
        "max_iterations": 45,
        "train_size": 0.22,
        "n_estimators": 320,
        "max_depth": 20,
        "window_size": 4,
        "blend_step": 0.04,
        "data_limit": 0,
        "reasoning": (
            "NY Lotto draws less often with a medium-high number space; favor "
            "more estimators and a longer pattern window."
        ),
    },
}


def _merge_defaults(
    base: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = deepcopy(base)
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                merged[key] = value
    return merged


def get_base_training_defaults() -> Dict[str, Any]:
    """Return the generic trainer defaults (no game overlay)."""
    return deepcopy(DEFAULT_TRAINING_CONFIG)


def get_game_training_defaults(game: Optional[str] = None) -> Dict[str, Any]:
    """
    Return defaults for ``game``.

    Preference order:
    1. Rule-derived defaults from game config / schedule / suggestion format
    2. Optional static profile overlay from GAME_TRAINING_DEFAULTS
    3. Generic baseline when game is missing
    """
    if not game:
        return get_base_training_defaults()
    key = str(game).strip().lower()

    # Dynamic derivation from game rules + expected suggestion shape.
    try:
        from utils.game_catalog import compute_training_defaults_for_game

        derived = compute_training_defaults_for_game(key)
    except Exception:
        derived = get_base_training_defaults()
        derived["reasoning"] = f"Could not derive defaults for '{key}'; using generic baseline."
        derived["optimization_applied"] = False

    # Optional hand-tuned overlay (keeps guide profiles as soft preferences).
    profile = GAME_TRAINING_DEFAULTS.get(key)
    if profile:
        # Keep derived reasoning/metrics; allow explicit profile knobs to win.
        merged = _merge_defaults(derived, {k: v for k, v in profile.items() if k != "reasoning"})
        if profile.get("reasoning"):
            merged["reasoning"] = (
                f"{derived.get('reasoning', '')} Overlay: {profile['reasoning']}"
            ).strip()
        merged["optimization_applied"] = True
        return merged

    if key not in GAME_TRAINING_DEFAULTS and not derived.get("optimization_applied"):
        derived["reasoning"] = derived.get("reasoning") or (
            f"No game-specific profile for '{key}'; using generic defaults."
        )
    return derived


def compare_to_generic(game: str) -> Dict[str, Any]:
    """Build a per-parameter comparison vs generic defaults for UI panels."""
    generic = get_base_training_defaults()
    optimized = get_game_training_defaults(game)
    comparison: Dict[str, Any] = {}
    for key, generic_value in generic.items():
        if key in ("reasoning", "optimization_applied", "model_strategy"):
            continue
        specific = optimized.get(key)
        if specific is None or generic_value is None:
            continue
        if isinstance(generic_value, bool) or isinstance(specific, bool):
            comparison[key] = {
                "game_specific": specific,
                "generic": generic_value,
                "adjustment": "changed" if specific != generic_value else "same",
            }
            continue
        try:
            g = float(generic_value)
            s = float(specific)
        except (TypeError, ValueError):
            comparison[key] = {
                "game_specific": specific,
                "generic": generic_value,
                "adjustment": "changed" if specific != generic_value else "same",
            }
            continue
        diff_pct = ((s - g) / g * 100.0) if g != 0 else 0.0
        if abs(diff_pct) < 0.01:
            adjustment = "same"
        elif s > g:
            adjustment = "higher"
        else:
            adjustment = "lower"
        comparison[key] = {
            "game_specific": specific,
            "generic": generic_value,
            "difference_pct": round(diff_pct, 2),
            "adjustment": adjustment,
        }
    return comparison


class TrainingOptimizer:
    """
    Game-aware training parameter helper used by routes / UI optimization panels.
    """

    def __init__(self, base: Optional[Dict[str, Any]] = None):
        self.base = deepcopy(base or DEFAULT_TRAINING_CONFIG)

    def get_optimizer_params(self) -> Dict[str, Any]:
        """Secondary knobs (documented for RF blend / future NN paths)."""
        return {
            "blend_step": self.base.get("blend_step", 0.05),
            "auto_tune": bool(self.base.get("auto_tune", True)),
            "random_state": int(self.base.get("random_state", 42)),
            # Retained for docs / future gradient paths; RF trainer ignores these.
            "beta_1": 0.9,
            "beta_2": 0.999,
            "epsilon": 1e-7,
            "weight_decay": 0.01,
        }

    def get_defaults(self, game: Optional[str] = None) -> Dict[str, Any]:
        return get_game_training_defaults(game)

    def get_optimization_payload(self, game: str) -> Dict[str, Any]:
        optimized = get_game_training_defaults(game)
        return {
            "game": str(game).strip().lower(),
            "optimized_defaults": optimized,
            "generic_defaults": get_base_training_defaults(),
            "comparison": compare_to_generic(game),
            "optimization_applied": bool(optimized.get("optimization_applied", False)),
            "optimization_reasoning": optimized.get("reasoning") or "",
        }


game_training_optimizer = TrainingOptimizer()

__all__ = [
    "DEFAULT_TRAINING_CONFIG",
    "ENV_KEY_MAP",
    "GAME_TRAINING_DEFAULTS",
    "TrainingOptimizer",
    "compare_to_generic",
    "game_training_optimizer",
    "get_base_training_defaults",
    "get_game_training_defaults",
]
