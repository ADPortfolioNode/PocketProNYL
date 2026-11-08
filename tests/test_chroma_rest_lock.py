"""Regression test for the Chroma REST lock deadlock.

Bug: CHROMA_REST_LOCK was a plain threading.Lock(), but _rest_count holds
it and calls _refresh_collection_catalog, which acquires it again. The
first count for a game with no collection yet (fresh/empty Chroma) then
deadlocks forever: the worker thread never returns and, on a
single-worker server, every later request queues behind it.

Fix: the lock is a threading.RLock().

No live Chroma server is needed: the REST API is faked (empty collection
list), so _rest_count must return 0. On the old Lock() code the call never
returns, so the test runs it in a worker thread with a timeout and fails
if the thread is still running.
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import services.chroma_client as chroma_client_mod  # noqa: E402
from services.chroma_client import CHROMA_REST_LOCK, ChromaClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


@pytest.fixture
def fake_empty_chroma(monkeypatch):
    """Pretend the Chroma REST API answers with no collections at all."""

    def fake_get(url, timeout=None, **kwargs):
        assert "/api/v1/collections" in url
        return _FakeResponse([])

    monkeypatch.setattr(chroma_client_mod.requests, "get", fake_get)


def test_rest_count_returns_promptly_for_game_with_no_collection(fake_empty_chroma):
    """_rest_count for a game with no collection yet must return 0, fast.

    This is the exact deadlock path: _rest_count holds CHROMA_REST_LOCK
    and _refresh_collection_catalog (force=True) acquires it again. With a
    plain Lock the worker thread blocks forever; the join timeout turns
    that into a loud failure instead of a hung test run.
    """
    client = ChromaClient()
    result = {}

    def run():
        result["value"] = client._rest_count("take5_no_collection_yet")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), (
        "_rest_count never returned — CHROMA_REST_LOCK deadlocked "
        "(is it still a plain threading.Lock()?)"
    )
    assert result["value"] == 0


def test_rest_lock_is_reentrant():
    """The REST lock must allow the same thread to acquire it twice.

    _rest_count -> _refresh_collection_catalog acquires the lock while it
    is already held by the caller; a non-reentrant lock deadlocks there.
    """
    assert isinstance(CHROMA_REST_LOCK, type(threading.RLock()))
    assert CHROMA_REST_LOCK.acquire(blocking=False)
    try:
        assert CHROMA_REST_LOCK.acquire(blocking=False)
        CHROMA_REST_LOCK.release()
    finally:
        CHROMA_REST_LOCK.release()
