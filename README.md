# PocketPro:NYL Predictive RAG

Lottery data pipeline with ingestion, model training, suggestions, and optional AI chat (Gemini, OpenAI, Grok). Stack: **React** frontend, **FastAPI** backend, **ChromaDB** vector store — all orchestrated with Docker Compose.

## Quick start (Windows)

1. Install Docker Desktop and wait until it is running.
2. Open the `PocketProNYL` folder.
3. Double-click **`StartPocketProNYL.bat`**
   - First run: creates `.env` from `.env.example` (optional API keys for chat).
   - Builds images, starts containers, opens the dashboard in your browser.
4. Double-click **`StopPocketProNYL.bat`** when finished (data volumes are kept).

First build can take 10–20 minutes. Later starts are faster.

| Launcher | Purpose |
|----------|---------|
| `StartPocketProNYL.bat` | Build + start stack, open app |
| `StopPocketProNYL.bat` | Stop containers cleanly |
| `start-windows.ps1` | PowerShell launcher used by the `.bat` file |
| `recover_stack.ps1` | Recovery when Docker port-forwarding fails |
| `rebuild.ps1` | Rebuild frontend/backend and restart |

**App URL:** `http://127.0.0.1:3000` (or `FRONTEND_HOST_PORT` from `.env`)

**Windows tips**

- Prefer `http://127.0.0.1:3000` over `localhost` if you see timeouts (IPv6/WSL relay issues).
- Wait for **Stack healthy** in the startup window, then hard-refresh (`Ctrl+Shift+R`).
- Gateway errors: `.\scripts\diag_gateway_502.ps1` or `.\scripts\run_full_diag.ps1`

## Quick start (Mac / Linux)

```bash
git clone https://github.com/ADPortfolioNode/PocketProNYL.git
cd PocketProNYL
cp .env.example .env   # add API keys if you want AI chat
docker compose up --build -d
```

Open **http://localhost:3000**. The frontend nginx proxy serves `/api/*` to the backend — you normally only need port 3000.

## Ports and URLs

Host ports come from `.env`. Compose defaults vs common Windows overrides:

| Service | Container port | Default host port | Often in `.env` (Windows) |
|---------|----------------|-------------------|---------------------------|
| Frontend | 80 | 3000 | 3000 |
| Backend | 5000 | 5000 | **5001** |
| ChromaDB | 8000 | 8000 | **8001** |

Health checks:

```bash
curl http://127.0.0.1:3000/
curl http://127.0.0.1:5001/api/health    # or :5000
curl http://127.0.0.1:8001/api/v1/heartbeat
```

## API keys (optional)

Training, ingestion, and suggestions work without keys. At least one key enables AI chat.

## Typical workflow

1. **Ingest** — pull draw history into ChromaDB for a game.
2. **Train** — build a model; experiments are saved with accuracy and parameters.
3. **Suggest** — generate next-draw suggestions from the trained model.
4. **Chat** (optional) — RAG concierge when API keys are set.

## Troubleshooting the local stack

If the UI loads but APIs fail:

- Set `REACT_APP_API_BASE=` (empty) in `.env` so the browser uses nginx `/api` proxy.
- Set `CHROMA_HOST=pocketpro_nyl_chroma` (not `mensa_chroma`).
- Point scripts at `http://127.0.0.1:5001` when `BACKEND_HOST_PORT=5001`.
- Do not commit `.env`. Rotate any keys that were pasted into chat or committed.
