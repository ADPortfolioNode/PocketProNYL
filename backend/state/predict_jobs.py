"""In-memory suggestion-batch status (one Hypercorn worker)."""

import threading
import time

_lock = threading.Lock()
_batch = {}


def _fresh():
    return {
        "status": "idle",
        "games": [],
        "completed": 0,
        "total": 0,
        "current_game": None,
        "errors": 0,
        "results": {},
        "started_at": None,
        "finished_at": None,
        "updated_at": time.time(),
    }


def start_batch(games):
    """Mark a suggestion batch running; games run sequentially, like train jobs."""
    keys = [str(g or "").strip().lower() for g in (games or []) if str(g or "").strip()]
    with _lock:
        _batch.clear()
        _batch.update(_fresh())
        _batch.update(
            {
                "status": "running",
                "games": keys,
                "total": len(keys),
                "current_game": keys[0] if keys else None,
                "started_at": time.time(),
                "updated_at": time.time(),
            }
        )
        return dict(_batch)


def mark_game_done(game_key, ok=True):
    """Advance the batch after one game finishes; next game becomes current."""
    key = str(game_key or "").strip().lower()
    with _lock:
        if not _batch:
            return {}
        _batch["completed"] = int(_batch.get("completed") or 0) + 1
        _batch.setdefault("results", {})[key] = "done" if ok else "error"
        if not ok:
            _batch["errors"] = int(_batch.get("errors") or 0) + 1
        games = _batch.get("games") or []
        done = int(_batch.get("completed") or 0)
        _batch["current_game"] = games[done] if done < len(games) else None
        _batch["updated_at"] = time.time()
        return dict(_batch)


def finish_batch(status="done"):
    """Close the batch; status is done or error."""
    with _lock:
        if not _batch:
            return {}
        _batch["status"] = status
        _batch["current_game"] = None
        _batch["finished_at"] = time.time()
        _batch["updated_at"] = time.time()
        return dict(_batch)


def get_predict_batch():
    """Snapshot of the last batch, or an idle payload when none ever ran."""
    with _lock:
        if not _batch:
            return _fresh()
        return dict(_batch)
