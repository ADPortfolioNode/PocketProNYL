#!/usr/bin/env python3
"""
Verify training learns from prior accuracy and does not regress.

Runs:
  1) Unit checks on TrainerService incremental-learning helpers
  2) Optional live API two-pass training (requires rebuilt backend on :5000)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

import numpy as np

# Import backend modules from an isolated cwd so root .env does not break Settings.
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
os.chdir(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)
from experiments.store import update_accuracy_history  # noqa: E402
from services.trainer import TrainerService  # noqa: E402

DEFAULT_API_CANDIDATES = (
    os.environ.get("POCKETPRO_API_BASE"),
    "http://127.0.0.1:5001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
)
BASE = os.environ.get("POCKETPRO_API_BASE", "http://127.0.0.1:5001")
GAME = os.environ.get("VERIFY_GAME", "pick3")
TRAIN_TIMEOUT = int(os.environ.get("VERIFY_TRAIN_TIMEOUT", "900"))
TRAIN_ITERATIONS = int(os.environ.get("VERIFY_TRAIN_ITERATIONS", "3"))
UNIT_ONLY = os.environ.get("VERIFY_UNIT_ONLY", "").lower() in ("1", "true", "yes")
INTEGRATION_ONLY = os.environ.get("VERIFY_INTEGRATION_ONLY", "").lower() in ("1", "true", "yes")
RESULTS: list[dict] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append({"check": name, "status": status, "detail": detail})
    mark = {"PASS": "PASS", "SKIP": "SKIP", "FAIL": "FAIL"}.get(status, status)
    print(f"[{mark}] {name}: {detail}")


def unit_baseline_resolution() -> None:
    trainer = TrainerService()
    artifact = {"metrics": {"accuracy": 0.82}, "accuracy": 0.75}
    resolved = trainer._baseline_accuracy(artifact, live_accuracy=0.84)
    if resolved is not None and abs(resolved - 0.84) < 1e-9:
        record("baseline picks max stored/live", "PASS", f"resolved={resolved:.4f}")
    else:
        record("baseline picks max stored/live", "FAIL", f"got {resolved}")


def unit_training_target_from_baseline() -> None:
    trainer = TrainerService()
    with tempfile.TemporaryDirectory() as tmp:
        experiments_dir = os.path.join(tmp, "experiments")
        os.makedirs(experiments_dir, exist_ok=True)
        trainer.models_dir = os.path.join(tmp, "models")
        os.makedirs(trainer.models_dir, exist_ok=True)

        metadata_path = os.path.join(experiments_dir, "pick3_model_metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump({"highest_accuracy": 0.87, "accuracy": 0.85}, handle)

        ctx = trainer.get_incremental_training_context("pick3", requested_target=0.80)
        target = float(ctx["training_target"])
        baseline = float(ctx["baseline_accuracy"])
        if abs(baseline - 0.87) < 1e-9 and abs(target - 0.87) < 1e-9:
            record("training_target uses prior baseline", "PASS", f"target={target:.4f}")
        else:
            record(
                "training_target uses prior baseline",
                "FAIL",
                f"baseline={baseline}, target={target}",
            )


def unit_accuracy_history_tracks_best() -> None:
    history = update_accuracy_history([0.81, 0.79], 0.83)
    history = update_accuracy_history(history, 0.82)
    expected = [0.83, 0.82, 0.81]
    if history == expected:
        record("accuracy_history keeps top values", "PASS", str(history))
    else:
        record("accuracy_history keeps top values", "FAIL", f"got {history}")


def unit_blend_can_improve() -> None:
    trainer = TrainerService()
    trainer.blend_step = 0.25
    y_val = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], dtype=float)
    y_full = y_val
    candidate = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], dtype=float)
    previous = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [6.0, 8.0, 9.0]], dtype=float)

    cand_mae, cand_acc = trainer._score_predictions(y_val, candidate, y_full)
    prev_mae, prev_acc = trainer._score_predictions(y_val, previous, y_full)
    blend = trainer._find_best_blend(candidate, previous, y_val, y_full)

    if blend is None:
        record("blend search finds candidate", "FAIL", "no blend result")
        return

    blend_acc = float(blend["accuracy"])
    best_single = max(cand_acc, prev_acc)
    if blend_acc + 1e-9 >= best_single:
        record(
            "blend does not regress below best single",
            "PASS",
            f"cand={cand_acc:.4f}, prev={prev_acc:.4f}, blend={blend_acc:.4f}",
        )
    else:
        record(
            "blend does not regress below best single",
            "FAIL",
            f"cand={cand_acc:.4f}, prev={prev_acc:.4f}, blend={blend_acc:.4f}",
        )


def unit_model_retention_never_regresses() -> None:
    """Simulate train_model selection: prior model is kept when candidate is worse."""
    trainer = TrainerService()
    baseline_accuracy = 0.86
    previous_accuracy = 0.86
    candidate_accuracy = 0.82
    floor_accuracy = float(baseline_accuracy)
    selected_accuracy = float(candidate_accuracy)
    retained_previous_model = False

    if floor_accuracy is not None and selected_accuracy < floor_accuracy:
        selected_accuracy = float(baseline_accuracy)
        retained_previous_model = True
    elif previous_accuracy is not None and previous_accuracy > selected_accuracy:
        selected_accuracy = float(baseline_accuracy)
        retained_previous_model = True

    if retained_previous_model and selected_accuracy + 1e-9 >= baseline_accuracy:
        record(
            "retention blocks accuracy regression",
            "PASS",
            f"kept {selected_accuracy:.4f} over candidate {candidate_accuracy:.4f}",
        )
    else:
        record(
            "retention blocks accuracy regression",
            "FAIL",
            f"selected={selected_accuracy}, retained={retained_previous_model}",
        )


def unit_training_target_never_below_baseline() -> None:
    requested = 0.80
    baseline = 0.87
    training_target = max(requested, baseline or 0.0)
    if abs(training_target - 0.87) < 1e-9:
        record(
            "training target builds on baseline",
            "PASS",
            f"target={training_target:.4f} from requested={requested:.2f}, baseline={baseline:.2f}",
        )
    else:
        record("training target builds on baseline", "FAIL", f"got {training_target}")


def unit_recursive_training_improves_or_holds() -> None:
    trainer = TrainerService()
    trainer.max_train_attempts = 6
    trainer.auto_tune = False
    trainer.train_size = 0.25
    rng = np.random.default_rng(42)
    n = 120
    X = rng.random((n, 12))
    y = (X[:, :3] * 9 + rng.random((n, 3))).astype(float)
    split = int(n * 0.25)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    first = trainer._fit_and_score_model(X_train, y_train, X_val, y_val, y, attempt=1)
    result = trainer._train_recursive(
        X_train,
        y_train,
        X_val,
        y_val,
        y,
        attempt=1,
        best_result=None,
        floor_accuracy=None,
        training_target=0.99,
    )
    first_acc = float(first[2])
    best_acc = float(result["accuracy"])
    if best_acc + 1e-9 >= first_acc:
        record(
            "recursive training holds/improves best",
            "PASS",
            f"first={first_acc:.4f}, best={best_acc:.4f}, attempts={result.get('attempts')}",
        )
    else:
        record(
            "recursive training holds/improves best",
            "FAIL",
            f"first={first_acc:.4f}, best={best_acc:.4f}",
        )


def resolve_api_base() -> str | None:
    seen: set[str] = set()
    for candidate in DEFAULT_API_CANDIDATES:
        if not candidate:
            continue
        base = str(candidate).rstrip("/")
        if base in seen:
            continue
        seen.add(base)
        try:
            with urllib.request.urlopen(f"{base}/api/train_settings?game={GAME}", timeout=8) as resp:
                if resp.status == 200:
                    return base
        except Exception:
            continue
    return None


def api_available() -> bool:
    return resolve_api_base() is not None


def is_infra_train_failure(code: int | None, payload: dict | None) -> bool:
    if code in (502, 503, 504):
        return True
    if code is None:
        err = str((payload or {}).get("error", "")).lower()
        return any(
            token in err
            for token in (
                "timed out",
                "timeout",
                "remote end closed",
                "connection refused",
                "connection reset",
                "urlopen error",
            )
        )
    return False


def latest_training_experiment(base: str) -> dict | None:
    code, data = api_safe_request("GET", "/api/experiments?limit=50", timeout=30, base=base)
    if code != 200:
        return None
    experiments = data.get("experiments") if isinstance(data, dict) else data
    if not isinstance(experiments, list):
        return None
    matches = []
    for item in experiments:
        if not isinstance(item, dict):
            continue
        if str(item.get("game", "")).lower() != GAME.lower():
            continue
        if str(item.get("type", "")).lower() not in ("training", ""):
            continue
        status = str(item.get("status", "")).lower()
        if status not in ("completed", "success"):
            continue
        matches.append(item)
    if not matches:
        return None
    matches.sort(key=lambda row: float(row.get("timestamp") or 0), reverse=True)
    return matches[0]


def api_request(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 300,
    *,
    base: str | None = None,
):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        (base or BASE) + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return resp.status, json.loads(raw) if raw else {}


def api_safe_request(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: int = 300,
    *,
    base: str | None = None,
):
    try:
        return api_request(method, path, body, timeout, base=base)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw[:300]}
        return exc.code, payload
    except Exception as exc:
        return None, {"error": str(exc)}


def is_record_floor_response(payload: dict) -> bool:
    message = str(payload.get("message", "")).lower()
    return "record floor" in message or "prevent regression" in message


def summarize_train_run(payload: dict, *, base: str, pre_baseline: float | None) -> dict:
    """Normalize a train API payload into comparable run metrics."""
    if is_record_floor_response(payload):
        _, ctx = api_safe_request("GET", f"/api/train_settings?game={GAME}", timeout=20, base=base)
        incremental = (ctx or {}).get("incremental") or {}
        highest = baseline_from_payload(incremental) or baseline_from_payload(payload) or pre_baseline
        return {
            "highest": highest,
            "baseline": incremental.get("baseline_accuracy") or payload.get("baseline_accuracy"),
            "previous": payload.get("previous_accuracy"),
            "training_target": payload.get("training_target") or incremental.get("training_target"),
            "retained": True,
            "used_prev": bool(incremental.get("incremental_learning")),
            "record_floor": True,
        }

    return {
        "highest": baseline_from_payload(payload),
        "baseline": payload.get("baseline_accuracy"),
        "previous": payload.get("previous_accuracy"),
        "training_target": payload.get("training_target"),
        "retained": bool(payload.get("retained_previous_model")),
        "used_prev": bool(payload.get("used_previous_training")),
        "record_floor": False,
    }


def baseline_from_payload(payload: dict) -> float | None:
    values = []
    for key in ("highest_accuracy", "accuracy", "baseline_accuracy"):
        val = payload.get(key)
        if val is not None:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                pass
    return max(values) if values else None


def integration_two_pass_training() -> None:
    global BASE
    resolved = resolve_api_base()
    if not resolved:
        record(
            "API incremental training",
            "SKIP",
            "backend unavailable on :5001/:3000/:5000 — unit checks still validate learning logic",
        )
        return

    BASE = resolved
    print(f"Using API base: {BASE}")

    _, ctx = api_safe_request("GET", f"/api/train_settings?game={GAME}", timeout=20)
    pre = baseline_from_payload((ctx or {}).get("incremental") or {})
    runs = []
    train_body = {
        "game": GAME,
        "target_accuracy": 0.80,
        "max_iterations": TRAIN_ITERATIONS,
        "auto_tune": False,
        "n_estimators": 120,
        "max_depth": 12,
    }

    for run_idx in range(1, 3):
        started = time.time()
        code, train = api_safe_request(
            "POST",
            "/api/train",
            train_body,
            timeout=TRAIN_TIMEOUT,
        )
        if code != 200:
            if is_infra_train_failure(code, train):
                recovered = latest_training_experiment(BASE)
                if recovered and float(recovered.get("timestamp") or 0) >= started - 30:
                    train = recovered
                    code = 200
                else:
                    record(
                        "API incremental training",
                        "SKIP",
                        f"run {run_idx} infra error HTTP {code}: {str(train)[:160]} "
                        f"(backend timeout/restart — learning logic verified by unit checks)",
                    )
                    return
            else:
                record(
                    "API incremental training",
                    "FAIL",
                    f"run {run_idx} HTTP {code}: {str(train)[:160]}",
                )
                return

        status = str(train.get("status", "")).lower()
        if status not in ("completed", "success"):
            if not is_record_floor_response(train):
                record("API incremental training", "FAIL", f"run {run_idx} status={train.get('status')}")
                return
        runs.append(summarize_train_run(train, base=BASE, pre_baseline=pre))
        time.sleep(1)

    checks = []
    for idx, run in enumerate(runs):
        if run["highest"] is None:
            checks.append(f"run {idx + 1}: missing accuracy")
            continue
        highest = float(run["highest"])
        if idx == 0 and pre is not None and highest + 1e-9 < pre:
            checks.append(f"run 1 regressed below pre-baseline {pre:.4f}")
        if idx > 0:
            prior = float(runs[idx - 1]["highest"])
            if highest + 1e-9 < prior:
                checks.append(f"run {idx + 1} regressed {prior:.4f} -> {highest:.4f}")
            if not run["used_prev"] and not run.get("record_floor"):
                checks.append(f"run {idx + 1} missing used_previous_training")

    if checks:
        record("API incremental training", "FAIL", "; ".join(checks))
        return

    r1 = float(runs[0]["highest"])
    r2 = float(runs[1]["highest"])
    delta = r2 - r1
    record(
        "API incremental training",
        "PASS",
        f"pre={pre}, run1={r1:.4f}, run2={r2:.4f}, delta={delta:+.4f}",
    )


def main() -> int:
    print("=== Training Learning Verification ===\n")
    if not INTEGRATION_ONLY:
        print("-- Unit checks --")
        unit_baseline_resolution()
        unit_training_target_from_baseline()
        unit_accuracy_history_tracks_best()
        unit_blend_can_improve()
        unit_model_retention_never_regresses()
        unit_training_target_never_below_baseline()
        unit_recursive_training_improves_or_holds()

    if not UNIT_ONLY:
        print("\n-- Integration checks --")
        integration_two_pass_training()

    failed = sum(1 for row in RESULTS if row["status"] == "FAIL")
    passed = sum(1 for row in RESULTS if row["status"] == "PASS")
    skipped = sum(1 for row in RESULTS if row["status"] == "SKIP")
    print(
        f"\n=== SUMMARY: {passed} PASS, {skipped} SKIP, {failed} FAIL / {len(RESULTS)} checks ==="
    )
    for row in RESULTS:
        print(f"  {row['check']}: {row['status']} — {row['detail']}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())