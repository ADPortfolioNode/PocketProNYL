"""Walk-forward retro-test: grade the engine against draw history it never saw.

The live suggestion ledger only fills as fast as real suggestions are
served and real draws land -- weeks to reach the 30 unique graded tickets
the reweighter needs. The ingested draw history is already here, so this
harness replays it: for a past draw at position ``i``, the engine predicts
from ``draws[:i]`` only (a truncated slice, so peeking is impossible by
construction), and the pick is graded against ``draws[i]`` with the same
scoring the live grader uses (same hit counting, same exact chance
distribution, same prize tiers).

Everything produced here is SIMULATED and stays labelled that way:

- rows land in their own store (``$DATA_DIR/retro/<game>/simulated.jsonl``),
  never in the live suggestion ledger;
- every row carries ``simulated: True`` and ``source: "retro_backtest"``;
- the report reuses the live per-strategy credit math
  (``graded_reweight.strategy_credit``) so simulated and live numbers are
  comparable, and any reweight proposal is advisory only -- this module
  never applies weights. Live promotion stays owned by the graded live
  ledger, exactly as ``graded_reweight`` documents.

The most recent stretch of history (the tail reserve, 10% of the draws by
default) is excluded from trials unless ``include_tail=True``: it is the
untouched final check, so tuning against the rest of history can be
verified against draws the tuning never saw.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw
from services import accuracy_stats, game_odds
from services.graded_reweight import (
    _exact_upper_tail,
    dedupe_graded,
    propose_reweight,
    strategy_credit,
)
from utils.paths import data_dir

RETRO_VERSION = 1
_GAME_RE = re.compile(r"^[a-z0-9]+$")
_LOCK = threading.Lock()
logger = logging.getLogger(__name__)

# Same bonus-pool rule as the live grader (services/suggestion_grader.py).
_BONUS_POOL_GAMES = {"powerball", "megamillions", "millionaireforlife", "cash4life"}

DEFAULT_MAX_TRIALS = 300
DEFAULT_WARMUP = 50
DEFAULT_HISTORY_WINDOW = 500


# --------------------------------------------------------------------------
# Simulated store: one append-only JSONL per game, apart from the live ledger.
# --------------------------------------------------------------------------

def retro_root() -> str:
    return os.environ.get("RETRO_DIR") or os.path.join(data_dir(), "retro")


def simulated_path(game: str) -> str:
    key = str(game or "").strip().lower()
    if not _GAME_RE.match(key):
        raise ValueError(f"Invalid game key for retro store: {game!r}")
    return os.path.join(retro_root(), key, "simulated.jsonl")


def _append_rows(game: str, rows: list[dict]) -> None:
    if not rows:
        return
    path = simulated_path(game)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def read_simulated(game: str) -> list[dict]:
    key = str(game or "").strip().lower()
    path = simulated_path(key)
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and str(record.get("game") or "").lower() == key:
                rows.append(record)
    return rows


# --------------------------------------------------------------------------
# One trial: predict from the past only, grade against the next real draw.
# --------------------------------------------------------------------------

def _session(draw: Draw) -> str:
    return str((draw.metadata or {}).get("draw_session") or "").strip().lower()


def _score_trial(game: str, rules, ticket_numbers: list[int], ticket_bonus: list[int],
                 target: Draw, *, model: str, weights: dict, contributions: dict,
                 history_draws_used: int, run_id: str) -> dict:
    """Grade one retro pick with the live grader's scoring rules."""
    hits_set = sorted(set(ticket_numbers) & set(target.primary))
    hits = len(hits_set)
    bonus_hit = None
    if game in _BONUS_POOL_GAMES and ticket_bonus and target.bonus:
        bonus_hit = int(ticket_bonus[0]) == int(target.bonus[0])
    try:
        pmf = accuracy_stats.hit_distribution(ticket_numbers, rules)
    except ValueError:
        pmf = []
    tier = game_odds.tier_for(
        game, ticket_numbers, list(target.primary), bonus_hit=bonus_hit,
        nylotto_bonus_ball=(int(target.bonus[0]) if game == "nylotto" and target.bonus else None),
    )
    return {
        "game": game,
        "retro_version": RETRO_VERSION,
        "run_id": run_id,
        "simulated": True,
        "source": "retro_backtest",
        "status": "graded",
        "numbers": [int(n) for n in ticket_numbers],
        "bonus_numbers": [int(n) for n in ticket_bonus or []],
        "actual_numbers": [int(n) for n in target.primary],
        "actual_bonus_numbers": [int(n) for n in target.bonus or []],
        "actual_draw_date": target.draw_date.isoformat() if target.draw_date else None,
        "actual_draw_session": _session(target) or None,
        "target_draw_id": target.draw_id,
        "matched_numbers": hits_set,
        "hits": hits,
        "bonus_hit": bonus_hit,
        "expected_hits_by_chance": round(accuracy_stats.expected_hits(pmf), 4) if pmf else None,
        "p_value_at_least_these_hits": round(accuracy_stats.upper_tail(pmf, hits), 6) if pmf else None,
        "tier": tier,
        "model": model,
        "strategy_weights": weights or {},
        "strategy_contributions": contributions or {},
        "history_draws_used": history_draws_used,
    }


