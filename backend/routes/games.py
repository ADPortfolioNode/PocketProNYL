"""
Games API routes — dynamic catalog driven by config + live draw data.
"""
import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel
from utils.game_catalog import get_game_catalog, get_game_detail
from utils.validation import _require_game_key
from services.chroma_client import chroma_client


router = APIRouter()

CHROMA_QUERY_TIMEOUT = 8.0


class GameSummaryResponse(BaseModel):
    game: str
    draw_count: int
    title: str | None = None
    has_draws: bool | None = None
    ready_for_training: bool | None = None
    ready_for_suggestions: bool | None = None


@router.get("/api/games")
async def get_games(refresh: bool = Query(False, description="Refresh live Chroma draw counts")):
    """
    Dynamically list all configured games with rules, suggestion formats,
    schedules, draw counts, and rule-derived training defaults.
    """
    try:
        catalog = await asyncio.wait_for(
            asyncio.to_thread(get_game_catalog, refresh_counts=refresh),
            timeout=CHROMA_QUERY_TIMEOUT + 2.0,
        )
    except asyncio.TimeoutError:
        catalog = get_game_catalog(refresh_counts=False)
        catalog["warning"] = "draw_count_refresh_timeout"
    return catalog


@router.get("/api/games/summaries")
async def get_all_game_summaries(refresh: bool = False):
    """
    Return draw counts (+ readiness) for all games in one request.
    """
    try:
        catalog = await asyncio.wait_for(
            asyncio.to_thread(get_game_catalog, refresh_counts=refresh),
            timeout=CHROMA_QUERY_TIMEOUT + 2.0,
        )
    except asyncio.TimeoutError:
        catalog = get_game_catalog(refresh_counts=False)

    summaries = {}
    for entry in catalog.get("catalog") or []:
        key = entry["key"]
        summaries[key] = {
            "game": key,
            "title": entry.get("title") or key,
            "draw_count": int(entry.get("draw_count") or 0),
            "has_draws": bool(entry.get("has_draws")),
            "ready_for_training": bool(entry.get("ready_for_training")),
            "ready_for_suggestions": bool(entry.get("ready_for_suggestions")),
            "suggestion_format": entry.get("suggestion_format") or {},
            "training_defaults": entry.get("training_defaults") or {},
        }
    return {
        "summaries": summaries,
        "total_games": catalog.get("total_games"),
        "populated_games": catalog.get("populated_games"),
    }


@router.get("/api/games/{game}")
async def get_game(game: str, refresh: bool = True):
    """Full dynamic detail for one game (rules, draws, training defaults)."""
    detail = await asyncio.to_thread(lambda: get_game_detail(game, refresh_counts=refresh))
    if not detail:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Unknown game '{game}'")
    return detail


@router.get("/api/games/{game}/summary", response_model=GameSummaryResponse)
async def get_game_summary(game: str):
    """
    Get summary statistics for a specific game.
    """
    game_key = _require_game_key(game)
    try:
        draw_count = await asyncio.wait_for(
            asyncio.to_thread(chroma_client.count_documents, game_key),
            timeout=CHROMA_QUERY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        draw_count = 0
    detail = get_game_detail(game_key, refresh_counts=False) or {}
    return GameSummaryResponse(
        game=game_key,
        draw_count=int(draw_count or 0),
        title=detail.get("title"),
        has_draws=int(draw_count or 0) > 0,
        ready_for_training=int(draw_count or 0) >= 50,
        ready_for_suggestions=int(draw_count or 0) >= 20,
    )
