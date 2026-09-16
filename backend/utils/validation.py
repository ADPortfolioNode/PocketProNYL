"""
Validation utilities for game keys and model metadata.
"""
from config import GAME_CONFIGS, resolve_game_key


def _require_game_key(raw_game: str) -> str:
    """
    Validate and resolve a game key from user input.
    Raises ValueError if the game is not recognized.
    """
    try:
        resolved = resolve_game_key(raw_game)
    except ValueError as e:
        raise ValueError(f"Invalid game '{raw_game}'. Available games: {list(GAME_CONFIGS.keys())}") from e
    if not resolved:
        raise ValueError(f"Invalid game '{raw_game}'. Available games: {list(GAME_CONFIGS.keys())}")
    return resolved