"""Persist per-game ensemble weights to JSON."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from prediction.core.types import WeightState


def _default_state_dir() -> str:
    # Mirrors utils.paths.prediction_state_dir(); kept local so the prediction
    # package stays importable on its own.
    return os.environ.get("PREDICTION_STATE_DIR") or os.path.join(os.environ.get("DATA_DIR", "/data"), "prediction")


class WeightStore:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or _default_state_dir())
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(game: str) -> str:
        """One file per game: normalize case/whitespace and refuse path tricks."""
        key = str(game or "").strip().lower()
        if not key or not re.fullmatch(r"[a-z0-9_]+", key):
            raise ValueError(f"Invalid game key for weight store: {game!r}")
        return key

    def _path(self, game: str) -> Path:
        return self.base_dir / f"{self._key(game)}_weights.json"

    def load(self, game: str, initial_weights: dict[str, float] | None = None) -> WeightState:
        game = self._key(game)
        path = self._path(game)
        if path.exists():
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            # Copy everything: never hand out the caller's (or a cached config's) dict.
            return WeightState(
                game=game,
                weights=dict(data["weights"] or {}) if "weights" in data else dict(initial_weights or {}),
                best_weights=dict(data.get("best_weights") or {}),
                best_validation_accuracy=data.get("best_validation_accuracy"),
                best_validation_at=float(data.get("best_validation_at", 0)),
                updated_at=float(data.get("updated_at", 0)),
                last_draw_id=data.get("last_draw_id"),
                history=list(data.get("history") or []),
            )

        weights = dict(initial_weights or {})
        if weights:
            total = sum(weights.values())
            if total > 0:
                weights = {k: v / total for k, v in weights.items()}
        return WeightState(game=game, weights=weights)

    def snapshot(self, game: str) -> dict:
        state = self.load(game)
        return {
            "weights": dict(state.weights or {}),
            "best_weights": dict(state.best_weights or {}),
            "best_validation_accuracy": state.best_validation_accuracy,
            "best_validation_at": state.best_validation_at,
            "last_draw_id": state.last_draw_id,
            "history": list(state.history or []),
        }

    def restore(self, game: str, snapshot: dict | None) -> WeightState:
        state = self.load(game)
        payload = snapshot or {}
        state.weights = dict(payload.get("weights") or state.weights or {})
        state.best_weights = dict(payload.get("best_weights") or {})
        state.best_validation_accuracy = payload.get("best_validation_accuracy")
        state.best_validation_at = float(payload.get("best_validation_at") or 0)
        state.last_draw_id = payload.get("last_draw_id")
        state.history = list(payload.get("history") or [])
        self.save(state)
        return state

    def adopt_best(self, game: str) -> WeightState:
        """Make the live mix the last promoted balance (production / end of tuning)."""
        state = self.load(game)
        if state.best_weights:
            state.weights = dict(state.best_weights)
            self.save(state)
        return state

    def clear_last_draw(self, game: str) -> WeightState:
        state = self.load(game)
        state.last_draw_id = None
        self.save(state)
        return state

    def save(self, state: WeightState) -> None:
        path = self._path(state.game)
        payload = {
            "game": state.game,
            "weights": state.weights,
            "best_weights": state.best_weights,
            "best_validation_accuracy": state.best_validation_accuracy,
            "best_validation_at": state.best_validation_at,
            "updated_at": state.updated_at or time.time(),
            "last_draw_id": state.last_draw_id,
            "history": state.history[-50:],  # keep last 50 updates
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        tmp.replace(path)