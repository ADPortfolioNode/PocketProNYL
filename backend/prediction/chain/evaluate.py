"""Champion/challenger evaluation of the chained ensemble (PR #13 machinery).

Split (same helpers as ``walk_forward_backtest``): train prefix / validation
window / final untouched holdout of the most recent draws.

1. Every chain variant and the incumbent (the committed ensemble weights,
   exactly what ``engine.predict(..., weights_override=committed)`` returns)
   predict each validation draw from the draws before it.
2. The best variant on validation is tested against the incumbent with the
   paired sign-flip permutation test at a Bonferroni threshold
   ``alpha / number_of_variants`` (``champion_challenger_decision``).
3. Only that one variant is then scored once on the holdout. It becomes the
   champion only if it also beats the incumbent there (paired permutation,
   one-sided, p <= alpha). Otherwise the incumbent stays.

The report carries both sides' holdout results, Monte Carlo p-values against
random tickets, chi-square uniformity, BH-FDR per-number tests, and the
meta-learner's out-of-fold log loss against the chance base rate.
"""

from __future__ import annotations

import time
from typing import Iterable

import numpy as np

from prediction.chain import champion_store
from prediction.chain.chained import DEFAULT_VARIANTS, ChainedPredictor, ChainVariant
from prediction.chain.posterior import BetaPosterior, update, usable_carried
from prediction.chain.stacking import forward_oof_log_loss
from prediction.chain.stats import chi_square_uniformity, number_frequency_tests
from prediction.config.loader import game_rules_from_config
from prediction.core.ensemble import build_ticket
from prediction.core.types import Draw
from prediction.metrics.evaluator import (
    DEFAULT_SIGNIFICANCE_ALPHA,
    champion_challenger_decision,
    paired_permutation_p_value,
    random_baseline_metrics,
    random_hits_p_value,
    resolve_holdout_size,
    resolve_test_start,
)


def _hits(ticket: list[int], draw: Draw) -> int:
    return len(set(ticket) & set(draw.primary))


def _side(hits: list[int], draws: list[Draw], rules) -> dict:
    if not hits:
        return {"draws": 0}
    mean = float(np.mean(hits))
    baseline = random_baseline_metrics(draws, rules)
    base = baseline.get("mean_partial_hits") or 0.001
    return {
        "draws": len(hits),
        "mean_hits": round(mean, 4),
        "accuracy_percent": round(mean / max(rules.primary_count, 1) * 100, 2),
        "random_baseline_mean_hits": baseline.get("mean_partial_hits"),
        "lift_vs_random": round(mean / base, 4),
        "p_value_vs_random": random_hits_p_value(int(sum(hits)), draws, rules),
    }


