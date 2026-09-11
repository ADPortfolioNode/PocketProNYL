"""
State management for ingestion processes.
Handles both automatic startup ingestion and manual ingestion queue.
"""
import threading
import json
import os
from pathlib import Path
from collections import deque, defaultdict
from datetime import datetime
from typing import Dict, Any

from config import GAME_CONFIGS


# Global startup state tracking for initialization
startup_state = {
    "status": "ready",
    "progress": 0.0,
    "total": len(GAME_CONFIGS),
    "current_game": None,
    "current_task": None,
    "current_game_rows_fetched": 0,
    "current_game_rows_total": 0,
    "games": {game: {"status": "pending", "error": None} for game in GAME_CONFIGS.keys()},
    "started_at": None,
    "completed_at": None,
}

# Track manual ingestion progress globally
manual_ingest_state = {}

# Manual ingestion queue to serialize manual ingests (avoid parallel runs)
manual_ingest_queue: deque[dict] = deque()
manual_ingest_worker_running = False
manual_ingest_queue_lock = threading.Lock()
# Monotonic sequence for enqueued jobs to provide stable IDs even if the
# worker consumes jobs quickly (prevents duplicate "position=1" confusion).
manual_ingest_seq = 0

# Persistent ingest state file (inside DATA_DIR)
_ingest_state_file = Path(os.environ.get('DATA_DIR', '/data')) / 'ingest_state.json'
_save_interval_s = float(os.environ.get('INGEST_STATE_SAVE_INTERVAL_S', '2'))
_save_lock = threading.Lock()
_save_timer: threading.Timer | None = None
_save_dirty = False


def _load_manual_ingest_state():
    """Load manual ingest state from persistent storage."""
    global manual_ingest_state
    try:
        if _ingest_state_file.exists():
            with _ingest_state_file.open('r', encoding='utf-8') as fh:
                data = json.load(fh) or {}
                if isinstance(data, dict):
                    manual_ingest_state.clear()
                    manual_ingest_state.update(data)
    except Exception as e:
        print(f"⚠ Failed to load manual ingest state from {_ingest_state_file}: {e}")


def _save_manual_ingest_state():
    """Save manual ingest state to persistent storage."""
    try:
        _ingest_state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ingest_state_file.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as fh:
            json.dump(manual_ingest_state, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(_ingest_state_file))
    except Exception as e:
        print(f"⚠ Failed to persist manual ingest state to {_ingest_state_file}: {e}")


def _flush_manual_ingest_state():
    """Write pending manual ingest state to disk."""
    global _save_dirty, _save_timer
    with _save_lock:
        if not _save_dirty:
            _save_timer = None
            return
        _save_dirty = False
        if _save_timer is not None:
            _save_timer.cancel()
            _save_timer = None
    _save_manual_ingest_state()


def _schedule_manual_ingest_state_save(*, force: bool = False):
    """Debounce disk writes during active ingestion; flush immediately on terminal states."""
    global _save_dirty, _save_timer
    with _save_lock:
        _save_dirty = True
        if force:
            if _save_timer is not None:
                _save_timer.cancel()
                _save_timer = None

    if force:
        _flush_manual_ingest_state()
        return

    with _save_lock:
        if _save_timer is not None:
            return

        def _timer_callback():
            _flush_manual_ingest_state()

        _save_timer = threading.Timer(_save_interval_s, _timer_callback)
        _save_timer.daemon = True
        _save_timer.start()


def set_manual_ingest_state(game_key: str, state: dict):
    """Update manual ingest state for a specific game."""
    manual_ingest_state[game_key] = state
    terminal = str(state.get("status", "")).lower() in {"completed", "error", "failed"}
    if terminal and str(state.get("status", "")).lower() == "completed":
        try:
            from state.draw_counts import update_draw_count

            total = state.get("total_rows") or state.get("rows_fetched")
            if total is not None:
                update_draw_count(game_key, int(total))
        except Exception:
            pass
    _schedule_manual_ingest_state_save(force=terminal)


def get_manual_ingest_state(game_key: str) -> Dict[str, Any]:
    """Get manual ingest state for a specific game."""
    return manual_ingest_state.get(game_key, {})


def get_startup_state() -> Dict[str, Any]:
    """Get current startup state."""
    try:
        from services.game_tuner import game_tuner
        return {**startup_state, "tuning": game_tuner.status()}
    except Exception:
        return startup_state


def reset_startup_state():
    """Reset startup state for a new run."""
    startup_state["status"] = "ready"
    startup_state["progress"] = 0.0
    startup_state["total"] = len(GAME_CONFIGS)
    startup_state["current_game"] = None
    startup_state["current_task"] = None
    startup_state["current_game_rows_fetched"] = 0
    startup_state["current_game_rows_total"] = 0
    startup_state["games"] = {game: {"status": "pending", "error": None} for game in GAME_CONFIGS.keys()}
    startup_state["started_at"] = None
    startup_state["completed_at"] = None


def update_startup_progress(progress: float, current_game: str = None, 
                           current_task: str = None, rows_fetched: int = 0, 
                           total_rows: int = 0):
    """Update startup progress state."""
    startup_state["progress"] = progress
    if current_game:
        startup_state["current_game"] = current_game
    if current_task:
        startup_state["current_task"] = current_task
    startup_state["current_game_rows_fetched"] = rows_fetched
    startup_state["current_game_rows_total"] = total_rows


def set_game_status(game: str, status: str, error: str = None):
    """Set status for a specific game in startup state."""
    startup_state["games"][game]["status"] = status
    if error:
        startup_state["games"][game]["error"] = error


def get_manual_ingest_queue_size() -> int:
    """Get current size of manual ingestion queue."""
    return len(manual_ingest_queue)


def enqueue_manual_ingest(game_key: str, force: bool = False) -> int:
    """Add a manual ingestion job to the queue. Returns sequence number."""
    global manual_ingest_seq
    manual_ingest_seq += 1
    job = {
        "game": game_key,
        "force": force,
        "seq": manual_ingest_seq
    }
    with manual_ingest_queue_lock:
        manual_ingest_queue.append(job)
    return manual_ingest_seq


# Load persisted state at startup (best-effort)
try:
    _load_manual_ingest_state()
    from state.draw_counts import seed_from_manual_ingest_state

    seed_from_manual_ingest_state(manual_ingest_state)
except Exception:
    pass