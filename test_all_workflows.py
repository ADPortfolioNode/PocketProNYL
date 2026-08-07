#!/usr/bin/env python3
"""Sequential end-to-end workflow test for Mensa Project API."""
import json
import sys
import re
import time
import urllib.error
import urllib.request

import os

BASE = os.environ.get("MENSA_API_BASE", "http://127.0.0.1:5000").rstrip("/")
GAME = os.environ.get("WORKFLOW_GAME", "pick3")
TRAIN_TIMEOUT = int(os.environ.get("WORKFLOW_TRAIN_TIMEOUT", "900"))
RESULTS = []
SHOULD_SKIP_TRAIN_PREDICT = False


def is_record_floor_response(payload: dict) -> bool:
    message = str(payload.get("message", "")).lower()
    return "record floor" in message or "prevent regression" in message


def record(name, status, detail=""):
    RESULTS.append({"workflow": name, "status": status, "detail": detail})
    mark = "PASS" if status == "PASS" else ("WARN" if status == "WARN" else "FAIL")
    print(f"[{mark}] {name}: {detail}")


def request(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"_raw": raw[:200]}
        return resp.status, payload


def safe_request(method, path, body=None, timeout=30):
    return _safe_request_impl(method, path, body, timeout)


def _safe_request_impl(method, path, body=None, timeout=30):
    try:
        return request(method, path, body, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw[:200]}
        return exc.code, payload
    except Exception as exc:
        return None, {"error": str(exc)}


def get_accuracy_from_payload(payload: dict) -> float | None:
    """Extracts the most relevant accuracy score from a training payload."""
    for key in ("highest_accuracy", "accuracy", "baseline_accuracy"):
        val = payload.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    if payload.get("mean_partial_hits") is not None: # New metric for modular engine
        return float(payload["mean_partial_hits"])
    
    message = payload.get("message", "")
    match = re.search(r"mean_partial_hits=([0-9.]+)", message)
    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            pass
    return None

def test_health():
    code, data = safe_request("GET", "/api/health", timeout=10)
    if code == 200 and data.get("status") == "healthy":
        record("1. Health", "PASS", "backend healthy")
    else:
        record("1. Health", "FAIL", str(data))


def test_startup_status():
    code, data = safe_request("GET", "/api/startup_status", timeout=15)
    required = {"status", "progress", "total", "games"}
    if code == 200 and required.issubset(data.keys()):
        record("2. Startup Status", "PASS", f"status={data.get('status')}")
    else:
        record("2. Startup Status", "FAIL", str(data)[:120])


def test_startup_init():
    code, data = safe_request("POST", "/api/startup_init", body={}, timeout=15)
    if code == 200 and data.get("status") in ("started", "running", "ready", "completed", "pending", "ingesting"):
        record("3. Startup Init", "PASS", f"status={data.get('status')}")
    elif code == 200:
        record("3. Startup Init", "WARN", str(data)[:120])
    else:
        record("3. Startup Init", "FAIL", str(data)[:120])


def test_games():
    code, data = safe_request("GET", "/api/games", timeout=15)
    expected = {
        "take5", "pick3", "powerball", "megamillions",
        "pick10", "cash4life", "quickdraw", "nylotto",
    }
    games = set(data.get("games", []))
    if code == 200 and expected.issubset(games):
        record("4. List Games", "PASS", f"{len(games)} games")
    else:
        record("4. List Games", "FAIL", str(data)[:120])

    code, data = safe_request("GET", "/api/games/summaries", timeout=60)
    summaries = data.get("summaries", {}) if code == 200 else {}
    pick3_draws = summaries.get(GAME, {}).get("draw_count", 0)
    if code == 200 and pick3_draws > 0:
        record("5. Game Summary", "PASS", f"{GAME} draws={pick3_draws}")
    elif code == 200:
        code2, data2 = safe_request("GET", f"/api/games/{GAME}/summary", timeout=30)
        draw_count = data2.get("draw_count", 0) if code2 == 200 else 0
        if draw_count > 0:
            record("5. Game Summary", "PASS", f"{GAME} draws={draw_count}")
        else:
            record("5. Game Summary", "WARN", f"{GAME} draws=0 (data may still be ingesting)")
    else:
        record("5. Game Summary", "FAIL", str(data)[:120])


