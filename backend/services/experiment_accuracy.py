"""Bridge past experiment suggestions to ground-truth draws.

Yes, the app has Experiments: every served prediction saves one (type
"prediction") with the full result — numbers, strategy weights and
contributions, recent_k, and the serve timestamp. But two facts shape
what they can prove:

- The live file keeps only 3 records per game (prune_experiments), and
  it keeps the TOP-accuracy ones. A sample kept for winning is biased
  upward: experiment accuracy alone overstates real accuracy.
- The ledger is the unbiased archive: append-only, every served
  suggestion, nothing evicted.

So this bridge grades BOTH, but labels them honestly: experiments give
rich recent diagnostics (with weights, so a graded experiment can feed
the reweight loop), while the ledger gives the unbiased verdict. From
now on, evicted experiments are also archived to
experiments_history.jsonl (see experiments/store.py) instead of being
deleted, so the experiment history grows instead of churning.

Grading reuses the ledger grader: an experiment becomes a ledger-like
entry with target_basis "experiment", which grades against the immediate
draw after it was served (experiments carry no real target date — their
next_draw_date is the serve date). Ground truth is the game's own
ingested draws, plus the verified official fallback for missing dates
(live path only, past dates only, read-time only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.accuracy_report import pooled_stats
from services.suggestion_grader import grade_entries
from services.suggestion_ledger import _numbers_from_result, model_version, suggestion_ledger
from utils.timestamps import normalize_experiment_record


def _served_iso(exp: dict) -> tuple[str | None, float | None]:
    iso = exp.get("timestamp_iso")
    if iso:
        try:
            epoch = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
            return str(iso), epoch
        except ValueError:
            pass
    seconds = exp.get("timestamp_seconds") or exp.get("timestamp")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None, None
    if seconds >= 1_000_000_000_000:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(), seconds
    except (ValueError, OverflowError, OSError):
        return None, None


def experiment_to_entry(exp: dict, game: str) -> dict | None:
    """One prediction experiment -> a gradable ledger-like entry, or None."""
    if not isinstance(exp, dict):
        return None
    if str(exp.get("game") or "").strip().lower() != game:
        return None
    if exp.get("type") != "prediction":
        return None
    status = exp.get("status")
    if status is not None and status != "COMPLETED":
        return None
    prediction = exp.get("prediction") or {}
    if not isinstance(prediction, dict):
        return None
    try:
        primary, bonus = _numbers_from_result({**prediction, "game": game})
    except Exception:
        return None
    if not primary:
        return None
    served_at, served_epoch = _served_iso(exp)
    if served_at is None:
        return None
    return {
        "id": exp.get("experiment_id"),
        "game": game,
        "primary": primary,
        "bonus": bonus,
        "served_at": served_at,
        "served_at_epoch": served_epoch,
        # Experiments carry no target claim: next_draw_date is the serve
        # date. The grader's experiment branch ignores it and resolves
        # the immediate next draw after served_at.
        "target_draw_date": exp.get("next_draw_date"),
        "target_draw_session": None,
        "target_draw_time": None,
        "target_basis": "experiment",
        "model": prediction.get("model_strategy") or prediction.get("strategy_used")
                 or exp.get("model_strategy") or "unknown",
        "engine": prediction.get("engine") or "modular",
        "model_version": model_version(prediction),
        "source": "experiment",
        "recent_k": exp.get("recent_k"),
        "strategy_weights": prediction.get("strategy_weights") or {},
        "strategy_contributions": prediction.get("strategy_contributions") or {},
    }


def read_prediction_experiments(game: str, store=None) -> list[dict]:
    """All prediction experiments for a game: live file + archive, deduped."""
    from experiments.store import ExperimentStore
    from utils.paths import experiments_file

    store = store or ExperimentStore(experiments_file())
    seen: set = set()
    ordered: list[dict] = []
    for item in list(store.list_experiments()) + list(store.list_history()):
        normalized = normalize_experiment_record(item)
        exp_id = normalized.get("experiment_id")
        if exp_id and exp_id in seen:
            continue
        if exp_id:
            seen.add(exp_id)
        ordered.append(normalized)
    ordered.sort(key=lambda e: float(e.get("timestamp_seconds")
                                     or e.get("timestamp") or 0))
    entries: list[dict] = []
    for exp in ordered:
        entry = experiment_to_entry(exp, game)
        if entry is not None:
            entries.append(entry)
    return entries


def build_experiment_accuracy(game: str, last: int = 3, *, store=None,
                              draws=None, loader=None) -> dict[str, Any]:
    """Grade a game's experiment suggestions against ground-truth draws."""
    entries = read_prediction_experiments(game, store=store)
    graded, info = grade_entries(game, entries, draws=draws, loader=loader)
    last = max(1, min(int(last or 3), 50))
    recent = list(reversed(graded[-last:]))
    real_recent = pooled_stats(recent, game)
    real_all = pooled_stats(graded, game)
    try:
        ledger_size = len(suggestion_ledger.read_suggestions(game))
    except Exception:
        ledger_size = None
    return {
        "status": "ok",
        "game": game,
        "last": last,
        "data_label": "Real" if real_all.get("graded") else "Simulated",
        "data_label_note": (
            "Graded against real draws." if real_all.get("graded") else
            "No experiment suggestion has been graded yet."
        ),
        "experiments_total": len(entries),
        "experiments_graded": real_all.get("graded"),
        "ledger_entries": ledger_size,
        "bias_note": (
            "The live experiments file keeps only 3 records per game, "
            "chosen by top accuracy, so experiment accuracy alone runs "
            "optimistic. The ledger (append-only, everything kept) is "
            "the unbiased verdict; use both together."
        ),
        "suggestions": recent,
        "pooled_last": real_recent,
        "pooled_all_time": real_all,
        "draws_loaded": info.get("draws_loaded"),
        "draws_error": info.get("draws_error"),
        "verified_draws_fetched": info.get("verified_draws_fetched", 0),
        "verified_draws_error": info.get("verified_draws_error"),
    }
