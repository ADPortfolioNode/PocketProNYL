# PocketPro:NYL Predictive RAG

Lottery data pipeline with ingestion, model training, suggestions, and optional AI chat (Gemini, OpenAI, Grok). Stack: **React** frontend, **FastAPI** backend, **ChromaDB** vector store — all orchestrated with Docker Compose.

## Quick start (Windows)

1. Install Docker Desktop and wait until it is running. # PocketPro:NYL Project
2. Open the `pocketpro-nyl` folder.
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
git clone https://github.com/ADPortfolioNode/pocketpro-nyl.git
cd pocketpro-nyl
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

Check your active ports:

```bash
docker compose ps
```

Health checks (adjust port if you use `BACKEND_HOST_PORT` / `CHROMA_HOST_PORT`):

```bash
curl http://127.0.0.1:3000/
curl http://127.0.0.1:5001/api/health    # or :5000
curl http://127.0.0.1:8001/api/v1/heartbeat
```

## API keys (optional)

Training, ingestion, and suggestions work without keys. At least one key enables AI chat:

| Provider | Variable | Get a key |
|----------|----------|-----------|
| Google Gemini | `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey |
| OpenAI | `OPENAI_API_KEY` / `CHAT_GPT_API_KEY` | https://platform.openai.com/api-keys |
| xAI Grok | `GROK_API_KEY` | https://console.x.ai |

## Typical workflow

1. **Ingest** — pull draw history into ChromaDB for a game.
2. **Train** — build a Random Forest model; experiments are saved with accuracy and parameters.
3. **Suggest** — generate next-draw suggestions from the trained model.
4. **Chat** (optional) — RAG concierge when API keys are set.

Training uses **incremental learning**: new runs build on the prior best model. The dashboard loads **highest-accuracy settings** from saved experiments when you select a game or restart.

**Large games (e.g. Powerball):** if training hits 502/504, lower target accuracy (~85–88%), max iterations (10–12), N estimators (~150), and disable auto-tune.

## Project layout

```text
pocketpro-nyl/
├── StartPocketProNYL.bat / StopPocketProNYL.bat # Windows one-click launchers
├── start-windows.ps1                  # Staged Docker startup (Windows)
├── docker-compose.yml                 # Local dev stack
├── docker-compose.prod.yml            # Loopback-only port overrides
├── docker-compose.distribution.yml    # Production / subscriber deployment
├── .env.example                       # Local env template
├── frontend/                          # React + nginx (production serve)
├── backend/
│   ├── main.py                        # FastAPI entry
│   ├── routes/                        # API: games, ingest, train, predict, chat, …
│   ├── services/                      # ingest, trainer, predictor, chroma, RAG
│   └── utils/                         # training params, timestamps, validation
├── scripts/                           # deploy, diagnostics, port helpers
├── docs/                              # Architecture, deployment, guides, testing
└── verify_*.ps1 / verify_*.py         # Smoke and workflow checks
```

## Docker commands

```bash
# Build and start
docker compose up --build -d

# Rebuild one service
docker compose build backend
docker compose build frontend

# Logs
docker compose logs -f backend
docker compose logs -f frontend

# Stop (keep data)
docker compose down

# Full reset (destroys volumes)
docker compose down -v
```

Force backend rebuild after code changes:

```bash
BACKEND_CACHE_BUSTER=$(date +%s) docker compose build backend
```

## Environment variables

Copy `.env.example` → `.env`. Important entries:

| Variable | Description |
|----------|-------------|
| `DOCKER_BIND_HOST` | Host bind address (`127.0.0.1` on Windows) |
| `FRONTEND_HOST_PORT` | Dashboard port (default `3000`) |
| `BACKEND_HOST_PORT` | Direct API port (default `5000`, often `5001`) |
| `CHROMA_HOST_PORT` | Chroma host port (default `8000`, often `8001`) |
| `REACT_APP_API_BASE` | Empty in Docker (nginx proxies `/api`); set for Vercel |
| `BACKEND_CACHE_BUSTER` / `FRONTEND_CACHE_BUSTER` | Cache-bust Docker builds |

Training defaults can be tuned via `TRAIN_*` variables (see `.env.example` and `docker-compose.yml`).

## Verification

```powershell
# Windows — containers, health, endpoints
.\verify_production.ps1
.\verify_production.ps1 -All   # includes security headers

