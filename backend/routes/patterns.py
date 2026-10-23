"""Pattern-search report: each signal's lift vs chance, p, BH-FDR q, and holdout verdict."""

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from prediction.chain import champion_store
from utils.paths import prediction_state_dir
from utils.validation import _require_game_key

router = APIRouter()
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _history_limit(raw: int | None) -> int:
    default = int(os.getenv("PATTERN_HISTORY_LIMIT", "1000") or 1000)
    return max(100, min(int(raw or default), 3000))


def _cache_path(game: str) -> Path:
    return Path(prediction_state_dir()) / "patterns" / f"{game}.json"


def _fingerprint(history) -> dict:
    last = history[-1] if history else None
    return {
        "draws": len(history),
        "last_draw_id": str(getattr(last, "draw_id", "") or ""),
        "last_draw_date": str(getattr(last, "draw_date", "") or ""),
    }


def build_report(game: str, limit: int, refresh: bool = False, history=None) -> dict:
    """Run (or reuse) the pattern search for one game's own draws."""
    from prediction.patterns.search import run_pattern_search

    if history is None:
        from prediction.core.draw_loader import from_chroma

        history = from_chroma(game, limit=limit)
    fingerprint = _fingerprint(history)
    path = _cache_path(game)
    with _GUARD:
        lock = _LOCKS.setdefault(game, threading.Lock())
    with lock:
        if not refresh and path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("fingerprint") == fingerprint and cached.get("game") == game:
                    return {**cached, "cached": True}
            except (OSError, json.JSONDecodeError):
                pass
        report = run_pattern_search(game, history)
        report["fingerprint"] = fingerprint
        report["champion"] = champion_store.champion_summary(game)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, default=str), encoding="utf-8")
        tmp.replace(path)
        return {**report, "cached": False}


@router.get("/api/pattern_report")
async def pattern_report(
    game: str = Query(...),
    refresh: bool = Query(False),
    limit: int | None = Query(None, ge=1),
):
    """Per-signal lift vs chance, permutation p, BH-FDR q and pass/fail for one game."""
    try:
        game_key = _require_game_key(game)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return await run_in_threadpool(build_report, game_key, _history_limit(limit), refresh)
    except Exception as exc:  # collection missing, chroma down, ...
        return {"status": "error", "game": game_key, "message": str(exc), "signals": []}
