"""Shared types for the modular prediction engine."""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Draw:
    """One lottery draw: primary numbers and optional bonus numbers."""

    primary: list[int]
    bonus: list[int] = field(default_factory=list)
    draw_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def all_numbers(self) -> list[int]:
        return list(self.primary) + list(self.bonus)

    @property
    def draw_date(self) -> date | None:
        raw = self.metadata.get("draw_date")
        if isinstance(raw, date):
            return raw
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw)
            except (ValueError, TypeError):
                pass
        return None


@dataclass
class GameRules:
    """Number-picking rules derived from GAME_CONFIGS."""

    game: str
    primary_count: int
    primary_min: int
    primary_max: int
    primary_unique: bool
    bonus_count: int = 0
    bonus_min: int = 1
    bonus_max: int = 99

    @property
    def primary_universe_size(self) -> int:
        return (self.primary_max - self.primary_min) + 1


@dataclass
class StrategyOutput:
    """Output from a strategy or agent: scores over the number universe."""

    name: str
    # Index i -> score for number (primary_min + i)
    scores: list[float]
    picks: list[int] | None = None
    bonus_scores: list[float] | None = None
    bonus_picks: list[int] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PredictionTicket:
    """Final blended prediction for one draw."""

    game: str
    primary: list[int]
    bonus: list[int] = field(default_factory=list)
    strategy_contributions: dict[str, list[int]] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class WeightState:
    """Persisted ensemble weights for a game."""

    game: str
    weights: dict[str, float]
    updated_at: float = 0.0
    last_draw_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)