"""PocketPro:NYL FastAPI bootstrap."""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routes import health, games, models, chroma, ingestion, predictions, training, experiments, chat
from middleware.rate_limit import rate_limit_middleware
from state.ingestion_worker import start_background_ingestion
from services.ingest import ingest_service
from services.socrata_fetch import install_capped_fetch
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def _deferred_lm_audit():
    try:
        from services.lm_router import lm_router
        snapshot = await lm_router.audit_connections(force=True)
        print(f"LM audit complete. Available providers: {snapshot.get('ordered_available', [])}")
    except Exception as exc:
        print(f"LM audit failed at startup: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import os

    print("PocketPro:NYL backend starting up...", flush=True)
    # Mark ready first so /api/health responds even if ingest/LM work is slow.
    health.APP_IS_READY = True
    install_capped_fetch(ingest_service)
    asyncio.create_task(_deferred_lm_audit())
    # Startup ingest loads embeddings and can wedge low-RAM hosts; allow skip.
    if os.getenv("SKIP_STARTUP_INGEST", "0") != "1":
        start_background_ingestion()
    else:
        print("SKIP_STARTUP_INGEST=1 — background ingest not started", flush=True)
    yield
    print("PocketPro:NYL backend shutting down...", flush=True)


app = FastAPI(lifespan=lifespan)


def _cors_allowed_origins() -> list[str]:
    raw = (settings.CORS_ALLOWED_ORIGINS or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(rate_limit_middleware)

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
