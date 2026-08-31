import json
import os
from typing import Any, Dict, List

MAX_STORED_PER_GAME = 3


def _experiment_accuracy(experiment: Dict[str, Any]) -> float | None:
    for key in ("highest_accuracy", "final_accuracy", "accuracy", "score"):
        value = experiment.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _experiment_timestamp(experiment: Dict[str, Any]) -> float:
    for key in ("timestamp", "timestamp_seconds"):
        value = experiment.get(key)
        if value is None:
            continue
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if ts >= 1_000_000_000_000:
            return ts / 1000.0
        return ts
    return 0.0


def prune_experiments(
    experiments: List[Dict[str, Any]],
    *,
    max_per_game: int = MAX_STORED_PER_GAME,
) -> List[Dict[str, Any]]:
    """Keep at most *max_per_game* records per game (top accuracy, then newest)."""
    if max_per_game <= 0:
        return []

    by_game: dict[str, list[Dict[str, Any]]] = {}
    unscoped: list[Dict[str, Any]] = []

    for item in experiments or []:
        game = str(item.get("game") or "").strip().lower()
        if game:
            by_game.setdefault(game, []).append(item)
        else:
            unscoped.append(item)

    pruned: list[Dict[str, Any]] = []

    for items in by_game.values():
        items.sort(
            key=lambda exp: (
                _experiment_accuracy(exp) if _experiment_accuracy(exp) is not None else -1.0,
                _experiment_timestamp(exp),
            ),
            reverse=True,
        )
        pruned.extend(items[:max_per_game])

    unscoped.sort(key=_experiment_timestamp, reverse=True)
    pruned.extend(unscoped[:max_per_game])
    return pruned


def update_accuracy_history(
    existing: List[Any] | None,
    new_accuracy: float | None,
    *,
    limit: int = MAX_STORED_PER_GAME,
) -> List[float]:
    """Maintain up to *limit* highest accuracy values (descending)."""
    values: list[float] = []
    for item in existing or []:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            continue
    if new_accuracy is not None:
        try:
            values.append(float(new_accuracy))
        except (TypeError, ValueError):
            pass
    if not values:
        return []
    values.sort(reverse=True)
    deduped: list[float] = []
    for value in values:
        if not deduped or abs(deduped[-1] - value) > 1e-9:
            deduped.append(value)
    return deduped[:limit]


class ExperimentStore:
    def __init__(self, store_path: str):
        self.store_path = store_path
        self._ensure_store_exists()

    def _ensure_store_exists(self):
        if not os.path.exists(self.store_path):
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump([], f)

    def list_experiments(self) -> List[Dict[str, Any]]:
        try:
            with open(self.store_path, "r") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError, ValueError):
            # Recover from a truncated/corrupt write so predictions can still save.
            return []

    @staticmethod
    def _json_default(value: Any):
        """Serialize numpy scalars and other common non-JSON types."""
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                pass
        if isinstance(value, (set, tuple)):
            return list(value)
        return str(value)

    def save_experiment(self, experiment_data: Dict[str, Any]):
        experiments = self.list_experiments()
        experiments.append(experiment_data)
        experiments = prune_experiments(experiments)
        with open(self.store_path, "w") as f:
            json.dump(experiments, f, indent=2, default=self._json_default)