.\verify_frontend.ps1
.\scripts\run_full_diag.ps1
```

```bash
# Python workflow checks (backend should be up; set port if not 5000)
python verify_training_learning.py
python verify_all_games_training.py
```

## Client distribution (zip / tar.gz)

Package the app for delivery to a client (source, launchers, optional pre-built Docker images):

```powershell
.\scripts\package-distribution.ps1 -Version 20260818
.\scripts\zip-distribution.ps1 -PackageDir release\pocketpro_nyl_client_20260818
```

Output under `release/`:

| Artifact | Purpose |
|----------|---------|
| `mensa_client_<version>.tar.gz` | Single-file bundle (~300 MB with images) |
| `mensa_client_<version>_app.tar.gz` | Source + docs only |
| `mensa_client_<version>_images.tar.gz` | Pre-built backend + frontend images |
| `SEND_TO_CLIENT.txt` | What to email/upload |

Client Windows flow: extract → `images\load-images.ps1` → `StartMensa.bat` (uses pre-loaded images when `.env` has `MENSA_REGISTRY=mensa-local`). See `INSTALL.md` inside the package.

Do **not** include your `.env` (API keys).

## Production deployment

Public HTTPS deployment with internal-only API/Chroma:

```bash
cp .env.production.example .env
# Edit DOMAIN, ACME_EMAIL, API keys
./scripts/deploy-production.sh    # Linux
.\scripts\deploy-production.ps1   # Windows
```

Details: [docs/deployment/PUBLIC_DISTRIBUTION.md](docs/deployment/PUBLIC_DISTRIBUTION.md)

## Supported games

| Game | Main numbers | Bonus | Schedule |
|------|--------------|-------|----------|
| Take 5 | 5 (1–39) | 1 (1–39) | 2× daily |
| Pick 3 | 3 (0–9) | — | 2× daily |
| Powerball | 5 (1–69) | 1 (1–26) | Mon, Wed, Sat |
| Mega Millions | 5 (1–70) | 1 (1–25) | Tue, Fri |
| Pick 10 | 20 (1–80) | — | Daily |
| Cash4Life | 5 (1–60) | 1 (1–4) | Daily |
| Quick Draw | 20 (1–80) | — | Frequent |
| NY Lotto | 6 (1–59) | 1 (1–59) | Wed, Sat |

## Documentation

| Topic | Location |
|-------|----------|
| Doc index | [docs/README.md](docs/README.md) |
| Operations | [docs/guides/OPERATIONS_GUIDE.md](docs/guides/OPERATIONS_GUIDE.md) |
| Troubleshooting | [docs/guides/TROUBLESHOOTING.md](docs/guides/TROUBLESHOOTING.md) |
| Architecture | [docs/architecture/](docs/architecture/) |
| Release notes | [docs/changes/RELEASE_NOTES.md](docs/changes/RELEASE_NOTES.md) |

## Troubleshooting

**Port conflicts** — set in `.env`:

```env
FRONTEND_HOST_PORT=3001
BACKEND_HOST_PORT=5001
CHROMA_HOST_PORT=8001
```

**Container unhealthy**

```bash
docker compose logs backend --tail 80
docker compose restart backend
```

**Volume permissions**

```bash
docker compose down
docker compose run --rm backend chown -R appuser:appuser /data
docker compose up -d
```

**Nuclear reset**

```bash
docker compose down -v
docker compose up --build -d
```

## CI/CD

GitHub Actions (`.github/workflows/`):

- `docker-build-push.yml` — build images, push to GHCR/Docker Hub on `main`
- `lint-and-quality.yml` — flake8, black, hadolint, yamllint
- `build-and-push-backend.yml` — backend image pipeline

## Contributing

1. Fork the repo and branch from `main`.
2. Run local checks before pushing:

```bash
pip install flake8 black
flake8 backend/ --select=E9,F63,F7,F82 --show-source
black --check backend/
```

3. Open a pull request against `main`.