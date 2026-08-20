Vercel Desktop Full Walkthrough — PocketPro:NYL Project

Goal
- Deploy the frontend (`frontend/`) to Vercel with minimal code changes and no regression; host the backend separately on a container-friendly platform and connect via an environment secret.

Summary of what was added
- `vercel.json` — build config, SPA rewrite, and `REACT_APP_API_BASE` secret reference.
- `.vercelignore` — exclude backend, data, and heavy files.
- `.env.example` — example env for local dev.
- `scripts/install-vercel.ps1` — PowerShell helper to install Vercel CLI.
- `VERCEL_DEPLOYMENT.md`, `VERCEL_DASHBOARD.md`, `VERCEL_VERIFY.md`, `VERCEL_SECRETS.md` — step-by-step docs.

Pre-reqs (local)
- Node 18+ and npm
- Vercel account and `vercel` CLI (or Desktop app)
- Backend hosted at an HTTPS URL (Render, Fly, DigitalOcean App Platform, Railway, or a VM) — NOT Vercel functions.

Step 1 — Import repo in Vercel (Desktop)
1. Open Vercel Dashboard → New Project → Import Git Repository.
2. Select this repository and branch `main`.
3. In Import Settings:
   - Root Directory: leave blank
   - Framework Preset: "Create React App" or "Other"
   - Build Command: `npm --prefix frontend run build`
   - Output Directory: `frontend/build`
4. Confirm and finish import.

Step 2 — Create secrets and env
1. In Project Settings → Environment Variables add:
   - Key: `REACT_APP_API_BASE` Value: `@backend_url` (reference to secret)
2. Add the secret `backend_url` (value: your backend public HTTPS URL).
3. Add scoped values for `Preview` and `Production` as appropriate.

Step 3 — Build & Deploy
1. From Vercel Dashboard click Deploy. Vercel will run the build using `frontend/package.json`.
2. For CLI deploy (local):
```bash
npx vercel login
npx vercel link
npx vercel --prod
```

Step 4 — Local verification (quick)
1. Install deps and build:
```bash
npm --prefix frontend ci
npm --prefix frontend run build
```
2. Serve production build:
```bash
npx serve -s frontend/build -l 3000
```
3. Smoke test frontend root and backend health:
```bash
curl -I http://localhost:3000/
curl -sS "${REACT_APP_API_BASE:-http://localhost:5000}/api/startup_status" | jq .
```

Backend hosting guidance (recommended)
- Why not Vercel Serverless: the backend requires heavy native libs (TensorFlow), persistent storage, and ChromaDB which need long-running processes and a persistent filesystem.
- Recommended hosts:
  - Render (Docker or Web Service)
  - Fly.io (Docker)
  - DigitalOcean App Platform (Docker)
  - Railway (Service with volumes)
  - Self-hosted VM or Kubernetes
- Use the provided `backend/Dockerfile` and `compose.yaml` as deployment references. Ensure persistent `/data` volume and Chroma connection.

CORS & Security
- Backend should set a CORS policy allowing your Vercel origin (or use token-based auth). Keep Gemini/OpenAI keys secure and never embed them into the frontend.

Optional: lightweight API proxy on Vercel
- If you need header injection or request transforms, add a small Vercel Serverless function under `/api/*` that proxies requests to the backend. Keep it tiny — do not move core logic to Vercel functions.

Suggested PR (you already pushed branch `feat/vercel-setup`)
- Title: `chore(deploy): add Vercel config, deployment docs, and CLI helper`
- Body: See `VERCEL_DEPLOYMENT.md` and `VERCEL_DASHBOARD.md` for details. Backend hosting must be provisioned separately.

Next steps I can take for you (pick one)
- A: Create PR description file `PR_DESCRIPTION.md` in the branch.
- B: Attempt to open a PR via API (requires a GitHub token).
- C: Start backend audit and produce deployment recommendations and scripts for Render/Fly/DO.
