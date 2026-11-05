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
from config import GAME_CONFIGS

SIGNIFICANCE_ALPHA = 0.05
SMALL_SAMPLE = 30


def _unique_tickets(graded: list[dict]) -> list[dict]:
    """One ticket per (draw graded against, numbers): re-serving the same
    pick is not new evidence. Keyed on the ACTUAL draw, not the claimed
    target: experiment entries carry no target (their next_draw_date is
    the serve date), and immediate-next entries grade against a draw
    other than their target — in both cases the actual draw is the
    evidence unit. Falls back to the target fields for rows without an
    actual draw (should not happen for graded rows)."""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in graded:
        key = (item.get("actual_draw_date") or item.get("target_draw_date"),
               item.get("actual_draw_session") or item.get("target_draw_session"),
               tuple(sorted(item.get("numbers") or [])))
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
    # Bonus balls were graded per ticket (bonus_hit) but never pooled, so
    # bonus-game performance was invisible in the verdict. Each entry's
    # bonus pick is one independent Bernoulli, so the binomial tail IS
    # exact here (unlike primary picks within a ticket).
    bonus_summary = None
    try:
        rules = game_rules_from_config(game)
        bonus_universe = max(rules.bonus_max - rules.bonus_min + 1, 1)
        if rules.bonus_count > 0:
            bonus_items = [g for g in items if g.get("bonus_hit") is not None]
            if bonus_items:
                # bonus_hit compares the first bonus ball only, so the
                # chance baseline is 1 pick from the bonus drum.
                b_hits = sum(1 for g in bonus_items if g["bonus_hit"])
                b_p = 1.0 / bonus_universe
                b_expected = len(bonus_items) * b_p
                bonus_summary = {
                    "n": len(bonus_items),
                    "hits": b_hits,
                    "expected_hits": round(b_expected, 4),
                    "p_value": round(accuracy_stats.binomial_upper_tail(
                        b_hits, len(bonus_items), b_p), 6),
                    "p_value_method": "exact binomial (one independent bonus pick per entry)",
                }
    except Exception:
        bonus_summary = None
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
        "bonus": bonus_summary,
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


def _dedupe_key(entry: dict):
    """Identity of one served pick: serve minute + numbers.

    The ledger and the experiment store record the SAME serve event
    twice (both are written by _normalize_prediction_result, seconds
    apart), so the report must not show or count it twice. Minute
    granularity absorbs that skew; identical numbers served in the
    same minute are the same suggestion for grading purposes. Entries
    without a serve timestamp are never deduped.
    """
    epoch = entry.get("served_at_epoch")
    try:
        minute = int(float(epoch) // 60) if epoch is not None else None
    except (TypeError, ValueError):
        minute = None
    return (minute, tuple(entry.get("primary") or []),
            tuple(entry.get("bonus") or []))


def _merged_entries(ledger_entries, experiment_entries):
    """Ledger + experiment suggestions, one row per served pick.

    The ledger is kept whole (it carries the stored targets); an
    experiment row that duplicates a ledger row is dropped. The result
    is sorted by serve time, so the last-N slice is the most recent
    suggestions from either source.
    """
    seen = set()
    merged = []
    for entry in ledger_entries:
        key = _dedupe_key(entry)
        if key[0] is not None:
            seen.add(key)
        merged.append(entry)
    for entry in experiment_entries or []:
        key = _dedupe_key(entry)
        if key[0] is not None and key in seen:
            continue
        merged.append(entry)
    merged.sort(key=lambda e: float(e.get("served_at_epoch") or 0))
    return merged


def build_accuracy_report(
    game: str,
    last: int = 3,
    *,
    ledger: SuggestionLedger | None = None,
    loader: DrawLoader | None = None,
    draws=None,
    backtest_metrics: dict | None = None,
    include_backtest: bool = True,
    experiment_entries=None,
) -> dict[str, Any]:
    ledger = ledger or suggestion_ledger
    rules = game_rules_from_config(game)
    ledger_entries = sorted(ledger.read_suggestions(game), key=lambda e: float(e.get("served_at_epoch") or 0))
    reported = ledger.read_feedback(game)
    # Real-world accuracy grades the suggestion history, and the
    # history lives in two places: the ledger AND the experiments.
    # A game whose ledger is empty (or predates the ledger) still has
    # experiment suggestions, and grading only the ledger would report
    # "no suggestions served" while experiments sit ungraded. The
    # experiment bridge is imported here, not at module level, because
    # it imports pooled_stats from this module.
    if experiment_entries is None:
        try:
            from services.experiment_accuracy import read_prediction_experiments

            experiment_entries = read_prediction_experiments(game)
        except Exception:
            experiment_entries = []
    entries = _merged_entries(ledger_entries, experiment_entries)
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
        "ledger_size": len(ledger_entries),
        "experiments_size": len(experiment_entries),
        "suggestions": recent,
        "pooled_last": real_recent,
        "pooled_all_time": real_all,
        "backtest": backtest_gap(game, real_all, metrics),
        "rules": {"primary_count": rules.primary_count, "primary_min": rules.primary_min,
                  "primary_max": rules.primary_max, "primary_unique": rules.primary_unique},
        "draws_loaded": info.get("draws_loaded"),
        "draws_error": info.get("draws_error"),
        "verified_draws_fetched": info.get("verified_draws_fetched", 0),
        "verified_draws_error": info.get("verified_draws_error"),
        "disclaimer": game_odds.PICKS_CANNOT_CHANGE_ODDS,
    }
    try:
        from prediction.chain.champion_store import champion_summary

        report["model_champion"] = champion_summary(game)
    except Exception:
        report["model_champion"] = None
    return report


