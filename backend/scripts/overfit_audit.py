"""Per-game overfit audit for PocketProNYL.

Stdlib only. This script deliberately imports NOTHING from the backend
(the old version imported PredictionAdapter, which pulls in
pydantic_settings, chromadb and scikit-learn, so it crashed on any plain
Python install with ModuleNotFoundError).

Default mode -- API mode, no backend deps needed:
    python scripts/overfit_audit.py [--api-base http://localhost:5002]

Queries the RUNNING backend (start it first with ./start.sh from the
repo root) and prints one row per game:
    optimum config (the app-wide per-game config), stored highest
    accuracy, has_saved_model, and -- when the backend has the
    /api/overfit_audit endpoint (PR: audit API mode) -- the last
    backtest's validation %, holdout %, gap, holdout lift, holdout
    p-value, exact rate, and overfit gate result.

If the backend does not have /api/overfit_audit yet, holdout columns
show "backend update needed" instead of invented numbers; optimum
config and stored accuracy still report.

Fresh-backtest mode -- needs a full backend environment:
    python scripts/overfit_audit.py --local-backtest

Runs a live walk-forward backtest per game through PredictionAdapter.
Only this mode imports backend code, and only when the flag is passed.
Use a venv with backend/requirements.txt installed for it.

"Accuracy" here is partial-hit rate on the final untouched holdout
(most recent draws, scored once after all tuning), NOT the validation
score the model was selected on. If holdout lift is ~1.0 and the
p-value is not significant, the honest conclusion is "no edge found",
not a bigger model.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

GAMES = [
    "take5",
    "pick3",
    "powerball",
    "megamillions",
    "pick10",
    "cash4life",
    "numbers",
    "win4",
]

OPTIMUM_KEYS = (
    "target_accuracy",
    "max_iterations",
    "train_size",
    "n_estimators",
    "max_depth",
    "window_size",
    "blend_step",
    "overfit_gap_limit_percent",
)


def _get_json(api_base: str, path: str) -> dict | None:
    """GET a backend JSON endpoint, or None on any failure."""
    url = api_base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _optimum_from_payload(payload: dict | None, game: str) -> dict:
    """Extract a game's optimum config from a train_settings payload."""
    if not isinstance(payload, dict):
        return {}
    per_game = payload.get("per_game") or {}
    entry = per_game.get(game) or {}
    profile = entry.get("optimized_defaults") or {}
    if profile:
        return {key: profile.get(key) for key in OPTIMUM_KEYS}
    if payload.get("game") == game:
        profile = payload.get("optimized_defaults") or {}
        return {key: profile.get(key) for key in OPTIMUM_KEYS}
    return {}


def audit_game_api(api_base: str, game: str, settings: dict | None) -> dict:
    """Build one audit row from backend API data (no local imports)."""
    if settings is None:
        settings = _get_json(api_base, "/api/train_settings")
    per_game = (settings or {}).get("per_game") or {}
    entry = per_game.get(game) or {}
    if not entry:
        single = _get_json(api_base, f"/api/train_settings/{game}")
        if single:
            entry = {
                "optimized_defaults": single.get("optimized_defaults") or {},
                "highest_accuracy": single.get("highest_accuracy"),
                "has_saved_model": (single.get("incremental") or {}).get(
                    "has_saved_model"
                ),
            }
    return {
        "game": game,
        "status": "ok" if entry else "error",
        "optimum_config": _optimum_from_payload(settings, game)
        or {key: (entry.get("optimized_defaults") or {}).get(key) for key in OPTIMUM_KEYS},
        "highest_accuracy": entry.get("highest_accuracy"),
        "has_saved_model": entry.get("has_saved_model"),
        "message": None if entry else "No train_settings entry for game.",
    }


