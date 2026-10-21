"""Real-world accuracy report: served suggestions graded against real draws.

Compares what the suggestions actually hit with what random tickets would hit
(exact hypergeometric / enumerated baselines), and with the backtest. Backtest
numbers are always labelled "Simulated"; the headline is labelled "Real" only
once at least one served suggestion has been graded.
"""

from __future__ import annotations

from typing import Any

from prediction.config.loader import game_rules_from_config
from services import accuracy_stats, game_odds
from services.suggestion_grader import DrawLoader, grade_entries
from services.suggestion_ledger import SuggestionLedger, suggestion_ledger

SIGNIFICANCE_ALPHA = 0.05
SMALL_SAMPLE = 30


def _unique_tickets(graded: list[dict]) -> list[dict]:
    """One ticket per (target draw, numbers): re-serving the same pick is not new evidence."""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in graded:
        key = (item.get("target_draw_date"), item.get("target_draw_session"), tuple(sorted(item.get("numbers") or [])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def pooled_stats(graded: list[dict], game: str) -> dict[str, Any]:
    items = _unique_tickets([g for g in graded if g.get("status") == "graded"])
    n = len(items)
    if n == 0:
        return {"graded": 0, "verdict": "No graded suggestions yet."}
    pmfs = [g["hit_distribution_by_chance"] for g in items if g.get("hit_distribution_by_chance")]
    total_hits = int(sum(g["hits"] for g in items))
    expected_total = float(sum(accuracy_stats.expected_hits(p) for p in pmfs))
    p_total = accuracy_stats.pooled_total_p_value(pmfs, total_hits)
    wins = sum(1 for g in items if (g.get("tier") or {}).get("wins"))
    p_any = game_odds.any_prize_probability(game)
    binom_p = accuracy_stats.binomial_upper_tail(wins, n, p_any) if p_any else None
    mean_hits = total_hits / n
    expected_mean = expected_total / max(len(pmfs), 1)
    if p_total is not None and p_total < SIGNIFICANCE_ALPHA:
        verdict = f"Above chance (exact p = {p_total:.4f})."
    else:
        verdict = "Consistent with chance: these hits are what random tickets would get."
    if n < SMALL_SAMPLE:
        verdict += f" Only {n} graded suggestion(s), far too few to tell a real edge from luck."
    return {
        "graded": n,
        "total_hits": total_hits,
        "expected_total_hits_by_chance": round(expected_total, 4),
        "mean_hits": round(mean_hits, 4),
        "expected_mean_hits_by_chance": round(expected_mean, 4),
        "lift_vs_chance": round(mean_hits / expected_mean, 4) if expected_mean > 0 else None,
        "p_value_exact": round(p_total, 6) if p_total is not None else None,
        "p_value_method": "exact convolution of per-suggestion hit distributions (one-sided)",
        "prize_wins": wins,
        "any_prize_probability_per_play": round(p_any, 6) if p_any else None,
        "binomial_p_value_prize_wins": round(binom_p, 6) if binom_p is not None else None,
        "verdict": verdict,
    }


def _backtest_metrics(game: str) -> dict | None:
    try:
        from services.prediction_adapter import prediction_adapter

        summary = prediction_adapter.get_prediction_summary(game)
    except Exception:
        return None
    metrics = (summary or {}).get("metrics") or {}
    return metrics or None


def backtest_gap(game: str, real: dict, metrics: dict | None) -> dict[str, Any]:
    if not metrics:
        return {"label": "Simulated", "available": False,
                "note": "No backtest has been run for this game yet."}
    holdout = metrics.get("holdout_mean_partial_hits")
    sim_mean = holdout if holdout is not None else metrics.get("mean_partial_hits")
    baseline = (metrics.get("holdout_random_baseline") or metrics.get("random_baseline") or {})
    out = {
        "label": "Simulated",
        "available": sim_mean is not None,
        "source": "holdout" if holdout is not None else (metrics.get("accuracy_source") or "backtest"),
        "simulated_mean_hits": sim_mean,
        "simulated_random_baseline_mean_hits": baseline.get("mean_partial_hits"),
        "simulated_lift_vs_random": metrics.get("holdout_lift_vs_random") or metrics.get("lift_vs_random"),
        "simulated_p_value": metrics.get("holdout_p_value"),
        "simulated_evaluated_draws": metrics.get("holdout_evaluated_draws") or metrics.get("evaluated_draws"),
    }
    if real.get("graded"):
        out["real_mean_hits"] = real.get("mean_hits")
        if sim_mean is not None:
            out["gap_real_minus_simulated"] = round(float(real["mean_hits"]) - float(sim_mean), 4)
    return out


def build_accuracy_report(
    game: str,
    last: int = 3,
    *,
    ledger: SuggestionLedger | None = None,
    loader: DrawLoader | None = None,
    draws=None,
    backtest_metrics: dict | None = None,
    include_backtest: bool = True,
) -> dict[str, Any]:
    ledger = ledger or suggestion_ledger
    rules = game_rules_from_config(game)
    entries = sorted(ledger.read_suggestions(game), key=lambda e: float(e.get("served_at_epoch") or 0))
    reported = ledger.read_feedback(game)
    graded, info = grade_entries(game, entries, draws=draws, reported=reported, loader=loader)
    last = max(1, min(int(last or 3), 50))
    recent = list(reversed(graded[-last:]))
    real_recent = pooled_stats(recent, game)
    real_all = pooled_stats(graded, game)
    metrics = backtest_metrics if backtest_metrics is not None else (
        _backtest_metrics(game) if include_backtest else None
    )
    has_real = bool(real_all.get("graded"))
    report = {
        "status": "ok",
        "game": game,
        "last": last,
        "data_label": "Real" if has_real else "Simulated",
        "data_label_note": (
            "Graded against real draws." if has_real else
            "No served suggestion has been graded yet, so only simulated backtest numbers exist."
        ),
        "ledger_size": len(entries),
        "suggestions": recent,
        "pooled_last": real_recent,
        "pooled_all_time": real_all,
        "backtest": backtest_gap(game, real_all, metrics),
        "rules": {"primary_count": rules.primary_count, "primary_min": rules.primary_min,
                  "primary_max": rules.primary_max, "primary_unique": rules.primary_unique},
        "draws_loaded": info.get("draws_loaded"),
        "draws_error": info.get("draws_error"),
        "disclaimer": game_odds.PICKS_CANNOT_CHANGE_ODDS,
    }
    try:
        from prediction.chain.champion_store import champion_summary

        report["model_champion"] = champion_summary(game)
    except Exception:
        report["model_champion"] = None
    return report
