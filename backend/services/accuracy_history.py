"""Track accuracy over time, by suggestion date.

Accuracy is tracked from the labeled suggestion records themselves —
the append-only ledger and the experiment archive — so it accumulates
as new suggestions are served and graded, and it never goes stale when
grading logic improves (there is no separate snapshot store to drift).

Each row is bucketed by its ``suggestion_date`` (the date the pick was
served), not by draw date: the question is "how accurate were the
suggestions we served in this period?" Ledger and experiments are
reported as separate series, because the same served pick exists in
both and combining them would double-count it. The ledger series is
the unbiased one; the experiment series carries the same rows with
their strategy weights attached.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.accuracy_report import _unique_tickets
from services.experiment_accuracy import read_prediction_experiments
from services.suggestion_grader import grade_entries
from services.suggestion_ledger import suggestion_ledger


def _period_key(suggestion_date: str | None, period: str) -> str:
    if not suggestion_date:
        return "unknown"
    try:
        day = date.fromisoformat(str(suggestion_date)[:10])
    except ValueError:
        return "unknown"
    if period == "weekly":
        iso = day.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return day.strftime("%Y-%m")


def _series(graded: list[dict], period: str) -> list[dict]:
    unique = _unique_tickets([g for g in graded if g.get("status") == "graded"])
    buckets: dict[str, list[dict]] = {}
    for row in unique:
        buckets.setdefault(_period_key(row.get("suggestion_date"), period), []).append(row)
    ordered = sorted(buckets.keys(), key=lambda k: (k == "unknown", k))
    out: list[dict] = []
    cum_n = 0
    cum_hits = 0
    cum_expected = 0.0
    for key in ordered:
        rows = buckets[key]
        expected = [r["expected_hits_by_chance"] for r in rows
                    if r.get("expected_hits_by_chance") is not None]
        hits = sum(r["hits"] for r in rows)
        exp_total = sum(expected)
        cum_n += len(rows)
        cum_hits += hits
        cum_expected += exp_total
        mean_hits = hits / len(rows)
        exp_mean = exp_total / len(expected) if expected else None
        cum_mean = cum_hits / cum_n if cum_n else 0.0
        cum_exp_mean = cum_expected / cum_n if cum_n else None
        bonus_rows = [r for r in rows if r.get("bonus_hit") is not None]
        out.append({
            "period": key,
            "n": len(rows),
            "mean_hits": round(mean_hits, 4),
            "expected_mean_hits_by_chance": round(exp_mean, 4) if exp_mean is not None else None,
            "lift_vs_chance": round(mean_hits / exp_mean, 4) if exp_mean else None,
            "bonus_hits": sum(1 for r in bonus_rows if r["bonus_hit"]),
            "bonus_n": len(bonus_rows),
            "cumulative_n": cum_n,
            "cumulative_mean_hits": round(cum_mean, 4),
            "cumulative_expected_mean": round(cum_exp_mean, 4) if cum_exp_mean is not None else None,
            "cumulative_lift_vs_chance": round(cum_mean / cum_exp_mean, 4) if cum_exp_mean else None,
        })
    return out


def build_accuracy_history(game: str, period: str = "monthly", *,
                           entries=None, experiment_entries=None,
                           draws=None) -> dict[str, Any]:
    """Accuracy over time for one game, from ledger + experiment rows."""
    if period not in ("monthly", "weekly"):
        raise ValueError("period must be 'monthly' or 'weekly'")
    if entries is None:
        entries = sorted(suggestion_ledger.read_suggestions(game),
                         key=lambda e: float(e.get("served_at_epoch") or 0))
    try:
        reported = suggestion_ledger.read_feedback(game)
    except Exception:
        reported = []
    graded, _info = grade_entries(game, entries, draws=draws, reported=reported)
    if experiment_entries is None:
        try:
            experiment_entries = read_prediction_experiments(game)
        except Exception:
            experiment_entries = []
    exp_graded, _ = grade_entries(game, experiment_entries, draws=draws,
                                  reported=reported)
    return {
        "status": "ok",
        "game": game,
        "period": period,
        "ledger": _series(graded, period),
        "experiments": _series(exp_graded, period),
        "note": ("Bucketed by suggestion_date. Ledger is the unbiased "
                 "series; experiments are the same picks with strategy "
                 "weights attached — do not add the two series together."),
    }