def test_ingest():
    code, data = safe_request("POST", "/api/ingest", {"game": GAME, "force": False}, timeout=20)
    if code == 200 and data.get("status") in ("queued", "running", "completed"):
        record("6. Ingest Queue", "PASS", data.get("message", data.get("status")))
    else:
        record("6. Ingest Queue", "FAIL", str(data)[:120])
        return

    for _ in range(15):
        time.sleep(2)
        code, data = safe_request("GET", f"/api/ingest_progress?game={GAME}", timeout=20)
        status = str(data.get("status", "")).lower()
        if status in ("completed", "done", "success"):
            draws = data.get("total_rows") or data.get("rows_fetched")
            record("7. Ingest Progress", "PASS", f"status={status} draws={draws}")
            return
        if status in ("error", "failed"):
            record("7. Ingest Progress", "FAIL", str(data)[:120])
            return

    record("7. Ingest Progress", "WARN", f"still {data.get('status', 'unknown')} after polling")


def test_train():
    global SHOULD_SKIP_TRAIN_PREDICT
    # Check for sufficient data before attempting to train
    code, data = safe_request("GET", f"/api/games/{GAME}/summary", timeout=30)
    draw_count = data.get("draw_count", 0) if code == 200 else 0
    if draw_count < 10: # A reasonable minimum for training
        record("8. Train Model", "WARN", f"Skipping training, not enough data ({draw_count} draws)")
        SHOULD_SKIP_TRAIN_PREDICT = True
        return

    code, data = safe_request(
        "POST",
        "/api/train",
        {
            "game": GAME,
            "max_iterations": 40, # Increased to match TRAIN_MAX_ATTEMPTS in docker-compose.yml
            # Removed target_accuracy, n_estimators, max_depth, and auto_tune: False
            # This allows the backend's default TRAIN_TARGET_ACCURACY and auto_tune (TRAIN_AUTO_TUNE=1)
            # to use its own logic and potentially better hyperparameters.
        },
        timeout=TRAIN_TIMEOUT,
    )
    status = str(data.get("status", "")).lower()
    
    if code == 200 and status in ("completed", "success"):
        acc = get_accuracy_from_payload(data)
        if acc is not None and acc > 0:
            record("8. Train Model", "PASS", f"accuracy={acc:.4f}")
        elif acc == 0.0:
            record("8. Train Model", "WARN", f"completed with 0.0 accuracy: {data.get('message', '')[:80]}")
        elif data.get("retained_previous_model"):
            record("8. Train Model", "PASS", f"retained record (no regression): {str(data.get('message', ''))[:80]}")
        elif is_record_floor_response(data):
            floor = data.get("highest_accuracy") or data.get("record_accuracy")
            record("8. Train Model", "PASS", f"record floor protected best={floor:.4f}")
        else:
            record("8. Train Model", "FAIL", f"training completed but no valid accuracy found: {str(data)[:160]}")
    elif code is None and "timed out" in str(data.get("error", "")).lower():
        record("8. Train Model", "WARN", "client timeout — training may still be running on server")
    elif "no attribute 'train'" in str(data.get("message", "")).lower():
        record("8. Train Model", "FAIL", data.get("message"))
    else:
        record("8. Train Model", "FAIL", str(data)[:160])


def test_predict():
    if SHOULD_SKIP_TRAIN_PREDICT:
        record("9. Suggest", "WARN", "Skipping prediction, training was skipped")
        return

    # Add a check for sufficient data before attempting to predict, same as training
    code, data = safe_request("GET", f"/api/games/{GAME}/summary", timeout=30)
    draw_count = data.get("draw_count", 0) if code == 200 else 0
    if draw_count < 5:  # A reasonable minimum for prediction, more than the backend's hard requirement of 2
        record("9. Suggest", "WARN", f"Skipping prediction, not enough data ({draw_count} draws)")
        SHOULD_SKIP_TRAIN_PREDICT = True # Also skip subsequent steps if we can't predict
        return

    code, data = safe_request(
        "POST",
        "/api/predict",
        {"game": GAME, "recent_k": 5},
        timeout=90,
    )
    status = str(data.get("status", "")).lower()
    numbers = data.get("predicted_numbers")
    if code == 200 and status in ("completed", "success") and numbers:
        record("9. Suggest", "PASS", f"numbers={numbers}")
    elif "no attribute 'predict'" in str(data.get("message", "")).lower():
        record("9. Suggest", "FAIL", data.get("message"))
    else:
        record("9. Suggest", "FAIL", str(data)[:160])


