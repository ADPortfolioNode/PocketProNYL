# Changelog

## Unreleased — production readiness

No behaviour change for existing local starts: every new control is off by default.

### Security
- Optional per-customer API-key gate (`AUTH_MODE=apikey`, `API_KEYS`, `ADMIN_API_KEYS`); admin keys required for train / ingest / vector-store admin when configured. Off by default.
- Frontend sends the stored key automatically (fetch + EventSource) and asks for it only when the server requires one.
- Replaced `eval()` in `backend/utils/game_data_parser.py` with `ast.literal_eval` (no code execution from draw data).

### Architecture
- `backend/utils/paths.py` centralises storage paths. Hard-coded `/data/...` paths in trainer, predictor, tuner, adapter, ingest, routes and state now honour `DATA_DIR`, fixing a mismatch where `MODELS_DIR` only applied in `model_utils`. Docker defaults unchanged.

### Legal / product
- Proprietary `LICENSE`.
- Responsible-play and non-affiliation notice in the app footer and marketing site.

### Packaging
- Runtime image no longer installs `pytest`, `pytest-asyncio`, `docker`, `boto3`, `langchain-community` (none are imported). Dev/test deps moved to `backend/requirements-dev.txt`.
- Duplicate `hypercorn` requirement removed.
- Test deps in `backend/requirements-dev.txt` so CI can run the pytest suite (workflow step to be added in GitHub).

### Repo hygiene
- Removed empty placeholder files and run artifacts (`Removed`, `Using`, `pocketpro_nyl_backend`, `pocketpro_nyl_chroma`, empty `.bat` stubs, `backend/main_new.py` (empty), `grok_logs.csv`, `force_request.json`, `frontend/build-log.txt`, `docs/status/file.tmp`).
- Moved stray `backend/AllGamesSuggestionsPanel.js` to `frontend/src/components/`.
- `StopMensa.bat` (was empty) now forwards to `StopPocketProNYL.bat`, like the other legacy launchers.