def _default_adapter():
    from services.prediction_adapter import PredictionAdapter

    return PredictionAdapter()


def run_retro_backtest(
    game: str,
    *,
    draws: list[Draw] | None = None,
    adapter=None,
    engine=None,
    max_trials: int = DEFAULT_MAX_TRIALS,
    stride: int = 1,
    warmup: int = DEFAULT_WARMUP,
    history_window: int = DEFAULT_HISTORY_WINDOW,
    tail_reserve: int | None = None,
    include_tail: bool = False,
    persist: bool = True,
    run_id: str | None = None,
) -> dict:
    """Replay history walk-forward and grade every pick against its next draw.

    ``draws`` must be chronological, oldest first (``from_chroma`` order).
    Trials target indices ``[warmup, len(draws) - reserve)`` stepping by
    ``stride``; when more indices qualify than ``max_trials``, the most
    recent ones are kept (they best reflect the current engine). The engine
    only ever receives ``draws[max(0, i - history_window):i]`` -- the target
    draw and everything after it are physically absent from its input.

    Weights are whatever the engine resolves from the weight store at run
    time and are never updated during the run: one run measures one
    configuration, so credit belongs to that configuration alone.
    """
    key = str(game or "").strip().lower()
    rules = game_rules_from_config(key)
    if draws is None:
        from prediction.core.draw_loader import from_chroma

        draws = from_chroma(key, limit=200_000)
    draws = list(draws or [])
    total = len(draws)

    max_trials = max(1, min(int(max_trials), 5000))
    stride = max(1, min(int(stride), 1000))
    warmup = max(2, min(int(warmup), 100_000))
    history_window = max(10, min(int(history_window), 100_000))

    if include_tail:
        reserve = 0
    elif tail_reserve is not None:
        reserve = max(0, min(int(tail_reserve), max(total - warmup, 0)))
    else:
        reserve = max(10, total // 10) if total > warmup + 10 else 0
    end = total - reserve

    indices = list(range(warmup, end, stride))
    if len(indices) > max_trials:
        indices = indices[-max_trials:]

    if adapter is None and engine is None:
        adapter = _default_adapter()
    engine = engine or adapter.engine
    # The graded ticket must be the ticket the app would have served: the
    # adapter's formatter is what turns an engine ticket into served numbers
    # (e.g. Pick 10 plays 10 numbers against a 20-number draw).
    format_prediction = adapter._format_prediction if adapter is not None else None

    run_id = run_id or uuid.uuid4().hex[:12]
    rows: list[dict] = []
    skipped = 0
    for i in indices:
        target = draws[i]
        history = draws[max(0, i - history_window):i]
        try:
            ticket = engine.predict(key, history=history, use_nn=False)
        except Exception as exc:
            skipped += 1
            logger.warning("retro trial skipped for %s at index %d: %s", key, i, exc)
            continue
        if format_prediction is not None:
            formatted, _flat = format_prediction(key, ticket.primary, ticket.bonus)
            numbers = [int(n) for n in formatted["main_numbers"]]
            bonus = [int(n) for n in formatted["bonus_numbers"]]
        else:
            numbers = [int(n) for n in ticket.primary]
            bonus = [int(n) for n in ticket.bonus or []]
        rows.append(_score_trial(
            key, rules, numbers, bonus, target,
            model=getattr(ticket, "strategy_used", None) or "ensemble",
            weights=getattr(ticket, "weights_used", None) or {},
            contributions=getattr(ticket, "strategy_contributions", None) or {},
            history_draws_used=len(history),
            run_id=run_id,
        ))

    if persist and rows:
        _append_rows(key, rows)

    return {
        "game": key,
        "simulated": True,
        "source": "retro_backtest",
        "run_id": run_id,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "draws_available": total,
        "trials": len(rows),
        "trials_skipped": skipped,
        "tail_reserved": reserve,
        "tail_included": bool(include_tail),
        "warmup": warmup,
        "stride": stride,
        "history_window": history_window,
        "first_target_date": rows[0]["actual_draw_date"] if rows else None,
        "last_target_date": rows[-1]["actual_draw_date"] if rows else None,
    }


# --------------------------------------------------------------------------
# Report: simulated evidence, same math as the live accuracy views.
# --------------------------------------------------------------------------

def _ticket_level(unique: list[dict], game: str) -> dict:
    """Whole-ticket hits vs chance across unique simulated trials."""
    rules = game_rules_from_config(game)
    total_hits = sum(int(r.get("hits") or 0) for r in unique)
    total_expected = sum(float(r.get("expected_hits_by_chance") or 0.0) for r in unique)
    lift = round(total_hits / total_expected, 4) if total_expected > 0 else None
    p_value = None
    method = None
    if rules.primary_unique and unique:
        universe = max(rules.primary_max - rules.primary_min + 1, 1)
        per_entry = [
            (universe, rules.primary_count, len(set(r.get("numbers") or [])))
            for r in unique
        ]
        p_value = round(_exact_upper_tail(per_entry, total_hits), 6)
        method = "exact convolution of per-ticket hypergeometrics"
    return {
        "trials": len(unique),
        "total_hits": total_hits,
        "expected_hits_by_chance": round(total_expected, 4),
        "mean_hits": round(total_hits / len(unique), 4) if unique else None,
        "lift_vs_random": lift,
        "p_value_at_least_these_hits": p_value,
        "p_value_method": method,
    }


def retro_report(game: str, *, current_weights: dict | None = None) -> dict:
    """Per-game simulated accuracy: ticket level + per-strategy credit.

    Strategy credit is the live ``strategy_credit`` on the simulated rows,
    so a strategy's simulated lift is computed exactly like its live lift.
    The reweight proposal, when current weights are supplied, is advisory
    only: it is what the live gates would say about this simulated
    evidence. It is never applied from here.
    """
    key = str(game or "").strip().lower()
    rows = read_simulated(key)
    graded = [r for r in rows if r.get("status") == "graded" and r.get("simulated")]
    unique = dedupe_graded(graded)
    runs: dict[str, int] = {}
    for row in graded:
        runs[str(row.get("run_id") or "unknown")] = runs.get(str(row.get("run_id") or "unknown"), 0) + 1
    advisory = None
    if current_weights:
        advisory = propose_reweight(key, graded, current_weights)
        advisory = {**advisory, "applied": False, "basis": "simulated_retro_backtest"}
    return {
        "game": key,
        "simulated": True,
        "source": "retro_backtest",
        "warning": (
            "Simulated retro-test evidence: engine picks graded against past "
            "draws they never saw. Useful for ranking strategies and tuning; "
            "it is not live served performance and not proof of a live edge."
        ),
        "n_rows_stored": len(rows),
        "n_graded_unique": len(unique),
        "runs": runs,
        "ticket_level": _ticket_level(unique, key),
        "strategies": strategy_credit(graded, key),
        "advisory_reweight": advisory,
    }
