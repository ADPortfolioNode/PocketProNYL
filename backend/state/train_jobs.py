"""In-memory training job status (one Hypercorn worker)."""
import threading
import time

_lock = threading.Lock()
_jobs = {}


def set_job(game_key: str, payload: dict) -> dict:
    game_key = str(game_key or "").strip().lower()
    row = {**payload, "game": game_key, "updated_at": time.time()}
    with _lock:
        _jobs[game_key] = row
    return dict(row)


def get_job(game_key: str) -> dict:
    game_key = str(game_key or "").strip().lower()
    with _lock:
        return dict(_jobs.get(game_key) or {"status": "idle", "game": game_key})


def list_jobs() -> dict:
    with _lock:
        return {key: dict(value) for key, value in _jobs.items()}


def is_running(game_key: str) -> bool:
    status = str(get_job(game_key).get("status") or "").lower()
    return status in {"running", "queued"}
