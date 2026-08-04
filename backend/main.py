"""
Mensa Project - FastAPI Application Bootstrap
Simplified main.py for application initialization and route registration.
"""
from typing import Any, Dict, Optional
import requests
import hashlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import routes
from routes import health, games, models, chroma, ingestion, predictions, training, experiments, chat
# Import middleware
from middleware.rate_limit import rate_limit_middleware
# Import state management
from state.ingestion_worker import start_background_ingestion

from services.chroma_client import chroma_client
from config import GAME_CONFIGS


def _require_game_key(game: str) -> str:
    from config import resolve_game_key

    key = resolve_game_key(game)
    if not key:
        raise ValueError(f"Unknown game: {game}")
    return key


def _latest_game_snapshot(game: str, sample_size: int = 200) -> Optional[dict]:
    try:
        collection = chroma_client.client.get_collection(game)
        count = collection.count()
        if count == 0:
            return None
        latest = collection.get(include=["metadatas"], limit=1)
        meta = (latest.get("metadatas") or [{}])[0]
        return {
            "game": game,
            "draw_count": count,
            "latest_draw_date": meta.get("draw_date"),
            "latest_numbers": meta.get("winning_numbers"),
        }
    except Exception:
        return {
            "game": game,
            "draw_count": 0,
            "latest_draw_date": None,
            "latest_numbers": None,
        }


def _compute_dataset_snapshot(game: str) -> dict:
    """
    Return a small dataset snapshot for a game including a lightweight
    reproducibility hash, record count, and latest draw date/numbers.
    """
    try:
        snap = _latest_game_snapshot(game)
        count = int(snap.get("draw_count", 0) or 0)
        latest_date = snap.get("latest_draw_date")
        latest_numbers = str(snap.get("latest_numbers") or "")
        digest_input = f"{game}|{count}|{latest_numbers}"
        digest = hashlib.md5(digest_input.encode("utf-8")).hexdigest()
        return {
            "dataset_hash": digest,
            "record_count": count,
            "latest_draw_date": latest_date,
            "latest_numbers": latest_numbers,
        }
    except Exception:
        return {
            "dataset_hash": None,
            "record_count": 0,
            "latest_draw_date": None,
            "latest_numbers": None,
        }


async def _deferred_lm_audit():
    """Run LM provider audit in background so API can serve health checks immediately."""
    try:
        from services.lm_router import lm_router
        snapshot = await lm_router.audit_connections(force=True)
        ordered = snapshot.get("ordered_available", [])
        print(f"LM audit complete. Available providers (fastest first): {ordered}")
    except Exception as exc:
        print(f"LM audit failed at startup: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    import asyncio

    # Startup
    print("🚀 Mensa Project backend starting up...")
    asyncio.create_task(_deferred_lm_audit())
    start_background_ingestion()
    
    yield
    
    # Shutdown
    print("👋 Mensa Project backend shutting down...")


# Create FastAPI application
app = FastAPI(lifespan=lifespan)


def _cors_allowed_origins() -> list[str]:
    from config import settings

    raw = (settings.CORS_ALLOWED_ORIGINS or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# Register middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(rate_limit_middleware)


# Register routes
app.include_router(health.router)
app.include_router(games.router)
app.include_router(models.router)
app.include_router(chroma.router)
app.include_router(ingestion.router)
app.include_router(predictions.router)
app.include_router(training.router)
app.include_router(experiments.router)
app.include_router(chat.router)

@app.get("/api/train_settings")
async def get_train_settings(game: str = None):
    """
    Returns recommended trainer knobs and (optionally) a dataset snapshot.
    - If `game` query param provided, returns defaults + dataset snapshot for that game.
    - Without `game`, returns defaults and a per-game summary map.
    """
    try:
        from services.trainer import trainer_service

        defaults = {
            "target_accuracy": float(getattr(trainer_service, "target_accuracy", 0.98)),
            "max_train_attempts": int(getattr(trainer_service, "max_train_attempts", 12)),
            "blend_step": float(getattr(trainer_service, "blend_step", 0.1)),
            # Sampling / model knobs (mirror Trainer defaults or sensible fallbacks)
            "train_size": 0.33,
            "validation_size": 0.67,
            "random_state": 42,
            "n_estimators": 100,
            "max_depth": 12,
        }

        if game:
            game_key = _require_game_key(game)
            dataset = _compute_dataset_snapshot(game_key)
            return {"game": game_key, "defaults": defaults, "dataset": dataset}

        per_game = {}
        for g in GAME_CONFIGS.keys():
            per_game[g] = _compute_dataset_snapshot(g)

        return {"game": None, "defaults": defaults, "per_game": per_game}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/train_settings/{game}")
async def get_train_settings_by_path(game: str):
    """Path-style alias for train settings."""
    return await get_train_settings(game=game)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)