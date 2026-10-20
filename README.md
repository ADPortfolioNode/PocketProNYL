# PocketPro:NYL Predictive RAG

Lottery data pipeline with ingestion, model training, suggestions, and optional AI chat (Gemini, OpenAI, Grok). Stack: **React** frontend, **FastAPI** backend, **ChromaDB** vector store — Docker Compose.

## Code from anywhere (GitHub Codespaces)

Open the repo on GitHub → **Code → Codespaces → Create codespace on main**. The dev container installs Python 3.11, Node 22 and Docker, then all dependencies. Run `docker compose up --build` and the dashboard opens on port 3000 as a private preview link. Works from any browser, including a tablet.

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

## Subscriber deployment (optional API keys)

Local starts are unchanged. For a paid or public server, turn on per-customer keys in `.env`:

```env
AUTH_MODE=apikey
API_KEYS=subscriber-key-1,subscriber-key-2   # predict, chat, read
ADMIN_API_KEYS=your-admin-key                 # train, ingest, vector-store admin
```

Generate a key with `openssl rand -hex 24`. The browser asks for the key once and remembers it. `/api/health` stays public for health checks. See `backend/middleware/auth.py`.

## Storage paths

All state lives under `DATA_DIR` (default `/data`, the Docker volume). `MODELS_DIR` and `PREDICTION_STATE_DIR` override their sub-folders. Paths resolve in `backend/utils/paths.py`.

## Tests

```bash
pip install -r backend/requirements-dev.txt
DATA_DIR=/tmp/pocketpro-data python -m pytest -q tests
```

## License and disclaimer

Proprietary, all rights reserved. See `LICENSE`.

PocketPro:NYL is statistical analysis for entertainment only. Lottery draws are random and no tool can predict or improve the odds. Not affiliated with the New York Lottery or the New York State Gaming Commission. 18+. Gambling problem? Call 1-877-8-HOPENY.
