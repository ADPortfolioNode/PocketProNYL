# PocketPro:NYL

NY Lottery ingest → train → suggest. React + nginx frontend, FastAPI/Hypercorn backend, ChromaDB.

**This is not Mensa and not the Concierge monorepo.** UI strings, titles, containers, and logs must say PocketPro:NYL / `pocketpro_nyl_*`. If the user talks about port 8002, Redis/Celery, or `E:\2024 RESET\concierge`, stop and ask which repo.

Repo: `E:\2024 RESET\PocketProNYL`
GitHub: `ADPortfolioNode/PocketProNYL` (work on `main` unless asked otherwise)

## Stack

- `frontend/` CRA React, nginx on container port 80
- `backend/` FastAPI + Hypercorn `--workers 1` on 5000
- `chroma` image `chromadb/chroma:0.5.3`
- Compose file: `docker-compose.yml`
- Env template: `.env.example` → copy to `.env` (never commit `.env`)

## Ports (Windows Docker Desktop)

Always bind `DOCKER_BIND_HOST=0.0.0.0`. Browse/test with `127.0.0.1`, not `localhost` (IPv6/wslrelay).

| Service  | Container | Typical host | User may remap in `.env` |
|----------|-----------|--------------|--------------------------|
| Frontend | 80        | 3000         | `FRONTEND_HOST_PORT` (this machine has used 3001) |
| Backend  | 5000      | 5001         | `BACKEND_HOST_PORT` (has used 5002) |
| Chroma   | 8000      | 8001         | `CHROMA_HOST_PORT` (has used 8002) |

UI talks to nginx `/api` on the **frontend** port. Direct backend is optional.

`REACT_APP_API_BASE=` must stay empty. `CHROMA_HOST=pocketpro_nyl_chroma`.

Before telling the user a URL, run:
`docker ps --filter name=pocketpro_nyl --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"`

## Hard rules (this repo has already been burned)

- Do **not** `docker system prune` as part of start. It wipes the pip layer.
- Do **not** `docker compose build --no-cache` unless pip/requirements actually changed.
- Do **not** bake the 79MB ONNX model on every image build. `SKIP_ONNX=1` (default). Model lives in volume `chroma_model_cache`.
- One Hypercorn worker + CPU-heavy ingest freezes HTTP. Cap Socrata at `INGEST_MAX_ROWS_PER_GAME=80000`. `INGEST_ENABLE_CATALOG_FALLBACK=0`.
- Frontend `depends_on` backend `service_started`, not `service_healthy`.
- Backend healthcheck is TCP to :5000, not `/api/health`.
- `start.sh --build` is a last resort. Prefer:
  `docker compose build backend` then `docker compose up -d --force-recreate --no-deps backend`
- After Docker `unexpected EOF` / BuildKit RPC errors: restart Docker Desktop first.

## Branding

Visible names: **PocketPro:NYL**. Tab title in `frontend/public/index.html`. Concierge copy in `ChatPanelRAG.js`. Do not reintroduce "Mensa Project" in UI.

## Chat & UI Design Guidelines

- **Chat Interface Focus**: Keep the chat experience clean and front-and-center. Secondary tasks, controls, and parameters must be tucked into a collapsible card or minimized accordion menu by default so the conversation owns the space.
- **RAG & Model Rigor**: Implement formal evaluation loops for RAG performance, retrieval relevance, hallucination checking, and answer faithfulness against known draw data.

## Agentic Workflows & Architecture

- **Coordinator Pattern (Primary Assistant)**: The main assistant dynamically clones or spawns specialized worker instances to break down complex instructions.
- **Specialized Agents**:
  - **Web Search Agent**: Equipped with external web tools for real-time validation and data retrieval.
  - **File Manipulation Agent**: Tooled for safe file system reads, writes, and cache/log updates.
- **Execution Guardrails**:
  - **Independent Premise Verification**: Always independently trace or calculate steps before confirming results. Never start answers with blind assertions.
  - **Module & Import Consistency**: Verify path structures (`__init__.py`, correct filenames like `ingestion.py`) prior to proposing imports.
  - **Step-by-Step Troubleshooting Loop**: Diagnose via container logs, plan minimal required file patches, and verify builds locally.

## What to change vs what not to

- Source-only backend fix: rebuild **backend** only, no `--no-cache`, do not recreate chroma.
- UI copy/progress: rebuild **frontend** only (`FRONTEND_CACHE_BUSTER` if webpack cache sticks).
- Do not recreate backend while a long ingest is healthy unless the user wants a restart.
- Do not edit `E:\2024 RESET\concierge` from this workspace.

## Verify

```powershell
curl.exe -s --max-time 8 [http://127.0.0.1](http://127.0.0.1):<FRONTEND>/api/health
curl.exe -s --max-time 8 [http://127.0.0.1](http://127.0.0.1):<BACKEND>/api/startup_status