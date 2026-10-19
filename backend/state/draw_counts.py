"""
Persistent per-game draw counts.

Chroma's count endpoint can be very slow on large collections. We store the
last known totals from successful ingests and serve summaries from this cache.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from utils.paths import data_dir

_counts_lock = threading.Lock()
_counts: dict[str, int] = {}
_counts_file = Path(data_dir()) / "draw_counts.json"
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _counts_lock:
        if _loaded:
            return
        try:
            if _counts_file.exists():
                with _counts_file.open("r", encoding="utf-8") as handle:
                    data = json.load(handle) or {}
                if isinstance(data, dict):
                    for game, value in data.items():
                        try:
                            _counts[str(game)] = max(0, int(value))
                        except (TypeError, ValueError):
                            continue
        except Exception as exc:
            print(f"⚠ Failed to load draw counts from {_counts_file}: {exc}")
        _loaded = True


def _persist() -> None:
    try:
        _counts_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = _counts_file.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(_counts, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(_counts_file))
    except Exception as exc:
        print(f"⚠ Failed to persist draw counts to {_counts_file}: {exc}")


def get_draw_count(game: str, default: int = 0) -> int:
    _ensure_loaded()
    with _counts_lock:
        return int(_counts.get(game, default))


def set_draw_count(game: str, count: int) -> None:
    _ensure_loaded()
    with _counts_lock:
        _counts[game] = max(0, int(count))
    _persist()


def update_draw_count(game: str, count: int) -> None:
    """Alias used by ingest completion paths."""
    set_draw_count(game, count)


def invalidate_draw_count(game: str) -> None:
    """Drop cached draw total so the next count query refreshes from Chroma."""
    _ensure_loaded()
    with _counts_lock:
        if game in _counts:
            del _counts[game]
    _persist()


def get_all_draw_counts(games: list[str] | None = None) -> dict[str, int]:
    _ensure_loaded()
    with _counts_lock:
        if not games:
            return dict(_counts)
        return {game: int(_counts.get(game, 0)) for game in games}


def seed_from_manual_ingest_state(state: dict) -> None:
    """Bootstrap cache from persisted manual ingest state on startup."""
    _ensure_loaded()
    changed = False
    with _counts_lock:
        for game, payload in (state or {}).items():
            if not isinstance(payload, dict):
                continue
            if str(payload.get("status", "")).lower() != "completed":
                continue
            total = payload.get("total_rows") or payload.get("rows_fetched")
            if total is None:
                continue
            try:
                total_i = max(0, int(total))
            except (TypeError, ValueError):
                continue
            if _counts.get(game, 0) < total_i:
                _counts[game] = total_i
                changed = True
    if changed:
        _persist()