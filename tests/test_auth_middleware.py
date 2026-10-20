"""Tests for the optional API-key middleware (off by default)."""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from middleware.auth import api_key_auth_middleware  # noqa: E402


def _app():
    app = FastAPI()
    app.middleware("http")(api_key_auth_middleware)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/predict")
    def predict():
        return {"ok": True}

    @app.post("/api/train")
    def train():
        return {"ok": True}

    @app.get("/api/ingest_stream")
    def stream():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("AUTH_MODE", "API_KEYS", "ADMIN_API_KEYS"):
        monkeypatch.delenv(k, raising=False)


def test_off_by_default_everything_passes():
    c = _app()
    assert c.post("/api/predict").status_code == 200
    assert c.post("/api/train").status_code == 200


def test_apikey_mode_requires_key(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("API_KEYS", "sub1")
    c = _app()
    assert c.get("/api/health").status_code == 200
    r = c.post("/api/predict")
    assert r.status_code == 401 and r.headers["x-auth-required"] == "apikey"
    assert c.post("/api/predict", headers={"X-API-Key": "sub1"}).status_code == 200
    assert c.post("/api/predict", headers={"Authorization": "Bearer sub1"}).status_code == 200
    assert c.get("/api/ingest_stream?api_key=sub1").status_code == 200
    assert c.post("/api/predict", headers={"X-API-Key": "nope"}).status_code == 401


def test_admin_routes_need_admin_key_when_configured(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    monkeypatch.setenv("API_KEYS", "sub1")
    monkeypatch.setenv("ADMIN_API_KEYS", "boss")
    c = _app()
    assert c.post("/api/train", headers={"X-API-Key": "sub1"}).status_code == 403
    assert c.post("/api/train", headers={"X-API-Key": "boss"}).status_code == 200
    assert c.post("/api/predict", headers={"X-API-Key": "boss"}).status_code == 200


def test_misconfigured_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "apikey")
    assert _app().post("/api/predict").status_code == 503
