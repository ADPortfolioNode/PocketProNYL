# PocketPro:NYL Predictive RAG

Lottery data pipeline with ingestion, model training, suggestions, and optional AI chat (Gemini, OpenAI, Grok). Stack: **React** frontend, **FastAPI** backend, **ChromaDB** vector store — Docker Compose.

## Quick start (Windows)

1. Install Docker Desktop and wait until it is running.
2. Open the `PocketProNYL` folder.
3. Double-click **`StartPocketProNYL.bat`**
4. Double-click **`StopPocketProNYL.bat`** when finished (data volumes are kept).

**App URL:** `http://127.0.0.1:3000`

Prefer `127.0.0.1` over `localhost` on Windows.

## Quick start (Mac / Linux)

```bash
git clone https://github.com/ADPortfolioNode/PocketProNYL.git
cd PocketProNYL
cp .env.example .env
docker compose up --build -d
```

Or: `./start.sh --build`

## Ports

| Service | Container | Typical host |
|---------|-----------|--------------|
| Frontend | 80 | 3000 |
| Backend | 5000 | 5001 |
| ChromaDB | 8000 | 8001 |

If APIs fail from the UI:

- `REACT_APP_API_BASE=` (empty) in `.env`
- `CHROMA_HOST=pocketpro_nyl_chroma`
- scripts/tests use `http://127.0.0.1:5001`

Do not commit `.env`.
