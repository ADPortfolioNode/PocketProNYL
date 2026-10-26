"""Background, debounced pattern-scan reruns after ingests (per game, one at a time).

Every ingest that adds draws for a game (startup, scheduled or manual: they all go
through ``IngestService.fetch_and_sync``) calls ``notify_ingest``. The game's scan is
scheduled ``PATTERN_RERUN_DEBOUNCE_SECONDS`` after the LAST such ingest, runs on a
single daemon worker (never blocks a request or an ingest), and its result is
persisted with a timestamp and the draw count under
``$PREDICTION_STATE_DIR/patterns/<game>.json``. An ingest that lands while that
game's scan is running marks it pending; it reruns once afterwards.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from utils.paths import prediction_state_dir


def cache_path(game: str) -> Path:
    from prediction.patterns.auto import _key

    return Path(prediction_state_dir()) / "patterns" / f"{_key(game)}.json"


def load_cached(game: str) -> dict | None:
    path = cache_path(game)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("game") == game else None


def _fingerprint(history) -> dict:
    last = history[-1] if history else None
    return {"draws": len(history), "last_draw_id": str(getattr(last, "draw_id", "") or ""),
            "last_draw_date": str(getattr(last, "draw_date", "") or "")}


def _default_loader(game: str, limit: int):
    from prediction.core.draw_loader import from_chroma

    return from_chroma(game, limit=limit)


def _default_count(game: str) -> int:
    try:
        from state.draw_counts import get_draw_count

        return int(get_draw_count(game, 0) or 0)
    except Exception:
        return 0


def run_scan(game: str, *, history=None, loader=None, count_fn=None, challenger=None, trigger: str = "manual",
             limit: int | None = None) -> dict:
    """Scan one game's own draws, run the promote/demote loop, persist and return the report."""
    from prediction.chain import champion_store
    from prediction.patterns.auto import apply_auto_promotion, now_iso, read_events
    from prediction.patterns.search import run_pattern_search
    from prediction.patterns.settings import load_settings

    settings = load_settings(game)
    if history is None:
        history = (loader or _default_loader)(game, int(limit or settings.history_limit))
    total = int((count_fn or _default_count)(game) or 0) or len(history)
    started = time.time()
    if len(history) < settings.min_draws:
        report = {"game": game, "status": "insufficient_data", "signals": [], "feature_signals": [],
                  "passed_holdout": [], "draws_available": len(history),
                  "note": f"Needs at least {settings.min_draws} draws (PATTERN_MIN_DRAWS); has {len(history)}."}
    else:
        report = run_pattern_search(game, history, windows=list(settings.windows), fdr_q=settings.fdr_q)
    if report.get("status") == "ok":
        report["auto_promotion"] = apply_auto_promotion(game, history, report, settings, challenger=challenger)
    report.update({
        "fingerprint": _fingerprint(history),
        "draw_count": total,
        "history_draws": len(history),
        "last_run_at": now_iso(),
        "run_seconds": round(time.time() - started, 2),
        "trigger": trigger,
        "settings": settings.to_dict(),
        "champion": champion_store.champion_summary(game),
    })
    report["events_tail"] = read_events(game, 20)
    path = cache_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, default=str), encoding="utf-8")
    tmp.replace(path)
    return report


