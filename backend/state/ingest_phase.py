"""What each game's ingest is doing right now, and when it last made progress.

``report_phase`` is called from the ingest pipeline at every stage. The
startup status merges it so the init screen can say what is actually
happening (waiting on Chroma, fetching N rows, downloading the embedding
model, embedding and writing rows) and can tell a normal slow phase from a
real stall: a phase is ``stalled`` only when it has made no progress for
longer than its own timeout.
"""
from __future__ import annotations

import os
import threading
import time

PHASE_LABELS = {
    "queued": "Queued",
    "waiting_on_chroma": "Connecting to Chroma",
    "counting_rows": "Asking data.ny.gov how many rows to fetch",
    "fetching": "Fetching rows from data.ny.gov",
    "loading_embedding_model": "Loading the embedding model (first run downloads ~80 MB)",
    "embedding_and_writing": "Embedding rows and writing them to Chroma",
    "repairing": "Checking stored rows belong to this game",
    "done": "Done",
    "error": "Failed",
}
TERMINAL = {"done", "error"}

# A phase is stalled after this many seconds without any progress update.
_DEFAULT_STALL_S = float(os.getenv("INGEST_STALL_SECONDS", "180"))
_STALL_OVERRIDES = {
    # The one-time model download has no byte progress; allow a slow connection.
    "loading_embedding_model": float(os.getenv("INGEST_MODEL_STALL_SECONDS", "900")),
}

_lock = threading.Lock()
_phases: dict[str, dict] = {}


def stall_timeout_for(phase: str | None) -> float:
    return _STALL_OVERRIDES.get(str(phase or ""), _DEFAULT_STALL_S)


def report_phase(game: str, phase: str, detail: str | None = None,
                 rows_done: int | None = None, rows_total: int | None = None) -> None:
    key = str(game or "").strip().lower()
    if not key:
        return
    now = time.time()
    with _lock:
        previous = _phases.get(key) or {}
        entry = {
            "phase": phase,
            "phase_label": PHASE_LABELS.get(phase, phase),
            "phase_detail": detail,
            "phase_rows_done": rows_done,
            "phase_rows_total": rows_total,
            "phase_started_at": previous.get("phase_started_at") if previous.get("phase") == phase else now,
            "phase_updated_at": now,
        }
        _phases[key] = entry


def clear_phase(game: str) -> None:
    with _lock:
        _phases.pop(str(game or "").strip().lower(), None)


def phase_snapshot(game: str, now: float | None = None) -> dict | None:
    key = str(game or "").strip().lower()
    with _lock:
        entry = dict(_phases.get(key) or {})
    if not entry:
        return None
    now = now or time.time()
    age = max(0.0, now - float(entry.get("phase_updated_at") or now))
    timeout = stall_timeout_for(entry.get("phase"))
    entry["phase_age_s"] = round(age, 1)
    entry["stall_timeout_s"] = timeout
    entry["stalled"] = entry.get("phase") not in TERMINAL and age > timeout
    return entry


def active_phase(now: float | None = None) -> tuple[str, dict] | tuple[None, None]:
    """Most recently updated non-terminal phase across games."""
    with _lock:
        games = list(_phases.keys())
    best = (None, None)
    for game in games:
        snap = phase_snapshot(game, now)
        if not snap or snap.get("phase") in TERMINAL:
            continue
        if best[1] is None or snap["phase_updated_at"] > best[1]["phase_updated_at"]:
            best = (game, snap)
    return best
