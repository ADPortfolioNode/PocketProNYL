"""Bankroll tracker: what was spent and won, per game, kept strictly apart.

Append-only JSONL per game at ``$DATA_DIR/roi/<game>/bankroll.jsonl``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timezone

from services.game_odds import PICKS_CANNOT_CHANGE_ODDS
from utils.paths import data_dir

_GAME_RE = re.compile(r"^[a-z0-9]+$")
_LOCK = threading.Lock()


def _path(game: str) -> str:
    key = str(game or "").strip().lower()
    if not _GAME_RE.match(key):
        raise ValueError(f"Invalid game key: {game!r}")
    return os.path.join(data_dir(), "roi", key, "bankroll.jsonl")


def record(game: str, stake: float, winnings: float = 0.0, *, tickets: int | None = None,
           on: str | None = None, note: str | None = None) -> dict:
    stake, winnings = float(stake), float(winnings)
    if stake < 0 or winnings < 0:
        raise ValueError("stake and winnings must be zero or more")
    day = date.fromisoformat(str(on)[:10]).isoformat() if on else date.today().isoformat()
    entry = {"id": uuid.uuid4().hex, "game": str(game).strip().lower(), "date": day, "stake": round(stake, 2),
             "winnings": round(winnings, 2), "tickets": tickets, "note": (note or "")[:200],
             "recorded_at": datetime.now(timezone.utc).isoformat()}
    path = _path(game)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK, open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def entries(game: str) -> list[dict]:
    path = _path(game)
    key = str(game).strip().lower()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("game") == key:
                out.append(row)
    return out


def summary(game: str, ev_per_dollar: float | None = None) -> dict:
    rows = entries(game)
    staked = round(sum(r["stake"] for r in rows), 2)
    won = round(sum(r["winnings"] for r in rows), 2)
    net = round(won - staked, 2)
    out = {"game": str(game).strip().lower(), "entries": len(rows), "total_staked": staked, "total_won": won,
           "net": net, "roi_percent": round(net / staked * 100, 2) if staked else None,
           "recent": sorted(rows, key=lambda r: (r["date"], r["recorded_at"]), reverse=True)[:10]}
    if ev_per_dollar is not None and staked:
        out["expected_net"] = round(staked * (ev_per_dollar - 1), 2)
        out["luck_vs_expected"] = round(net - out["expected_net"], 2)
    return out


def all_summaries(games: list[str], ev_lookup: dict[str, float]) -> dict:
    per_game = [summary(g, ev_lookup.get(g)) for g in games]
    per_game = [s for s in per_game if s["entries"]]
    staked = round(sum(s["total_staked"] for s in per_game), 2)
    won = round(sum(s["total_won"] for s in per_game), 2)
    return {"status": "ok", "games": per_game, "total_staked": staked, "total_won": won,
            "net": round(won - staked, 2), "disclaimer": PICKS_CANNOT_CHANGE_ODDS}