class PatternScheduler:
    """Debounced per-game scheduling onto one daemon worker thread."""

    def __init__(self, runner=None, count_fn=None):
        self._runner = runner or (lambda game, trigger, limit: run_scan(game, trigger=trigger, limit=limit))
        self._count = count_fn or _default_count
        self._lock = threading.Lock()
        self._states: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

    # -------------------------------------------------------------- state
    def _state(self, game: str) -> dict:
        return self._states.setdefault(game, {"phase": "idle", "timer": None, "pending": False, "runs": 0,
                                              "last_error": None, "trigger": None, "limit": None,
                                              "scheduled_for": None, "last_started_at": None,
                                              "last_finished_at": None})

    def notify_ingest(self, game: str, added: int, source: str = "ingest") -> bool:
        """Called after every ingest. Schedules a debounced rerun when draws were added."""
        from prediction.patterns.settings import load_settings

        try:
            added = int(added or 0)
        except (TypeError, ValueError):
            added = 0
        settings = load_settings(game)
        if added <= 0 or not settings.enabled:
            return False
        self.request(game, delay=settings.debounce_seconds, trigger=f"{source}:+{added}")
        return True

    def request(self, game: str, delay: float = 0.0, trigger: str = "manual", limit: int | None = None) -> str:
        """(Re)schedule a scan ``delay`` seconds from now; the latest request wins the timer."""
        with self._lock:
            st = self._state(game)
            st["trigger"], st["limit"] = trigger, limit or st.get("limit")
            if st["phase"] == "queued":
                return "queued"  # the queued scan loads the newest draws anyway
            if st["phase"] == "running":
                st["pending"] = True  # rerun once the current scan finishes
                return "running"
            if st["timer"] is not None:
                st["timer"].cancel()
            if delay <= 0:
                st["timer"], st["scheduled_for"] = None, None
                self._enqueue_locked(game)
                return "queued"
            timer = threading.Timer(delay, self._fire, args=(game,))
            timer.daemon = True
            st["timer"], st["phase"], st["scheduled_for"] = timer, "scheduled", time.time() + delay
            timer.start()
            return "scheduled"

    def _fire(self, game: str) -> None:
        with self._lock:
            st = self._state(game)
            st["timer"], st["scheduled_for"] = None, None
            if st["phase"] == "running":
                st["pending"] = True
                return
            if st["phase"] != "queued":
                self._enqueue_locked(game)

    def _enqueue_locked(self, game: str) -> None:
        self._state(game)["phase"] = "queued"
        self._queue.put(game)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, daemon=True, name="PatternScanWorker")
            self._worker.start()

    def _work(self) -> None:
        while True:
            try:
                game = self._queue.get(timeout=5)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._worker = None
                        return
                continue
            with self._lock:
                st = self._state(game)
                st["phase"], st["last_started_at"], st["pending"] = "running", time.time(), False
                trigger, limit = st["trigger"], st["limit"]
            error = None
            try:
                self._runner(game, trigger, limit)
            except Exception as exc:  # recorded; the panel shows the last good result
                error = str(exc)[:500]
                print(f"⚠ [PATTERNS] scan failed for {game}: {error}")
            with self._lock:
                st = self._state(game)
                st["phase"], st["last_finished_at"], st["last_error"] = "idle", time.time(), error
                st["runs"] += 1
                if st["pending"]:
                    st["pending"] = False
                    self._enqueue_locked(game)

    # -------------------------------------------------------------- reads
    def status(self, game: str, cached: dict | None = None) -> dict:
        with self._lock:
            st = dict(self._state(game))
        st.pop("timer", None)
        if st["phase"] != "idle":
            scan_status = "running"
        elif cached is None:
            scan_status = "stale"
        else:
            current = int(self._count(game) or 0)
            scan_status = "stale" if current and current > int(cached.get("draw_count") or 0) else "fresh"
        return {**st, "scan_status": scan_status}

    def wait_idle(self, game: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                st = self._state(game)
                if st["phase"] == "idle" and not st["pending"]:
                    return True
            time.sleep(0.02)
        return False


_SCHEDULER: PatternScheduler | None = None
_SCHED_GUARD = threading.Lock()


def get_scheduler() -> PatternScheduler:
    global _SCHEDULER
    with _SCHED_GUARD:
        if _SCHEDULER is None:
            _SCHEDULER = PatternScheduler()
        return _SCHEDULER


def set_scheduler(scheduler: PatternScheduler | None) -> None:
    """Tests swap in a scheduler with a fake runner."""
    global _SCHEDULER
    with _SCHED_GUARD:
        _SCHEDULER = scheduler


def notify_ingest(game: str, result: dict | None, source: str = "ingest") -> bool:
    """Ingest hook: never raises (an ingest must not fail because of the pattern scan)."""
    try:
        added = (result or {}).get("added", 0)
        return get_scheduler().notify_ingest(game, added, source=source)
    except Exception as exc:
        print(f"⚠ [PATTERNS] could not schedule a rerun for {game}: {exc}")
        return False