def build_all_games_accuracy(*, ledgers_by_game=None, draws_by_game=None,
                             experiments_by_game=None) -> dict[str, Any]:
    """Process every game through the merged real-world report, at once.

    One call answers "which games are Real?" instead of opening each
    game's panel: every game (except Quick Draw) is graded from its
    merged suggestion history — ledger + experiments — against its
    game record (ingested draws + verified official fallback, live
    path). A game is Real as soon as one of its suggestions grades;
    a game with no history stays Simulated, and the reason says so
    plainly: no suggestions to grade is not a grading failure. A game
    whose suggestions exist but grade nothing reports that instead
    (draws missing for their targets). Per-game errors never fail the
    whole run.
    """
    ledgers_by_game = ledgers_by_game or {}
    draws_by_game = draws_by_game or {}
    experiments_by_game = experiments_by_game or {}
    games: dict[str, Any] = {}
    for game in sorted(GAME_CONFIGS.keys()):
        if game == "quickdraw":
            games[game] = {
                "game": game,
                "data_label": "Excluded",
                "excluded": True,
                "reason": ("Draws every 4 minutes cannot be pinned by date; "
                           "same exclusion as the verified-draw fallback."),
            }
            continue
        try:
            report = build_accuracy_report(
                game, last=3,
                ledger=ledgers_by_game.get(game),
                draws=draws_by_game.get(game),
                include_backtest=False,
                experiment_entries=experiments_by_game.get(game))
            pooled = report["pooled_all_time"]
            total_history = report["ledger_size"] + report["experiments_size"]
            if report["data_label"] == "Real":
                reason = None
            elif total_history == 0:
                reason = ("No suggestions served for this game yet: "
                          "no ledger entries and no experiments to grade.")
            else:
                reason = ("Suggestions exist but none graded yet: their "
                          "target draws are not in the game record and "
                          "the verified fallback found none.")
            games[game] = {
                "game": game,
                "data_label": report["data_label"],
                "ledger_size": report["ledger_size"],
                "experiments_size": report["experiments_size"],
                "graded": pooled.get("graded", 0),
                "mean_hits": pooled.get("mean_hits"),
                "expected_mean_hits_by_chance": pooled.get("expected_mean_hits_by_chance"),
                "lift_vs_chance": pooled.get("lift_vs_chance"),
                "p_value_exact": pooled.get("p_value_exact"),
                "verdict": pooled.get("verdict"),
                "reason": reason,
            }
        except Exception as exc:
            games[game] = {"game": game, "status": "error", "message": str(exc)}
    real = sorted(g for g, row in games.items() if row.get("data_label") == "Real")
    simulated = sorted(g for g, row in games.items() if row.get("data_label") == "Simulated")
    total_graded = sum(row.get("graded") or 0 for row in games.values())
    return {
        "status": "ok",
        "games": games,
        "real_games": real,
        "simulated_games": simulated,
        "total_graded": total_graded,
        "note": ("Every game is graded from its merged suggestion history "
                 "(ledger + experiments) against its game record. A game "
                 "with no suggestions cannot be Real — there is nothing "
                 "to grade — so Simulated there means 'serve a suggestion "
                 "first', not 'grading failed'."),
    }
