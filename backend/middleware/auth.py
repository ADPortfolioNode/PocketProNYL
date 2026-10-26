"""
Optional API-key access control for PocketProNYGames.

OFF BY DEFAULT: with AUTH_MODE unset (or "off") every request passes through
unchanged, so local Docker / .bat starts behave exactly as before.

Enable for a paid or public deployment:

    AUTH_MODE=apikey
    API_KEYS=key-for-subscriber-a,key-for-subscriber-b     # read + predict + chat
    ADMIN_API_KEYS=your-own-admin-key                       # optional: train/ingest/admin

Keys are accepted from (first match wins):
    X-API-Key: <key>
    Authorization: Bearer <key>
    ?api_key=<key>        (needed for EventSource streams, which cannot set headers)

When ADMIN_API_KEYS is empty, any valid key may use admin routes (single-owner
setups). Admin keys are always accepted on subscriber routes.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

# Always reachable: container healthchecks and the startup splash screen.
PUBLIC_PATHS = {
    "/api/health",
    "/api/startup_status",
    "/health",
}

# Heavy or destructive operations: training, ingestion, vector-store admin.
ADMIN_PATH_PREFIXES = (
    "/api/train",          # /api/train and /api/train_all
    "/api/ingest",         # POST ingest (the GET stream is matched by method below)
    "/api/startup_init",
    "/collection/",
    "/api/chroma",
)


def _split_keys(raw: str | None) -> list[str]:
    return [k.strip() for k in (raw or "").split(",") if k.strip()]


def auth_mode() -> str:
    return (os.environ.get("AUTH_MODE") or "off").strip().lower()


def auth_enabled() -> bool:
    return auth_mode() in {"apikey", "api_key", "on", "token"}


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    authz = request.headers.get("authorization") or ""
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    query = request.query_params.get("api_key")
    return query.strip() if query else None


def _matches(candidate: str, keys: list[str]) -> bool:
    return any(secrets.compare_digest(candidate, k) for k in keys)


def _is_admin_route(request: Request) -> bool:
    path = request.url.path
    if request.method == "GET" and path.startswith("/api/ingest"):
        return False  # progress/status streams are read-only
    return path.startswith(ADMIN_PATH_PREFIXES)


def _deny(status: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail},
        headers={"X-Auth-Required": "apikey"},
    )


async def api_key_auth_middleware(request: Request, call_next):
    if not auth_enabled():
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)
    # Only the API is gated; anything else (docs, root) passes.
    if not (path.startswith("/api/") or path.startswith("/collection/")):
        return await call_next(request)

    user_keys = _split_keys(os.environ.get("API_KEYS"))
    admin_keys = _split_keys(os.environ.get("ADMIN_API_KEYS"))
    if not user_keys and not admin_keys:
        return _deny(503, "AUTH_MODE is on but no API_KEYS / ADMIN_API_KEYS are configured.")

    key = _extract_key(request)
    if not key:
        return _deny(401, "API key required.")

    is_admin = bool(admin_keys) and _matches(key, admin_keys)
    is_user = is_admin or _matches(key, user_keys)
    if not is_user:
        return _deny(401, "Invalid API key.")

    if _is_admin_route(request) and admin_keys and not is_admin:
        return _deny(403, "This action requires an admin key.")

    request.state.api_key_role = "admin" if is_admin else "user"
    return await call_next(request)