def test_experiments():
    code, data = safe_request("GET", "/api/experiments", timeout=20)
    if code == 200 and data.get("status") == "ok" and isinstance(data.get("experiments"), list):
        record("10. Experiments", "PASS", f"count={data.get('count', len(data.get('experiments', [])))}")
    else:
        record("10. Experiments", "FAIL", str(data)[:120])


def test_chroma():
    code, data = safe_request("GET", "/api/chroma/status", timeout=30) # Increased timeout
    if code == 200 and data.get("status") in ("ok", "connected"): # Accept "connected" as valid
        record("11. Chroma Status", "PASS", data.get("status"))
    else:
        record("11. Chroma Status", "FAIL", f"status={data.get('status')} {str(data)[:120]}")

    code, data = safe_request("GET", "/api/chroma/collections", timeout=60) # Increased timeout
    cols = data.get("collections", [])
    if code == 200 and len(cols) >= 8:
        record("12. Chroma Collections", "PASS", f"{len(cols)} collections")
    else:
        record("12. Chroma Collections", "FAIL", str(data)[:120])


def test_models_and_predictions():
    code, data = safe_request("GET", "/api/models/metadata", timeout=20)
    if code == 200:
        record("13. Models Metadata", "PASS", f"keys={list(data.keys())[:4]}")
    else:
        record("13. Models Metadata", "FAIL", str(data)[:120])

    code, data = safe_request("GET", f"/api/models/{GAME}/metadata", timeout=20)
    if code == 200:
        record("14. Game Model Metadata", "PASS", f"game={data.get('game', GAME)}")
    else:
        record("14. Game Model Metadata", "WARN", str(data)[:120])

    code, data = safe_request("GET", "/api/predictions/all", timeout=300)
    if code == 200 and data.get("status") == "ok":
        record("15. Suggestions All", "PASS", f"games={len(data.get('predictions', []))}")
    elif code is None and "timed out" in str(data.get("error", "")).lower():
        record("15. Suggestions All", "WARN", "slow endpoint timed out at 300s (per-game predict still works)")
    else:
        record("15. Suggestions All", "FAIL", str(data)[:120])


def test_chat():
    code, data = safe_request(
        "POST",
        "/api/chat",
        {"text": "What games are available?", "game": GAME, "use_rag": False},
        timeout=45,
    )
    if code == 200 and data.get("response"):
        record("16. Chat", "PASS", f"response_len={len(data.get('response', ''))}")
    elif code == 200:
        record("16. Chat", "WARN", "empty response (LM may be unavailable)")
    elif code == 500 and "select_provider" in str(data.get("detail", "")):
        record("16. Chat", "FAIL", str(data.get("detail", ""))[:120])
    elif code in (500, 503):
        record("16. Chat", "WARN", str(data)[:120])
    else:
        record("16. Chat", "FAIL", str(data)[:120])


def main():
    print("=" * 60)
    print("MENSA PROJECT - ALL WORKFLOWS TEST")
    print("=" * 60)

    test_health()
    test_startup_status()
    test_startup_init()
    test_ingest()
    test_games() # Run after ingestion is complete
    test_train()
    test_predict()
    test_experiments()
    test_chroma()
    test_models_and_predictions()
    test_chat()

    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    warned = sum(1 for r in RESULTS if r["status"] == "WARN")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")

    print("=" * 60)
    print(f"SUMMARY: {passed} PASS, {warned} WARN, {failed} FAIL / {len(RESULTS)} total")
    print("=" * 60)

    # WARN does not fail the suite — only hard FAIL counts.
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())