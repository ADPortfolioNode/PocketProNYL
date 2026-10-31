"""Overfit gate for PocketProNYL backtests.

Stdlib only, so it can be unit-tested without the full backend stack.

The modular evaluator (prediction/metrics/evaluator.py) already reports a
three-way split: validation accuracy, a final untouched holdout scored once,
the validation-minus-holdout gap, and a holdout p-value against random
tickets. Until now those were warnings in the report: a champion with a big
gap, or a holdout that does not beat random, could still be promoted.

This gate turns those warnings into a promotion decision:

- blocked when the validation-vs-holdout gap reaches the evaluator's own
  boundary (2.0 percentage points by default),
- blocked when the holdout does not beat random at the usual 0.05 level,
- blocked when there is no holdout at all, because then the reported
  accuracy was selected on the draws it is scored on and cannot be verified.

The gate never re-scores anything and never looks at the holdout to pick a
winner; it only reads the evaluator's already-computed, once-scored
holdout summary. A blocked run keeps the previous ready model instead of
promoting a new one.

Override (for diagnosis only, never as the normal path):
    OVERFIT_GATE_MODE=warn  -> report the gate, do not block
    OVERFIT_GATE_MODE=off   -> skip the gate entirely
Default is "block".
"""

from __future__ import annotations

import os

DEFAULT_GAP_BOUNDARY_PERCENT = 2.0
DEFAULT_SIGNIFICANCE_ALPHA = 0.05

_BLOCK_REASONS = (
    "validation_beats_holdout",
    "holdout_not_significant_vs_random",
    "no_holdout",
)


def gate_mode() -> str:
    """Return the configured gate mode: block (default), warn, or off."""
    mode = str(os.environ.get("OVERFIT_GATE_MODE", "block")).strip().lower()
    return mode if mode in ("block", "warn", "off") else "block"


def evaluate_overfit_gate(metrics: dict | None) -> dict:
    """Decide whether a backtest result may be promoted.

    Returns a dict with:
        blocked: bool -- True only in block mode with a blocking reason.
        reasons: list[str] -- blocking reasons found (empty when clean).
        mode: str -- the gate mode that was applied.
        gap_percent / holdout_p_value: the values the decision rested on.
    """
    mode = gate_mode()
    if metrics is None:
        return {
            "blocked": mode == "block",
            "reasons": ["no_metrics"],
            "mode": mode,
            "gap_percent": None,
            "holdout_p_value": None,
        }

    reasons: list[str] = []

    gap = metrics.get("overfit_gap_percent")
    if metrics.get("overfit_boundary_reached"):
        reasons.append("validation_beats_holdout")
    elif gap is not None:
        try:
            boundary = float(
                metrics.get(
                    "overfit_boundary_percent", DEFAULT_GAP_BOUNDARY_PERCENT
                )
            )
            if float(gap) >= boundary:
                reasons.append("validation_beats_holdout")
        except (TypeError, ValueError):
            pass

    p_value = metrics.get("holdout_p_value")
    if p_value is not None:
        try:
            if float(p_value) >= DEFAULT_SIGNIFICANCE_ALPHA:
                reasons.append("holdout_not_significant_vs_random")
        except (TypeError, ValueError):
            pass

    warning_reasons = metrics.get("overfit_warning_reasons") or []
    for reason in warning_reasons:
        if reason in _BLOCK_REASONS and reason not in reasons:
            reasons.append(str(reason))

    if metrics.get("holdout_accuracy_percent") is None and not reasons:
        # No holdout was scored: the headline number was selected on the
        # draws it is scored on, so it cannot be promoted as verified.
        reasons.append("no_holdout")

    return {
        "blocked": bool(reasons) and mode == "block",
        "reasons": reasons,
        "mode": mode,
        "gap_percent": gap,
        "holdout_p_value": p_value,
    }
