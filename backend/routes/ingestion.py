"""
Ingestion API routes.
"""
import json
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.ingest import ingest_service
from state.ingest_state import get_manual_ingest_state, enqueue_manual_ingest, set_manual_ingest_state
from state.manual_ingest_worker import _start_manual_ingest_worker_if_needed
from utils.validation import _require_game_key
from typing import Optional
from config import GAME_CONFIGS


router = APIRouter()


class IngestRequest(BaseModel):
    game: str
    force: bool = False


class RepairRequest(BaseModel):
    game: Optional[str] = None  # None / "all" = every configured game
    dry_run: bool = True  # report only unless the caller opts in to deletes


def _fast_complete_populated_game(game_key: str) -> dict | None:
    """Return an immediate completed payload when data exists and force is off."""
    if os.getenv("INGEST_FAST_COMPLETE_POPULATED", "1") == "0":
        return None

    from services.chroma_client import chroma_client

    existing_draws = chroma_client.count_documents(game_key, allow_refresh=False)
    if existing_draws <= 0 and chroma_client.collection_exists(game_key):
        existing_draws = 1
    if existing_draws <= 0:
        return None

    completed_state = {
        "status": "completed",
        "rows_fetched": existing_draws,
        "total_rows": existing_draws,
        "progress": 100,
        "added": 0,
        "skipped_fetch": True,
    }
    set_manual_ingest_state(game_key, completed_state)
    return {
        "status": "completed",
        "game": game_key,
        "message": f"{game_key} already has {existing_draws} draws (skipped re-fetch; use force=true to reingest)",
        "rows_fetched": existing_draws,
        "total_rows": existing_draws,
        "added": 0,
    }


@router.post("/api/repair_game_data")
async def repair_game_data(request: RepairRequest):
    """Remove rows from a game's store that belong to another game or fail its rules."""
    import asyncio
    from services.data_repair import repair_all_games, repair_game_collection

    try:
        if not request.game or str(request.game).strip().lower() in ("all", "*"):
            results = await asyncio.to_thread(repair_all_games, dry_run=request.dry_run)
        else:
            game_key = _require_game_key(request.game)
            results = [await asyncio.to_thread(repair_game_collection, game_key, dry_run=request.dry_run)]
        return {"status": "ok", "dry_run": request.dry_run, "results": results}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {"status": "error", "message": str(e), "results": []}


@router.post("/api/ingest")
async def trigger_ingestion(request: IngestRequest):
    """
    Trigger manual data ingestion for a specific game.
    """
    try:
        game_key = _require_game_key(request.game)

        if not request.force:
            fast_result = _fast_complete_populated_game(game_key)
            if fast_result:
                return fast_result

        # Enqueue the ingestion job
        seq = enqueue_manual_ingest(game_key, request.force)
        
        # Start worker if needed
        _start_manual_ingest_worker_if_needed()
        
        # Mark as queued in state
        set_manual_ingest_state(game_key, {
            "status": "queued",
            "queued": True,
            "seq": seq
        })
        
        return {
            "status": "queued",
            "game": game_key,
            "sequence": seq,
            "message": f"Ingestion queued for {game_key}"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@router.get("/api/ingest_progress")
async def get_ingest_progress(game: str):
    """
    Get progress of manual ingestion for a specific game.
    """
    try:
        game_key = _require_game_key(game)
        progress = get_manual_ingest_state(game_key)
        
        if not progress:
            return {
                "status": "not_started",
                "game": game_key
            }
        
        return {
            "game": game_key,
            **progress
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


def _build_all_games_progress() -> dict:
    """Aggregate manual ingest progress for every configured game."""
    payload = {}
    for game_key in GAME_CONFIGS.keys():
        progress = get_manual_ingest_state(game_key)
        if progress:
            payload[game_key] = progress
        else:
            payload[game_key] = {
                "status": "not_started",
                "rows_fetched": 0,
                "total_rows": 0,
            }
    return payload


def _is_active_ingest_status(status: str) -> bool:
    return str(status or "").lower() in {"queued", "ingesting", "running", "active", "pending"}


@router.get("/api/ingest_stream")
async def get_ingest_stream(game: Optional[str] = None):
    """
    Server-sent events stream for ingestion progress.
    Without `game`, streams a map of all configured games (for all-games UI mode).
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    try:
        if game:
            game_key = _require_game_key(game)

            async def single_game_event_stream():
                while True:
                    progress = get_manual_ingest_state(game_key)

                    if progress:
                        status = progress.get("status", "unknown")
                        serialized = json.dumps(progress)
                        yield f"data: {serialized}\n\n"
                        yield f"event: progress\ndata: {serialized}\n\n"

                        if status in ["completed", "error"]:
                            yield f"event: complete\ndata: {serialized}\n\n"
                            break

                    await asyncio.sleep(1)

            stream_factory = single_game_event_stream
        else:

            async def all_games_event_stream():
                while True:
                    payload = _build_all_games_progress()
                    yield f"data: {json.dumps(payload)}\n\n"

                    statuses = [payload[g].get("status") for g in payload]
                    if not any(_is_active_ingest_status(s) for s in statuses):
                        break

                    await asyncio.sleep(1)

            stream_factory = all_games_event_stream

        return StreamingResponse(
            stream_factory(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }