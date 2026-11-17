"""Overfit gate for PocketProNYL backtests.

Stdlib only, so it can be unit-tested without the full backend stack.

The modular evaluator (prediction/metrics/evaluator.py) already reports a
three-way split: validation accuracy, a final untouched holdout scored once,
the validation-minus-holdout gap, and a holdout p-value against random
tickets. Until now those were warnings in the report: a champion with a big
gap, or a holdout that does not beat random, could still be promoted.

This gate turns those warnings into a promotion decision:

- blocked when the validation-vs-holdout gap is real: it reaches the
  evaluator's own boundary (2.0 percentage points by default) AND clears
  the holdout's own sampling noise (see ``overfit_gap_significant``),
- blocked when there is no holdout at all, because then the reported
  accuracy was selected on the draws it is scored on and cannot be verified.

A holdout that merely does not beat random at the usual 0.05 level does
NOT block promotion: on lottery draws a 5-60-draw holdout almost always
has p >= 0.05, and that is the normal outcome, not evidence the model
memorized the training rows. It stays a warning label
(``holdout_not_significant_vs_random``) and is reported in words, never a
promotion block — blocking on it meant nothing ever promoted.

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

# Reasons that block promotion. "holdout_not_significant_vs_random" is
# deliberately NOT here: a holdout that does not beat random is the normal
# lottery outcome and stays a warning label, never a promotion block.
_BLOCK_REASONS = (
    "validation_beats_holdout",
    "no_holdout",
)


def stored_candidate_fails_gate(mapping: dict | None) -> bool:
    """Check a stored optimum-config candidate for explicit overfit failure.

    Unlike evaluate_overfit_gate(), this does NOT block on missing holdout
    data: older stored candidates predate the holdout report, and blocking
    all of them would discard every legacy optimum. It only blocks when
    the stored record explicitly says the run failed: a real gap (at/over
    the boundary AND clearing the holdout noise margin, when the record
    carries the noise-aware flag; the raw boundary for older records) or a
    blocking warning reason. A non-significant holdout p-value alone never
    fails a stored candidate — that is the normal lottery outcome.
    Unknown candidates pass through.
    """
    if not isinstance(mapping, dict):
        return False
    significant = mapping.get("overfit_gap_significant")
    if significant is True:
        return True
    if significant is None:
        # Records from before the noise-aware gap: fall back to the raw
        # boundary, exactly as the old gate did.
        if mapping.get("overfit_boundary_reached"):
            return True
        gap = mapping.get("overfit_gap_percent")
        if gap is not None:
            try:
                boundary = float(
                    mapping.get(
                        "overfit_boundary_percent", DEFAULT_GAP_BOUNDARY_PERCENT
                    )
                )
                if float(gap) >= boundary:
                    return True
            except (TypeError, ValueError):
                pass
    for reason in mapping.get("overfit_warning_reasons") or []:
        if reason in _BLOCK_REASONS:
            return True
    return False


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

    # A real gap blocks promotion: the noise-aware flag when the record
    # carries it, the raw boundary for older records.
    gap = metrics.get("overfit_gap_percent")
    significant = metrics.get("overfit_gap_significant")
    if significant is True:
        reasons.append("validation_beats_holdout")
    elif significant is None:
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

    # The holdout p-value is deliberately NOT a blocking reason (see the
    # module docstring): it stays a warning label, reported in words.
    p_value = metrics.get("holdout_p_value")
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


def overfit_reason_labels(metrics: dict | None) -> list[str]:
    """Human-readable phrases for each overfit warning reason, in words.

    The three reasons mean different things and must not be collapsed into
    one blanket sentence: a validation/holdout gap is evidence the kept
    weights fit the validation window harder than unseen draws; a
    non-significant holdout p-value is the normal lottery outcome (a 5-60
    draw holdout almost never beats random at p < 0.05); a missing holdout
    means the headline number was selected on the draws it is scored on.
    """
    if not isinstance(metrics, dict):
        return []
    labels: list[str] = []
    for reason in metrics.get("overfit_warning_reasons") or []:
        if reason == "validation_beats_holdout":
            gap = metrics.get("overfit_gap_percent")
            boundary = metrics.get(
                "overfit_boundary_percent", DEFAULT_GAP_BOUNDARY_PERCENT
            )
            margin = metrics.get("holdout_noise_margin_percent")
            text = "validation (kept weights) beats the holdout by "
            try:
                text += f"{float(gap):.1f} pp" if gap is not None else "a wide margin"
            except (TypeError, ValueError):
                text += "a wide margin"
            try:
                text += f" (boundary {float(boundary):.1f} pp"
            except (TypeError, ValueError):
                text += " (boundary 2.0 pp"
            if margin is not None:
                try:
                    text += f"; holdout noise margin {float(margin):.1f} pp"
                except (TypeError, ValueError):
                    pass
            text += ")"
            labels.append(text)
        elif reason == "holdout_not_significant_vs_random":
            p_value = metrics.get("holdout_p_value")
            text = "holdout did not beat random picks at p < 0.05"
            if p_value is not None:
                try:
                    text += f" (p = {float(p_value):.3f})"
                except (TypeError, ValueError):
                    pass
            labels.append(text)
        elif reason == "no_holdout":
            labels.append(
                "no untouched holdout was available, so the score was "
                "selected on the draws it is scored on"
            )
    return labels


def overfit_warning_sentence(metrics: dict | None) -> str | None:
    """One sentence naming the actual overfit-warning reason(s) in words.

    Returns None when there is no warning. A gap or a missing holdout is
    worded as an overfit warning; a holdout that merely did not beat random
    is worded as its own label ("Holdout check"), because on lottery draws
    a 5-60-draw holdout almost always has p >= 0.05 and that is not
    evidence the model memorized the training rows.
    """
    labels = overfit_reason_labels(metrics)
    if not labels:
        return None
    reasons = (metrics or {}).get("overfit_warning_reasons") or []
    if reasons == ["holdout_not_significant_vs_random"]:
        return (
            "Holdout check: " + labels[0]
            + " — the usual lottery outcome for a 5-60-draw holdout, "
            "not evidence of memorization."
        )
    return "Overfit warning: " + "; ".join(labels) + "."
