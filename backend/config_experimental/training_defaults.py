# File Location: /app/config/training_defaults.py
"""
Compatibility shim — canonical implementation lives in ``utils.training_defaults``.
"""
from utils.training_defaults import (  # noqa: F401
    DEFAULT_TRAINING_CONFIG,
    ENV_KEY_MAP,
    GAME_TRAINING_DEFAULTS,
    TrainingOptimizer,
    compare_to_generic,
    game_training_optimizer,
    get_base_training_defaults,
    get_game_training_defaults,
)

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
