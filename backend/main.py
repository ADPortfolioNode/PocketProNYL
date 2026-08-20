"""
PocketPro:NYL Project - FastAPI Application Bootstrap
Simplified main.py for application initialization and route registration.

"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# Import the new health router specifically to access its global flag
from routes import health, games, models, chroma, ingestion, predictions, training, experiments, chat
from middleware.rate_limit import rate_limit_middleware
from state.ingestion_worker import start_background_ingestion
from services.chroma_client import chroma_client
from config import GAME_CONFIGS, settings, resolve_game_key

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


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
    print("🚀 PocketPro:NYL Project backend starting up...")
    # Run startup tasks
    asyncio.create_task(_deferred_lm_audit())
    # This is a synchronous call that starts the background worker thread.
    # It should not be wrapped in asyncio.create_task.
    start_background_ingestion() 

    # Signal that the application is now fully initialized and ready to serve traffic.
    health.APP_IS_READY = True

    yield

    # Shutdown
    print("👋 PocketPro:NYL Project backend shutting down...")

# Create FastAPI application
app = FastAPI(lifespan=lifespan)


def _cors_allowed_origins() -> list[str]:
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)