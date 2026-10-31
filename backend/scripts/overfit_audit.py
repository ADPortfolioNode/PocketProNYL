"""Per-game overfit audit for PocketProNYL.

Run this on a machine with the live backend data (Chroma), e.g. from
backend/:

    python scripts/overfit_audit.py [--games take5,powerball] [--json]

For each game it runs the modular walk-forward backtest through
PredictionAdapter (which now applies the overfit gate) and prints one
row per game with the numbers that actually show overfitting:

    game, status, validation %, holdout %, gap (pp), holdout mean hits,
    random baseline, lift vs random, holdout p-value, exact-match rate,
    gate blocked?, gate reasons

"Accuracy" here is partial-hit rate on the final untouched holdout
(most recent draws, scored once after all tuning), NOT the validation
score the model was selected on, and NOT the legacy trainer's scaled
MAE accuracy. If holdout lift is ~1.0 and the p-value is not
significant, the honest conclusion is "no edge found", not a bigger
model.

This script needs live draw data; in a sandbox without Chroma it will
report an error per game, not invented numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

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


def optimum_config_for_game(game: str) -> dict:
    """Return this game's default optimum-accuracy config (per-game view).

    This is the per-game profile from utils/training_defaults, the same
    config training defaults to for that game -- not the generic baseline.
    """
    try:
        from utils.training_defaults import get_optimum_config

        profile = get_optimum_config(game) or {}
    except Exception:
        profile = {}
    return {
        key: profile.get(key)
        for key in (
            "target_accuracy",
            "max_iterations",
            "train_size",
            "n_estimators",
            "max_depth",
            "window_size",
            "blend_step",
            "overfit_gap_limit_percent",
        )
    }


def audit_game(adapter, game: str) -> dict:
    """Run one game's backtest and return its audit row."""
    result = adapter.train_or_backtest(game)
    metrics = result.get("metrics") or {}
    gate = result.get("overfit_gate") or {}
    baseline = metrics.get("holdout_random_baseline") or {}
    return {
        "optimum_config": optimum_config_for_game(game),
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
        "--json", action="store_true", help="Print rows as JSON."
    )
    args = parser.parse_args(argv)

    from services.prediction_adapter import prediction_adapter

    games = [g.strip().lower() for g in args.games.split(",") if g.strip()]
    rows = [audit_game(prediction_adapter, game) for game in games]

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        header = (
            "game | status | validation% | holdout% | gap pp | "
            "holdout hits | random | lift | p | exact | gate"
        )
        print(header)
        for row in rows:
            print(
                f"{row['game']} | {row['status']} | "
                f"{row['validation_accuracy_percent']} | "
                f"{row['holdout_accuracy_percent']} | "
                f"{row['overfit_gap_percent']} | "
                f"{row['holdout_mean_partial_hits']} | "
                f"{row['holdout_random_baseline_mean_hits']} | "
                f"{row['holdout_lift_vs_random']} | "
                f"{row['holdout_p_value']} | "
                f"{row['exact_match_rate']} | "
                f"{'BLOCKED: ' + ','.join(row['gate_reasons']) if row['gate_blocked'] else 'pass'}"
            )
            print(f"    optimum config (default per game): {row['optimum_config']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
