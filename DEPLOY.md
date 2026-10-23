# Deploying PocketPro:NYL

Short, current deploy guide. Architecture notes stay in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md);
public multi-customer servers use `docker-compose.distribution.yml` (see `docs/deployment/PUBLIC_DISTRIBUTION.md`).

Stack: `frontend` (nginx, container :80, proxies `/api` to `backend:5000`) -> `backend`
(FastAPI/hypercorn, container :5000) -> `chroma` (chromadb/chroma 0.5.3, container :8000).

## 1. Windows quickstart (Docker Desktop, PowerShell)

```powershell
git clone https://github.com/ADPortfolioNode/PocketProNYL.git
cd PocketProNYL
Copy-Item .env.example .env      # then add your API keys in .env
docker compose up -d --build
docker compose ps                # wait until backend/chroma show "healthy"
```

Open http://127.0.0.1:3002. Host ports come from `FRONTEND_PORT` (3002) and `BACKEND_PORT` (5002)
in `.env`; container ports and the nginx `/api` proxy never change. Keep `FRONTEND_HOST_PORT`/
`BACKEND_HOST_PORT` equal to them (older scripts such as `start-windows.ps1` read those).

Loopback-only binding: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.

## 2. Updating

Build from source:

```powershell
git pull
docker compose up -d --build
```

Or run the images CI publishes to GHCR (no local build):

```powershell
git pull
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml up -d
```

Images: `ghcr.io/adportfolionode/pocketpro-nyl-backend` and `ghcr.io/adportfolionode/pocketpro-nyl-frontend`.
Tags: `latest` (main), `sha-<7 chars>`, full commit SHA, branch name, `v*`. Pin one with
`POCKETPRO_NYL_VERSION=sha-abc1234` in `.env`. If the packages are private, run
`docker login ghcr.io -u ADPortfolioNode` first (token with `read:packages`).
Data volumes survive both update paths.

## 3. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FRONTEND_PORT` | `3002` | Host port for the UI (container :80) |
| `BACKEND_PORT` | `5002` | Host port for the API (container :5000) |
| `CHROMA_HOST_PORT` | `8001` | Host port for Chroma (container :8000) |
| `DOCKER_BIND_HOST` | `0.0.0.0` | Bind address; keep 0.0.0.0 on Docker Desktop Windows |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `CHAT_GPT_API_KEY`, `GROK_API_KEY` | empty | LLM providers (chat/assistant features) |
| `GROK_API_BASE`, `GROK_MODEL` | `https://api.x.ai/v1`, `grok-3-mini-beta` | Grok endpoint/model |
| `DATA_DIR` | `/data` | Backend data root (named volume `backend_data`) |
| `PREDICTION_ENGINE` | `modular` | Prediction engine |
| `PREDICTION_STATE_DIR` | `/data/prediction` | Prediction state (inside the data volume) |
| `CHROMA_HOST`, `CHROMA_PORT` | `pocketpro_nyl_chroma`, `8000` | Chroma server the backend talks to |
| `SKIP_STARTUP_INGEST` | `0` | `1` skips the ingest on boot |
| `CORS_ALLOWED_ORIGINS` | empty | Extra browser origins (only needed when not using the `/api` proxy) |
| `AUTH_MODE`, `API_KEYS`, `ADMIN_API_KEYS` | `off` | Optional X-API-Key gate (`backend/middleware/auth.py`) |
| `POCKETPRO_NYL_REGISTRY`, `POCKETPRO_NYL_VERSION` | `ghcr.io/adportfolionode`, `latest` | Image source for `docker-compose.ghcr.yml` |

## 4. Data persistence and backup

Named volumes (kept across `down`, rebuilds and GHCR pulls; removed only by `docker compose down -v`):
`backend_data` -> `/data` (DATA_DIR, models, prediction state), `chroma_data` -> `/chroma/chroma`
(vector DB), `chroma_model_cache` (embedding model cache). Docker prefixes them with the
project folder name; list them with `docker volume ls`.

Backup (PowerShell, from the repo folder; replace `pocketpronyl` with your prefix):

```powershell
docker compose stop backend chroma
docker run --rm -v pocketpronyl_backend_data:/v -v ${PWD}:/b alpine tar czf /b/nyl-data.tgz -C /v .
docker run --rm -v pocketpronyl_chroma_data:/v -v ${PWD}:/b alpine tar czf /b/nyl-chroma.tgz -C /v .
docker compose start chroma backend
```

Restore: same commands with `tar xzf /b/nyl-data.tgz -C /v` (stack stopped).

## 5. Cloud option: Render Blueprint (`render.yaml`)

Creates three services: `pocketpro-nyl-chroma` (private, 10 GB disk), `pocketpro-nyl-backend`
(Docker, 20 GB disk on `/data`, health `/api/health`) and `pocketpro-nyl-frontend` (static site
that rewrites `/api/*` to the backend). Disks need paid plans; the backend uses `standard` (2 GB RAM).

1. render.com -> New -> Blueprint -> connect GitHub -> pick `ADPortfolioNode/PocketProNYL`, branch `main`.
2. Enter the secrets it asks for (`GEMINI_API_KEY`, `OPENAI_API_KEY`, ...; `CORS_ALLOWED_ORIGINS` can stay empty).
3. Apply. If the backend URL is not `https://pocketpro-nyl-backend.onrender.com`, edit the
   `/api/*` rewrite in `render.yaml` (or the static site's Redirects/Rewrites) to match.
4. Deploys are manual (`autoDeploy: false`): Manual Deploy -> Deploy latest commit.

Caveat (not yet verified on Render): the backend image runs as `appuser`; if logs show
permission errors on `/data`, the disk is root-owned and the Dockerfile needs a follow-up change.
`fly.toml` is a backend-only alternative; it needs a separate Chroma app.

## 6. Smoke URLs

Local (defaults):
- http://127.0.0.1:3002 - UI
- http://127.0.0.1:3002/api/health - API through the nginx proxy
- http://127.0.0.1:5002/api/health - backend direct
- http://127.0.0.1:5002/api/startup_status - ingest/startup progress
- http://127.0.0.1:5002/docs - FastAPI docs
- http://127.0.0.1:8001/api/v1/heartbeat - Chroma

Render: `https://<frontend>.onrender.com/` and `https://<frontend>.onrender.com/api/health`.
