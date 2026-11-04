"""All-games accuracy overview: where is there enough graded evidence to act?

One call answers the question the per-game panel cannot: which games
have reached the 30-unique-graded threshold for a reweight proposal, and
which are still data-starved. Per game it reports the ledger verdict
(the unbiased archive), the reweight proposal status, a per-model
breakdown (ensemble vs chained_ensemble vs unknown — diagnostic only;
chained tickets never feed strategy credit), and a recent_k breakdown.

The recent_k breakdown is OBSERVATIONAL, not causal: it groups already-
graded tickets by the recent_k they were served with. It can suggest
which recent_k values deserve a proper offline sweep; it cannot prove
one is better, because recent_k correlates with time, weights, and
model version. Quick Draw is excluded: draws every 4 minutes cannot be
pinned by date (the same exclusion as the verified-draw fallback).
"""

from __future__ import annotations

from typing import Any

from config import GAME_CONFIGS
from services.accuracy_report import _unique_tickets, pooled_stats
from services.graded_reweight import MIN_GRADED, propose_reweight
from services.suggestion_grader import grade_entries
from services.suggestion_ledger import suggestion_ledger

EXCLUDED_GAMES = {"quickdraw"}


def _breakdown(items: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        value = item.get(key)
        label = str(value) if value is not None else "unknown"
        groups.setdefault(label, []).append(item)
    out: dict[str, dict] = {}
    for label, rows in sorted(groups.items()):
        expected = [r["expected_hits_by_chance"] for r in rows
                    if r.get("expected_hits_by_chance") is not None]
        mean_hits = sum(r["hits"] for r in rows) / len(rows)
        expected_mean = sum(expected) / len(expected) if expected else None
        out[label] = {
            "n": len(rows),
            "mean_hits": round(mean_hits, 4),
            "expected_mean_hits_by_chance": round(expected_mean, 4) if expected_mean is not None else None,
            "lift_vs_chance": round(mean_hits / expected_mean, 4)
                              if expected_mean else None,
        }
    return out


def _game_overview(game: str, *, entries=None, draws=None, weights=None,
                   experiment_total=None) -> dict[str, Any]:
    entries = suggestion_ledger.read_suggestions(game) if entries is None else entries
    entries = sorted(entries, key=lambda e: float(e.get("served_at_epoch") or 0))
    try:
        reported = suggestion_ledger.read_feedback(game)
    except Exception:
        reported = []
    graded, _info = grade_entries(game, entries, draws=draws, reported=reported)
    pooled = pooled_stats(graded, game)
    unique = _unique_tickets([g for g in graded if g.get("status") == "graded"])
    # Pooled stats already dedupe; reuse its graded count for the verdict.
    n_unique = pooled.get("graded", 0)
    if weights is None:
        try:
            from prediction.state.weight_store import WeightStore

            weights = WeightStore().load(game).weights
        except Exception:
            weights = {}
    try:
        proposal = propose_reweight(game, graded, weights or {})
    except Exception as exc:
        proposal = {"verdict": "error", "reasons": [str(exc)],
                    "n_graded_unique": n_unique, "candidate_weights": None}
    if experiment_total is None:
        try:
            from services.experiment_accuracy import read_prediction_experiments

            experiment_total = len(read_prediction_experiments(game))
        except Exception:
            experiment_total = None
    return {
        "game": game,
        "ledger_entries": len(entries),
        "graded_unique": n_unique,
        "min_graded_for_reweight": MIN_GRADED,
        "data_label": "Real" if n_unique else "Simulated",
        "pooled": pooled,
        "reweight_verdict": proposal.get("verdict"),
        "reweight_reasons": proposal.get("reasons") or [],
        "reweight_candidate": proposal.get("candidate_weights"),
        "by_model": _breakdown(unique, "model"),
        "by_recent_k": _breakdown(unique, "recent_k"),
        "experiment_total": experiment_total,
    }


def build_accuracy_overview(*, entries_by_game=None, draws_by_game=None,
                            weights_by_game=None) -> dict[str, Any]:
    """Grade every game (except Quick Draw) and summarize readiness."""
    entries_by_game = entries_by_game or {}
    draws_by_game = draws_by_game or {}
    weights_by_game = weights_by_game or {}
    games: dict[str, Any] = {}
    for game in sorted(GAME_CONFIGS.keys()):
        if game in EXCLUDED_GAMES:
            games[game] = {
                "game": game,
                "excluded": True,
                "reason": ("Draws every 4 minutes cannot be pinned by date; "
                           "same exclusion as the verified-draw fallback."),
            }
            continue
        try:
            games[game] = _game_overview(
                game,
                entries=entries_by_game.get(game),
                draws=draws_by_game.get(game),
                weights=weights_by_game.get(game),
            )
        except Exception as exc:
            games[game] = {"game": game, "status": "error", "message": str(exc)}
    ready = sorted(g for g, row in games.items()
                   if row.get("reweight_verdict") == "promote")
    total_graded = sum(row.get("graded_unique") or 0 for row in games.values())
    return {
        "status": "ok",
        "games": games,
        "ready_to_reweight": ready,
        "total_graded_unique": total_graded,
        "min_graded_for_reweight": MIN_GRADED,
        "note": ("Ledger verdicts are the unbiased archive. Experiment "
                 "totals are diagnostic counts only; experiment grading "
                 "lives at /api/experiment_accuracy."),
    }
