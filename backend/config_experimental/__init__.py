"""
Configuration package initialization for PocketPro:NYL.
Exposes game configurations, endpoints, titles, aliases, and resolution utilities.
"""

try:
    from .settings import (
        GAME_CONFIGS,
        DATASET_ENDPOINTS,
        GAME_TITLES,
        GAME_ALIASES,
        resolve_game_key,
    )
except ImportError:
    # Fallback definitions to prevent import crashes during initial container boot or testing
    GAME_CONFIGS = {}
    DATASET_ENDPOINTS = {}
    GAME_TITLES = {}
    GAME_ALIASES = {}
    
    def resolve_game_key(key: str) -> str:
        return key

__all__ = [
    "GAME_CONFIGS",
    "DATASET_ENDPOINTS",
    "GAME_TITLES",
    "GAME_ALIASES",
    "resolve_game_key",
]