def evaluate_chain(
    engine,
    game: str,
    history: list[Draw],
    *,
    variants: Iterable[ChainVariant] | None = None,
    alpha: float = DEFAULT_SIGNIFICANCE_ALPHA,
    commit: bool = True,
    holdout_draws: int | None = None,
    max_test_draws: int | None = None,
    seed: int = 13,
    min_history: int | None = None,
) -> dict:
    started = time.time()
    config = engine._load_config(game)
    rules = game_rules_from_config(game)
    variants = list(variants or DEFAULT_VARIANTS)
    metrics_cfg = config.metrics
    min_draws = int(metrics_cfg.min_backtest_draws)
    n = len(history)
    holdout_size = resolve_holdout_size(
        n, min_draws=min_draws,
        holdout_draws=holdout_draws if holdout_draws is not None else getattr(metrics_cfg, "holdout_draws", None),
        holdout_fraction=float(getattr(metrics_cfg, "holdout_fraction", 0.15)),
        max_holdout_draws=int(getattr(metrics_cfg, "max_holdout_draws", 60)),
    )
    holdout_start = n - holdout_size
    test_start = resolve_test_start(
        holdout_start, min_draws=min_draws,
        max_test_draws=max_test_draws if max_test_draws is not None else getattr(metrics_cfg, "max_test_draws", None),
    )
    if holdout_size == 0 or test_start >= holdout_start:
        return {"status": "insufficient_data", "game": game, "draws_available": n,
                "note": "Not enough draws for a train / validation / untouched holdout split."}

    stored = champion_store.load(game)
    carried = BetaPosterior.from_dict(stored.get("posterior"))
    base_posterior, posterior_status = usable_carried(carried, history, test_start, game, rules)

    predictor = ChainedPredictor(engine, game, history, base_posterior, seed=seed, min_history=min_history)
    builder = predictor.builder
    champion_weights = dict(engine._resolve_weights(game, config))

    def incumbent_ticket(index: int) -> list[int]:
        # Same ticket as engine.predict(game, history[:index], use_nn=False, weights_override=...).
        outputs = builder.outputs_at(index)
        return build_ticket(game=game, outputs=outputs, weights=champion_weights, rules=rules).primary

    validation_idx = list(range(test_start, holdout_start))
    holdout_idx = list(range(holdout_start, n))
    validation_draws = [history[i] for i in validation_idx]
    holdout_draws_list = [history[i] for i in holdout_idx]

    incumbent_val = [_hits(incumbent_ticket(i), history[i]) for i in validation_idx]
    variant_rows = []
    for variant in variants:
        hits = [_hits(predictor.ticket_at(i, variant)["primary"], history[i]) for i in validation_idx]
        variant_rows.append({"variant": variant, "hits": hits, "mean": float(np.mean(hits))})
    best = max(variant_rows, key=lambda row: (row["mean"], -row["variant"].votes))
    decision = champion_challenger_decision(
        best["hits"], incumbent_val, comparisons=len(variants), alpha=alpha, primary_count=rules.primary_count,
    )

    # The holdout is scored once, for the one selected variant and the incumbent.
    best_variant: ChainVariant = best["variant"]
    chain_hold = [_hits(predictor.ticket_at(i, best_variant)["primary"], history[i]) for i in holdout_idx]
    inc_hold = [_hits(incumbent_ticket(i), history[i]) for i in holdout_idx]
    hold_diff = float(np.mean(chain_hold) - np.mean(inc_hold))
    hold_p = paired_permutation_p_value(chain_hold, inc_hold)
    holdout_beats = bool(hold_diff > 0 and hold_p <= alpha)
    promoted = bool(decision["accepted"] and holdout_beats)
    if promoted:
        reason = "Chained ensemble beat the incumbent on validation (Bonferroni) and on the untouched holdout."
    elif not decision["accepted"]:
        reason = (f"Not promoted: validation gain not significant (p={decision['p_value']} > "
                  f"alpha/{decision['comparisons']}={decision['alpha_adjusted']}). Incumbent stays.")
    else:
        reason = (f"Not promoted: passed validation but not the untouched holdout (diff={hold_diff:+.4f}, "
                  f"p={hold_p}). Incumbent stays.")

    oof_positions = list(range(max(builder.min_history, test_start - 120), test_start))
    report = {
        "status": "ok",
        "game": game,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 2),
        "split": {"draws": n, "train_draws": test_start, "validation_draws": len(validation_idx),
                  "holdout_draws": holdout_size, "validation_start": test_start, "holdout_start": holdout_start},
        "variants_tested": [
            {**row["variant"].to_dict(), "validation_mean_hits": round(row["mean"], 4)} for row in variant_rows
        ],
        "selected_variant": best_variant.to_dict(),
        "validation": {
            "decision": decision,
            "incumbent": _side(incumbent_val, validation_draws, rules),
            "chained": _side(best["hits"], validation_draws, rules),
        },
        "holdout": {
            "incumbent": _side(inc_hold, holdout_draws_list, rules),
            "chained": _side(chain_hold, holdout_draws_list, rules),
            "mean_hit_diff_chained_minus_incumbent": round(hold_diff, 4),
            "paired_permutation_p_value": hold_p,
            "alpha": alpha,
            "chained_beats_incumbent": holdout_beats,
        },
        "promoted": promoted,
        "champion": champion_store.CHAINED if promoted else champion_store.INCUMBENT,
        "decision_reason": reason,
        "stats": {
            "chi_square_uniformity": chi_square_uniformity(history, rules),
            "per_number_frequency_bh_fdr": number_frequency_tests(history, rules),
            "meta_learner_oof": forward_oof_log_loss(builder, oof_positions),
            "note": "Uniformity and frequency tests are descriptive (full history); they are not used for selection.",
        },
        "posterior": {"status": posterior_status, "carried_runs": (carried.runs if carried else 0)},
        "committed": bool(commit),
    }

    if commit:
        # Carry the posterior as of the end of the train prefix (precedes this run's scoring windows).
        new_posterior = update(base_posterior, history[:test_start], rules)
        new_posterior.runs = (carried.runs if carried and posterior_status == "carried" else 0) + 1
        new_posterior.history = (new_posterior.history + [{
            "evaluated_at": report["evaluated_at"], "as_of": new_posterior.as_of,
            "draws_counted": new_posterior.draws_counted}])[-20:]
        champion_store.save(game, {
            "champion": report["champion"],
            "champion_variant": best_variant.to_dict() if promoted else None,
            "last_evaluated_at": report["evaluated_at"],
            "last_evaluation": {
                k: report[k] for k in ("promoted", "decision_reason", "holdout", "split", "selected_variant")
            },
            "posterior": new_posterior.to_dict(),
        })
    return report


def main():  # pragma: no cover - CLI convenience
    import argparse
    import json

    from prediction.core.draw_loader import from_chroma
    from prediction.engine import LotteryPredictionEngine

    parser = argparse.ArgumentParser(description="Evaluate the chained ensemble against the incumbent")
    parser.add_argument("--game", required=True)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    engine = LotteryPredictionEngine()
    history = from_chroma(args.game, limit=args.limit)
    print(json.dumps(evaluate_chain(engine, args.game, history, commit=not args.dry_run), indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()