def audit_game_local(game: str) -> dict:
    """Run a live backtest for one game. Backend deps required."""
    # Imports are lazy on purpose: importing this module must never
    # require pydantic_settings, chromadb, or scikit-learn.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services.prediction_adapter import prediction_adapter

    result = prediction_adapter.train_or_backtest(game)
    metrics = result.get("metrics") or {}
    gate = result.get("overfit_gate") or {}
    baseline = metrics.get("holdout_random_baseline") or {}
    return {
        "game": game,
        "status": result.get("status"),
        "validation_accuracy_percent": metrics.get(
            "validation_accuracy_percent"
        ),
        "holdout_accuracy_percent": metrics.get("holdout_accuracy_percent"),
        "overfit_gap_percent": metrics.get("overfit_gap_percent"),
        "holdout_mean_partial_hits": metrics.get("holdout_mean_partial_hits"),
        "holdout_random_baseline_mean_hits": baseline.get(
            "mean_partial_hits"
        ),
        "holdout_lift_vs_random": metrics.get("holdout_lift_vs_random"),
        "holdout_p_value": metrics.get("holdout_p_value"),
        "holdout_evaluated_draws": metrics.get("holdout_evaluated_draws"),
        "exact_match_rate": metrics.get("exact_match_rate"),
        "gate_blocked": bool(gate.get("blocked")),
        "gate_reasons": gate.get("reasons") or [],
        "message": result.get("message"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--games",
        default=",".join(GAMES),
        help="Comma-separated game keys (default: all 8 games).",
    )
    parser.add_argument(
        "--api-base",
        default="http://localhost:5002",
        help="Running backend base URL for API mode (default: %(default)s).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print rows as JSON."
    )
    parser.add_argument(
        "--local-backtest",
        action="store_true",
        help="Run live backtests locally (needs backend deps installed).",
    )
    args = parser.parse_args(argv)
    games = [g.strip().lower() for g in args.games.split(",") if g.strip()]

    if args.local_backtest:
        rows = []
        for game in games:
            try:
                rows.append(audit_game_local(game))
            except Exception as exc:
                rows.append(
                    {
                        "game": game,
                        "status": "error",
                        "message": (
                            "Local backtest needs a full backend environment "
                            f"(venv with backend/requirements.txt): {exc}"
                        ),
                    }
                )
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(
                    f"{row['game']} | {row['status']} | "
                    f"validation={row.get('validation_accuracy_percent')} | "
                    f"holdout={row.get('holdout_accuracy_percent')} | "
                    f"gap={row.get('overfit_gap_percent')} | "
                    f"lift={row.get('holdout_lift_vs_random')} | "
                    f"p={row.get('holdout_p_value')} | "
                    f"gate={'BLOCKED: ' + ','.join(row.get('gate_reasons') or []) if row.get('gate_blocked') else 'pass'}"
                )
        return 0

    # API mode: one settings fetch, one overfit-audit fetch, then rows.
    settings = _get_json(args.api_base, "/api/train_settings")
    overfit = _get_json(args.api_base, "/api/overfit_audit")
    overfit_rows = {}
    if isinstance(overfit, dict):
        for row in overfit.get("games") or []:
            overfit_rows[row.get("game")] = row
    rows = []
    for game in games:
        row = audit_game_api(args.api_base, game, settings)
        holdout = overfit_rows.get(game)
        if holdout:
            row.update(
                {
                    "validation_accuracy_percent": holdout.get(
                        "validation_accuracy_percent"
                    ),
                    "holdout_accuracy_percent": holdout.get(
                        "holdout_accuracy_percent"
                    ),
                    "overfit_gap_percent": holdout.get("overfit_gap_percent"),
                    "holdout_lift_vs_random": holdout.get(
                        "holdout_lift_vs_random"
                    ),
                    "holdout_p_value": holdout.get("holdout_p_value"),
                    "gate_blocked": bool(holdout.get("gate_blocked")),
                    "gate_reasons": holdout.get("gate_reasons") or [],
                }
            )
        else:
            row.update(
                {
                    "validation_accuracy_percent": None,
                    "holdout_accuracy_percent": None,
                    "overfit_gap_percent": None,
                    "holdout_lift_vs_random": None,
                    "holdout_p_value": None,
                    "gate_blocked": False,
                    "gate_reasons": [],
                    "holdout_note": (
                        "Backend update needed: /api/overfit_audit not "
                        "available on this backend yet."
                        if settings is not None
                        else "Backend not reachable at "
                        f"{args.api_base} — start it with ./start.sh first."
                    ),
                }
            )
        rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['game']} | {row['status']} | optimum={row['optimum_config']}")
            print(
                f"    stored highest_accuracy={row.get('highest_accuracy')} "
                f"has_saved_model={row.get('has_saved_model')} "
                f"validation={row.get('validation_accuracy_percent')} "
                f"holdout={row.get('holdout_accuracy_percent')} "
                f"gap={row.get('overfit_gap_percent')} "
                f"lift={row.get('holdout_lift_vs_random')} "
                f"p={row.get('holdout_p_value')} "
                f"gate={'BLOCKED: ' + ','.join(row.get('gate_reasons') or []) if row.get('gate_blocked') else 'pass'}"
            )
            if row.get("holdout_note"):
                print(f"    {row['holdout_note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
