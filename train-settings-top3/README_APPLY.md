This archive contains the combined patch `train-settings-top3.patch` which
adds:

- `GET /api/train_settings` and `GET /api/train_settings/{game}` in
  `backend/main.py` (returns trainer defaults + lightweight dataset snapshot)
- Frontend preload logic in `frontend/src/components/Dashboard.js` that fetches
  defaults but preserves user-edited `trainParams`.
- Trainer update in `backend/services/trainer.py` to collect candidate
  attempts, select top-3 by validation accuracy, compute an ensemble, and
  persist the chosen artifact (stores `top_models` and `ensemble_weights`).

Files included:
- train-settings-top3.patch
- README_APPLY.md (this file)

Pre-apply notes
---------------
- Review uncommitted local changes before applying: `git status`.
- If your local repo's `.git` is corrupted, apply patch in a fresh clone
  (recommended). See "Fresh-clone flow" below.
- Back up `/data/models` before running training verification (artifacts
  may grow in size).

Apply in-place (macOS / Linux / Windows PowerShell) - quick method
----------------------------------------------------------------
From your repository root:

macOS / Linux / WSL / PowerShell Core (bash):
```bash
# preview
git apply --check train-settings-top3.patch
# apply
git apply train-settings-top3.patch
# commit
git add -A
git commit -m "Add train settings API + frontend preload and trainer top-3 ensemble"
```

Windows PowerShell (if `git apply` shows conflicts, use a fresh clone):
```powershell
git apply --check .\train-settings-top3.patch
git apply .\train-settings-top3.patch
git add -A
git commit -m "Add train settings API + frontend preload and trainer top-3 ensemble"
```

Fresh-clone flow (recommended if current `.git` was corrupted)
--------------------------------------------------------------
1. Clone fresh and create the branch from `origin/main`:

```bash
cd /path/to/parent
git clone https://github.com/ADPortfolioNode/mensa_project.git mensa_fresh
cd mensa_fresh
git fetch origin
git checkout -b cleanup/remove-debug-scripts origin/main
```

2. Copy only the patch file into the fresh clone (or place this patch into
   the fresh clone directory). Then apply:

```bash
# from mensa_fresh root
git apply --check ../mensa_project/train-settings-top3.patch
git apply ../mensa_project/train-settings-top3.patch
git add -A
git commit -m "Add train settings API + frontend preload and trainer top-3 ensemble"
git push origin cleanup/remove-debug-scripts:cleanup/remove-debug-scripts
```

Restarting services locally
---------------------------
- Backend (dev):
```bash
# from repo root
uvicorn backend.main:app --reload --host 0.0.0.0 --port 5000
```
- Frontend (dev):
```bash
cd frontend
npm install
npm start
```

Verification
------------
- Check the train settings endpoint:
```bash
curl 'http://localhost:5000/api/train_settings'
curl 'http://localhost:5000/api/train_settings?game=pick3'
curl 'http://localhost:5000/api/train_settings/pick3'
```
- Run a manual training and inspect the artifact:
```bash
curl -X POST 'http://localhost:5000/api/train' -H 'Content-Type: application/json' -d '{"game":"pick3"}'
python - <<PY
import joblib, pprint
a=joblib.load('data/models/pick3_model.joblib')
print(a.get('version'))
print('top_models count:', len(a.get('top_models') or []))
pprint.pprint(a.get('ensemble_weights'))
pprint.pprint(a.get('metrics'))
PY
```

OS-specific notes
-----------------
- macOS: use Terminal or iTerm. `git` and `python3` are typically available.
- Windows: prefer PowerShell 7+ or Git Bash. If using PowerShell, prefix paths
  with `.`\ when appropriate.
- Android (Termux): install `git`, `python`, and `curl`. Use the fresh-clone
  flow; avoid applying if the repo's `.git` is corrupted on-device.

If you want a single downloadable zip containing these files, tell me and I
will produce a base64-encoded zip you can decode on any OS (or I can create
separate patch files).