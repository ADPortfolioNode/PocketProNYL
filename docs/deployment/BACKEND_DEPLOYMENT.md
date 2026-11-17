Backend Deployment Audit & Manifests (Render / Fly)

What I inspected
- `backend/Dockerfile` — Docker-based Python app with `hypercorn` ASGI server and an expectation that `/data` is a writable persistent directory.
- `backend/requirements.txt` — contains heavy native libraries (TensorFlow, chromadb) not suitable for serverless short-lived functions.
- `compose.yaml` — shows how services expect a `DATA_DIR` volume and `CHROMA_HOST` connectivity.

Recommendation summary
- Do NOT use Vercel Serverless for the backend. Use a container host that supports persistent volumes and custom Docker images (Render, Fly, DigitalOcean App Platform, Railway, or self-hosted).
- Create a persistent disk/volume mounted at `/data` and set `DATA_DIR=/data`.
- Expose port 5000 internally and map to 80/443 externally.
- Configure Chroma (if used externally) to be accessible to the backend, or run Chroma as its own service with networking and service discovery.

Files added
- `render.yaml` — Render service manifest (Docker-based) with a persistent disk `mensa-data` mounted at `/data`.
- `fly.toml` — Fly manifest referencing `backend/Dockerfile`, creating a mount `mensa_data` to `/data` and exposing port 80.

Render deployment steps (quick)
1. Create a new Web Service in Render and choose "Docker".
2. Point the service at this repository and set the Dockerfile path to `backend/Dockerfile`.
3. Create a persistent disk named `mensa-data` and mount it at `/data` (Render UI → Disks).
4. Add environment variables in Render's service settings: `DATA_DIR=/data`, `CHROMA_HOST` and `CHROMA_PORT`, and secrets (`GEMINI_API_KEY`, `OPENAI_API_KEY`, etc.).
5. Deploy and monitor logs in Render.

Fly deployment steps (quick)
1. Install `flyctl` and authenticate: `flyctl auth login`.
2. Create a volume:
   ```bash
   fly volumes create mensa_data --size 20 --region iad
   ```
3. Set required secrets:
   ```bash
   fly secrets set GEMINI_API_KEY=... OPENAI_API_KEY=... CHAT_GPT_API_KEY=...
   ```
4. Deploy using the provided `fly.toml`:
   ```bash
   fly deploy --config fly.toml
   ```

Operational notes
- Scale: Start with a single instance to preserve local in-memory startup state (the app currently uses a single-worker pattern). If you scale horizontally, move startup/ingestion state to an external store to coordinate jobs.
- Backups: Persist the `/data` volume and back it up (Chroma DB files, models, experiments) regularly.
- Monitoring: Hook up logs and health checks; the Dockerfile provides a healthcheck on port 5000.

Next steps I can implement
- Produce a Render `service` YAML variant tuned to your account (I need your Render service slug and region).
- Produce a GitHub Actions workflow to build/push the backend Docker image to a registry and deploy to Fly/Render (requires registry credentials).
