"""
Background ingestion worker for automatic startup ingestion.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from state.ingest_state import (
    startup_state,
    update_startup_progress,
    set_game_status,
)
from services.ingest import ingest_service


_startup_state_lock = threading.Lock()
_startup_parallel = max(1, int(os.getenv("INGEST_STARTUP_PARALLEL", "1")))
_startup_count_workers = max(1, int(os.getenv("INGEST_STARTUP_COUNT_WORKERS", "4")))


def _prefetch_existing_counts(games: list[str]) -> dict[str, int]:
    """
    Resolve per-game draw totals for startup skip logic.

    Prefer the persisted draw_counts cache, but if a game shows 0, probe live
    Chroma once so rebuilds/restarts keep existing history instead of re-ingesting.
    """
    from state.draw_counts import get_all_draw_counts, set_draw_count

    counts = get_all_draw_counts(games)
    for game in games:
        counts.setdefault(game, 0)

    missing = [game for game, value in counts.items() if int(value or 0) <= 0]
    if not missing:
        return counts

    try:
        from services.chroma_client import chroma_client

        snapshots = chroma_client.get_collections_snapshot(missing, timeout_seconds=8.0, refresh=True)
        for snap in snapshots or []:
            name = snap.get("name")
            live = int(snap.get("count") or 0)
            if name and live > 0:
                counts[name] = live
                set_draw_count(name, live)
                print(f"✓ Startup count refresh: {name}={live} (kept existing Chroma draws)")
    except Exception as exc:
        print(f"⚠ Startup could not refresh live Chroma counts: {exc}")

    return counts


def _ingest_single_game(game: str, game_index: int, total_games: int, existing_draws: int):
    """Ingest one configured game and update startup state."""
    try:
        if existing_draws > 0:
            print(f"⏭ [STARTUP] Skipping {game} — {existing_draws} draws already in Chroma")
            with _startup_state_lock:
                set_game_status(game, "completed")
                update_startup_state(
                    startup_state,
                    current_game=game,
                    current_task="skipped_existing",
                    progress=float(game_index),
                    current_game_rows_fetched=existing_draws,
                    current_game_rows_total=existing_draws,
                )
            return

        with _startup_state_lock:
            update_startup_state(
                startup_state,
                current_game=game,
                current_task="fetching",
                progress=float(game_index - 1),
                current_game_rows_fetched=0,
                current_game_rows_total=0,
            )
            set_game_status(game, "ingesting")

        def update_game_progress(rows_fetched, total_rows):
            with _startup_state_lock:
                update_startup_state(
                    startup_state,
                    current_game=game,
                    current_game_rows_fetched=rows_fetched,
                    current_game_rows_total=total_rows,
                    progress=calculate_progress(game_index, rows_fetched, total_rows, total_games),
                )

        result = ingest_service.fetch_and_sync(
            game,
            progress_callback=update_game_progress,
            force=False,
        )

        with _startup_state_lock:
            set_game_status(game, "completed")
            update_startup_state(
                startup_state,
                current_game=game,
                progress=float(game_index),
                current_game_rows_fetched=result.get("total", 0),
                current_game_rows_total=result.get("total", 0),
            )
    except Exception as exc:
        with _startup_state_lock:
            set_game_status(game, "error", error=str(exc))
        print(f"Failed to ingest {game}: {exc}")


def start_background_ingestion():
    """Start non-blocking background ingestion in a daemon thread."""
    def ingest_all():
        from config import GAME_CONFIGS

        startup_state["started_at"] = time.time()
        startup_state["status"] = "ingesting"
        update_startup_state(startup_state, status="ingesting", started_at=time.time())

        games = list(GAME_CONFIGS.keys())
        existing_counts = _prefetch_existing_counts(games)

        if _startup_parallel <= 1:
            for index, game in enumerate(games, 1):
                _ingest_single_game(game, index, len(games), existing_counts.get(game, 0))
        else:
            workers = min(_startup_parallel, len(games))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        _ingest_single_game,
                        game,
                        index,
                        len(games),
                        existing_counts.get(game, 0),
                    )
                    for index, game in enumerate(games, 1)
                ]
                for future in as_completed(futures):
                    future.result()

        startup_state["status"] = "completed"
        startup_state["completed_at"] = time.time()
        update_startup_state(startup_state, status="completed", completed_at=time.time())

    thread = threading.Thread(target=ingest_all, daemon=True, name="BackgroundIngestion")
    thread.start()


def update_startup_state(state_dict, **kwargs):
    """Helper to update startup state dictionary."""
    for key, value in kwargs.items():
        state_dict[key] = value


def calculate_progress(index, rows_fetched, total_rows, total_games):
    """Calculate progress fraction based on current game and fetch progress."""
    if total_rows and total_rows > 0:
        fraction = rows_fetched / max(total_rows, 1)
        fraction = max(0.0, min(1.0, fraction))
        return float(index - 1) + fraction
    return float(index - 1)


# Global flag for ingestion status
_ingestion_started = False