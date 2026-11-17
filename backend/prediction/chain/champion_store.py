"""Per-game champion record and carried posterior for the chained ensemble.

One JSON file per game under ``$PREDICTION_STATE_DIR/chain/<game>.json``. The
champion is ``incumbent`` unless an evaluation promoted the chained ensemble.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.paths import prediction_state_dir

INCUMBENT = "incumbent"
CHAINED = "chained_ensemble"
_GAME_RE = re.compile(r"^[a-z0-9]+$")


def _path(game: str) -> Path:
    key = str(game or "").strip().lower()
    if not _GAME_RE.match(key):
        raise ValueError(f"Invalid game key: {game!r}")
    return Path(prediction_state_dir()) / "chain" / f"{key}.json"


def load(game: str) -> dict:
    path = _path(game)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or str(data.get("game") or "") != str(game).strip().lower():
        return {}
    return data


def save(game: str, data: dict) -> None:
    path = _path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**data, "game": str(game).strip().lower()}, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def current_champion(game: str) -> tuple[str, dict | None]:
    data = load(game)
    if data.get("champion") == CHAINED and isinstance(data.get("champion_variant"), dict):
        return CHAINED, data["champion_variant"]
    return INCUMBENT, None


def champion_summary(game: str) -> dict | None:
    data = load(game)
    if not data:
        return None
    last = data.get("last_evaluation") or {}
    return {
        "champion": data.get("champion") or INCUMBENT,
        "last_evaluated_at": data.get("last_evaluated_at"),
        "promoted": bool(last.get("promoted")),
        "reason": last.get("decision_reason"),
        "holdout": last.get("holdout"),
    }
