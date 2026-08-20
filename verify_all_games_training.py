#!/usr/bin/env python3
"""Verify all games train by building off previous/highest accuracy."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("POCKETPRO_API_BASE", "http://127.0.0.1:5001")
TRAIN_TIMEOUT = int(os.environ.get("VERIFY_TRAIN_TIMEOUT", "900"))
GAMES = [
    "take5", "pick3", "powerball", "megamillions",
    "pick10", "cash4life", "quickdraw", "nylotto",
]
LOW_TARGET = 0.80
MAX_ITERATIONS = 8
RESULTS = []


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
        return resp.status, json.loads(raw) if raw else {}


def safe_request(method, path, body=None, timeout=30):
    try:
        return request(method, path, body, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw[:300]}
        return exc.code, payload
    except Exception as exc:
        return None, {"error": str(exc)}


def baseline_from_meta(meta: dict) -> float | None:
    if not meta:
        return None
    values = []
    for key in ("highest_accuracy", "accuracy", "baseline_accuracy"):
        val = meta.get(key)
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
    return max(values) if values else None


def record(game, status, detail=""):
    RESULTS.append({"game": game, "status": status, "detail": detail})
    mark = "PASS" if status == "PASS" else "FAIL"
    print(f"[{mark}] {game}: {detail}")


def verify_game(game: str, pre_meta: dict):
    pre_baseline = baseline_from_meta(pre_meta)

    code, train = safe_request(
        "POST",
        "/api/train",
        {"game": game, "target_accuracy": LOW_TARGET, "max_iterations": MAX_ITERATIONS},
        timeout=TRAIN_TIMEOUT,
    )
    if code != 200:
        record(game, "FAIL", f"train HTTP {code}: {str(train)[:160]}")
        return

    status = str(train.get("status", "")).lower()
    if status not in ("completed", "success"):
        record(game, "FAIL", f"train status={train.get('status')}: {train.get('message', '')[:120]}")
        return

    highest = train.get("highest_accuracy", train.get("accuracy", train.get("score")))
    baseline = train.get("baseline_accuracy")
    training_target = train.get("training_target")
    requested = train.get("target_accuracy", LOW_TARGET)
    candidate = train.get("candidate_accuracy")
    previous = train.get("previous_accuracy")
    retained = bool(train.get("retained_previous_model"))
    used_prev = bool(train.get("used_previous_training"))

    checks = []

    if highest is None:
        checks.append("missing highest_accuracy/accuracy")
    else:
        highest = float(highest)

    if training_target is None:
        checks.append("missing training_target")
    else:
        training_target = float(training_target)
        effective_baseline = pre_baseline
        if effective_baseline is None and baseline is not None:
            effective_baseline = float(baseline)
        expected_target = max(float(requested), effective_baseline or 0.0)
        if abs(training_target - expected_target) > 1e-6:
            checks.append(
                f"training_target {training_target:.4f} != expected {expected_target:.4f}"
            )

    if baseline is not None and highest is not None and highest + 1e-9 < float(baseline):
        checks.append(f"highest {highest:.4f} < baseline_accuracy {float(baseline):.4f}")

    if pre_baseline is not None or baseline is not None:
        ref_baseline = pre_baseline if pre_baseline is not None else float(baseline)
        if ref_baseline is not None and not used_prev:
            checks.append("expected used_previous_training=true when prior model exists")
        if highest is not None and ref_baseline is not None and highest + 1e-9 < ref_baseline:
            checks.append(
                f"regression: highest {highest:.4f} < baseline {ref_baseline:.4f}"
            )
        if retained:
            if previous is None or candidate is None:
                checks.append("retained model but missing previous/candidate accuracy")
            elif not (float(previous) >= float(candidate)):
                checks.append(
                    f"retained but previous {float(previous):.4f} < candidate {float(candidate):.4f}"
                )
            if highest is not None and abs(highest - float(previous or ref_baseline)) > 1e-6:
                checks.append("retained but highest_accuracy != previous_accuracy")
        elif ref_baseline is not None and highest is not None and highest + 1e-9 < ref_baseline:
            checks.append("not retained but accuracy regressed")

    if pre_baseline is None and baseline is None and highest is None:
        checks.append("first train produced no accuracy")

    if checks:
        record(game, "FAIL", "; ".join(checks))
        return

    pre_pct = f"{pre_baseline * 100:.2f}%" if pre_baseline is not None else "none"
    hi_pct = f"{highest * 100:.2f}%" if highest is not None else "N/A"
    tgt_pct = f"{training_target * 100:.2f}%" if training_target is not None else "N/A"
    if retained:
        mode = "retained"
    elif pre_baseline is not None and highest is not None and highest > pre_baseline:
        mode = "improved"
    else:
        mode = "new/held"
    cand_pct = f"{float(candidate) * 100:.2f}%" if candidate is not None else "n/a"
    record(
        game,
        "PASS",
        f"pre={pre_pct} -> highest={hi_pct}, target={tgt_pct}, {mode}, candidate={cand_pct}",
    )


def main():
    print("=== All-Games Training Accuracy Verification ===")
    print(f"Request target={LOW_TARGET}, max_iterations={MAX_ITERATIONS}\n")

    code, health = safe_request("GET", "/api/health", timeout=10)
    if code != 200 or health.get("status") != "healthy":
        print(f"FAIL: backend unhealthy ({health})")
        sys.exit(1)

    code, meta_resp = safe_request("GET", "/api/models/metadata", timeout=30)
    pre_by_game = {}
    if code == 200:
        for item in meta_resp.get("models", []):
            if isinstance(item, dict) and item.get("game"):
                pre_by_game[item["game"]] = item

    for game in GAMES:
        print(f"\n--- Training {game} ---")
        verify_game(game, pre_by_game.get(game, {}))
        time.sleep(1)

    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    print(f"\n=== SUMMARY: {passed} PASS, {failed} FAIL / {len(RESULTS)} games ===")
    for row in RESULTS:
        print(f"  {row['game']}: {row['status']} — {row['detail']}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()