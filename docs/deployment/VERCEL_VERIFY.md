Vercel Local Verification & Smoke Tests

1) Install & login
- Install Vercel CLI (global):
  - PowerShell
```powershell
npm install -g vercel
npx vercel login
```
  - Alternatively run the helper:
```powershell
.\scripts\install-vercel.ps1
```

2) Link project (once)
```bash
npx vercel link
# follow prompts to select the existing project or create a new one
```

3) Local dev options
- Fast feedback (CRA dev server):
```bash
npm --prefix frontend install
npm --prefix frontend start
```
- Run Vercel emulation (optional):
```bash
npx vercel dev
```

4) Build verification
```bash
npm --prefix frontend ci
npm --prefix frontend run build
test -f frontend/build/index.html && echo build_ok
```

5) Serve the production build locally
```bash
npx serve -s frontend/build -l 3000
```

6) Smoke tests
- Frontend root (should return 200):
```bash
curl -I http://localhost:3000/
```
- Backend API health (PowerShell):
```powershell
Invoke-RestMethod -Uri "http://localhost:5000/api/startup_status" -Method GET
```
- Or bash:
```bash
curl -sS "${REACT_APP_API_BASE:-http://localhost:5000}/api/startup_status" | jq .
```

7) Troubleshooting
- If frontend cannot reach API in production, verify `REACT_APP_API_BASE` in Vercel Project Settings.
- For CORS issues, ensure the backend allows the Vercel origin or use a proxy layer.
