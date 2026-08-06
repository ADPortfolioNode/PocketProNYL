Vercel Dashboard Setup — Recommended Settings

1) Project import
- Choose the repo and branch `cleanup/remove-debug-scripts` (or `main`).
- Project Name: `pocketpro-nyl-frontend` (or any friendly name).

2) Build & Output
- Root Directory: leave blank (repo root).
- Framework Preset: "Other" or "Create React App".
- Build Command: `npm --prefix frontend run build`
- Output Directory: `frontend/build`

3) Environment Variables / Secrets
- Create a project-level Environment Variable `REACT_APP_API_BASE` and set its value to the secret reference `@backend_url`.
- Add the secret `backend_url` via the Vercel Dashboard or CLI with the backend's public URL (e.g. `https://api.example.com`).

4) Routes & Rewrites
- `vercel.json` already includes a catch-all rewrite to `/index.html` for SPA routing.

5) Preview / Production
- Enable Git integration for preview deployments on PRs.
- For Production, deploy the `main` branch or run `vercel --prod` from your machine.

6) CLI installation and linking
- Install globally (or locally in a dev container):
```bash
npm install -g vercel
# or project-local dev install
npm install --save-dev vercel
```
- Login and link the project:
```bash
npx vercel login
npx vercel link
# Deploy
npx vercel --prod
```

7) Notes
- The `backend/` contains a heavy FastAPI service that is NOT suited for Vercel Serverless. Host the backend on a container-friendly host (Render, Fly, DigitalOcean App Platform, Railway, or self-hosted) and point `backend_url` to its public endpoint.
- If you need a small transform layer between the frontend and backend (CORS, header injection), add a lightweight `api/` function on Vercel and proxy to the backend.
