"""Load and merge YAML prediction configs with GAME_CONFIGS."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from config import GAME_CONFIGS
from prediction.core.types import GameRules

_CONFIG_DIR = Path(__file__).resolve().parent
_GAMES_DIR = _CONFIG_DIR / "games"


class EnsembleConfig(BaseModel):
    enabled: bool = True
    initial_weights: dict[str, float] = Field(default_factory=dict)
    learning_rate: float = 0.05
    min_weight: float = 0.01


class NNConfig(BaseModel):
    enabled: bool = True
    backend: str = "lstm"
    lookback: int = 30
    blend_weight: float = 0.15
    hidden_layers: list[int] = Field(default_factory=lambda: [64, 32])
    max_iter: int = 200


class MetricsConfig(BaseModel):
    backtest_window: int = 200
    min_backtest_draws: int = 50
    random_trials: int = 500
    target_accuracy: float = 0.98
    verification_rounds: int = 1


class GamePredictionConfig(BaseModel):
    game: str
    enabled_strategies: list[str] = Field(default_factory=list)
    enabled_agents: list[str] = Field(default_factory=list)
    ensemble: EnsembleConfig = Field(default_factory=EnsembleConfig)
    nn: NNConfig = Field(default_factory=NNConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    strategy_params: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _synthesize_game_config(game_key: str) -> dict:
    """Build per-game defaults from GAME_CONFIGS when no YAML file exists."""
    cfg = GAME_CONFIGS[game_key]
    primary_count = int(cfg.get("primary_count", 5))
    bonus_count = int(cfg.get("bonus_count", 0) or 0)
    has_bonus = bonus_count > 0 or bool(cfg.get("embedded_bonus_in_winning_numbers"))
    is_digit_game = not bool(cfg.get("primary_unique", True)) and primary_count <= 5

    enabled_strategies = ["frequency", "delta", "hot_cold", "distribution"]
    enabled_agents = ["repeats", "sums", "gaps"]
    if has_bonus or is_digit_game:
        enabled_agents.append("last_digit")

    game_data: dict[str, Any] = {
        "game": game_key,
        "enabled_strategies": enabled_strategies,
        "enabled_agents": enabled_agents,
    }

    if primary_count >= 15:
        game_data["ensemble"] = {
            "initial_weights": {
                "frequency": 0.24,
                "delta": 0.18,
                "hot_cold": 0.18,
                "distribution": 0.14,
                "repeats": 0.10,
                "sums": 0.08,
                "gaps": 0.08,
            }
        }
        game_data["metrics"] = {"backtest_window": 500, "min_backtest_draws": 100}
        game_data["nn"] = {"blend_weight": 0.10}
    elif is_digit_game:
        game_data["ensemble"] = {
            "initial_weights": {
                "frequency": 0.25,
                "delta": 0.20,
                "hot_cold": 0.20,
                "distribution": 0.15,
                "repeats": 0.10,
                "sums": 0.05,
                "last_digit": 0.05,
            }
        }
    else:
        weights = {
            "frequency": 0.22,
            "delta": 0.18,
            "hot_cold": 0.18,
            "distribution": 0.12,
            "repeats": 0.10,
            "sums": 0.08,
            "gaps": 0.07,
        }
        if "last_digit" in enabled_agents:
            weights["last_digit"] = 0.05
        game_data["ensemble"] = {"initial_weights": weights}

    return game_data


@lru_cache(maxsize=32)
def load_game_config(game: str) -> GamePredictionConfig:
    """Merge defaults.yaml with per-game YAML; validate game exists in GAME_CONFIGS."""
    game_key = game.lower().strip()
    if game_key not in GAME_CONFIGS:
        raise ValueError(f"Unknown game '{game}'. Supported: {sorted(GAME_CONFIGS.keys())}")

    defaults = _load_yaml(_CONFIG_DIR / "defaults.yaml")
    game_path = _GAMES_DIR / f"{game_key}.yaml"
    game_data = _load_yaml(game_path) if game_path.exists() else _synthesize_game_config(game_key)
    merged = _deep_merge(defaults, game_data)
    merged["game"] = game_key
    return GamePredictionConfig.model_validate(merged)


def game_rules_from_config(game: str) -> GameRules:
    """Build GameRules from central GAME_CONFIGS."""
    cfg = GAME_CONFIGS.get(game, {}) or {}
    return GameRules(
        game=game,
        primary_count=int(cfg.get("primary_count", 5)),
        primary_min=int(cfg.get("primary_min", 1)),
        primary_max=int(cfg.get("primary_max", 99)),
        primary_unique=bool(cfg.get("primary_unique", True)),
        bonus_count=int(cfg.get("bonus_count", 0) or 0),
        bonus_min=int(cfg.get("bonus_min", 1)),
        bonus_max=int(cfg.get("bonus_max", 99)),
    )


def list_configured_games() -> list[str]:
    return sorted(GAME_CONFIGS.keys())


def config_dir() -> str:
    return str(_CONFIG_DIR)