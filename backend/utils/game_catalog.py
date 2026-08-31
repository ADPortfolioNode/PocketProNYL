"""
Dynamic game catalog for PocketPro:NYL.

Builds the API-facing game list from GAME_CONFIGS / titles / schedules /
prediction formats, enriched with live (or cached) draw counts and
rule-derived training defaults for suggestions.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, List, Optional

from config import (
    DATASET_ENDPOINTS,
    GAME_ALIASES,
    GAME_CONFIGS,
    GAME_PREDICTION_FORMATS,
    GAME_PREDICTION_SCHEDULES,
    GAME_TITLES,
)


def _combination(n: int, k: int) -> float:
    if k < 0 or n < 0 or k > n:
        return 0.0
    return float(math.comb(n, k))


def _space_size(rules: Dict[str, Any]) -> float:
    """Estimate discrete outcome space size for a game."""
    primary_count = int(rules.get("primary_count") or 0)
    primary_min = int(rules.get("primary_min") or 0)
    primary_max = int(rules.get("primary_max") or 0)
    primary_unique = bool(rules.get("primary_unique", True))
    span = max(0, primary_max - primary_min + 1)

    if primary_unique:
        primary_space = _combination(span, primary_count)
    else:
        primary_space = float(span ** primary_count) if primary_count else 0.0

    bonus_count = int(rules.get("bonus_count") or 0)
    if bonus_count <= 0:
        return max(primary_space, 1.0)

    bonus_min = int(rules.get("bonus_min") or 1)
    bonus_max = int(rules.get("bonus_max") or bonus_min)
    bonus_span = max(0, bonus_max - bonus_min + 1)
    # Bonus balls are usually ordered / independent of the primary unique set.
    bonus_space = float(bonus_span ** bonus_count) if bonus_span else 1.0
    return max(primary_space * bonus_space, 1.0)


def _daily_draw_rate(game: str) -> float:
    schedule = GAME_PREDICTION_SCHEDULES.get(game) or {}
    daily = float(schedule.get("daily_draws") or 0)
    if daily > 0:
        return daily
    weekday = schedule.get("weekday_draws") or {}
    if isinstance(weekday, dict) and weekday:
        # Approximate average draws/day from weekly schedule.
        return float(sum(int(v or 0) for v in weekday.values())) / 7.0
    return 1.0


def suggestion_format_for_game(game: str) -> Dict[str, Any]:
    """Return the expected suggestion/output shape for a game."""
    fmt = deepcopy(GAME_PREDICTION_FORMATS.get(game) or {})
    rules = GAME_CONFIGS.get(game) or {}
    if not fmt:
        fmt = {
            "main_count": int(rules.get("primary_count") or 0),
            "main_min": int(rules.get("primary_min") or 0),
            "main_max": int(rules.get("primary_max") or 0),
            "bonus_count": int(rules.get("bonus_count") or 0),
            "bonus_min": rules.get("bonus_min"),
            "bonus_max": rules.get("bonus_max"),
            "unique_main": bool(rules.get("primary_unique", True)),
            "sort_main": bool(rules.get("primary_unique", True)),
            "main_label": "Numbers",
            "bonus_label": "Bonus" if int(rules.get("bonus_count") or 0) else None,
        }
    return fmt


def compute_training_defaults_for_game(game: str) -> Dict[str, Any]:
    """
    Derive training knobs from game rules + draw frequency + suggestion shape.

    Goal: tune capacity / target for the expected suggestion outcome
    (main board + optional bonus), not a one-size-fits-all profile.
    """
    rules = GAME_CONFIGS.get(game) or {}
    fmt = suggestion_format_for_game(game)
    space = _space_size(rules)
    daily = _daily_draw_rate(game)
    log_space = math.log10(max(space, 10.0))

    # Baseline
    target_accuracy = 0.90
    max_iterations = 40
    train_size = 0.25
    n_estimators = 250
    max_depth = 18
    window_size = 3
    blend_step = 0.05
    data_limit = 0
    auto_tune = True

    # High-frequency games: favor recent signal, lower target, less depth.
    if daily >= 50:
        target_accuracy = 0.82
        max_iterations = 25
        train_size = 0.35
        n_estimators = 120
        max_depth = 10
        window_size = 1
        blend_step = 0.08
        data_limit = 100_000
        reason_freq = f"very high frequency (~{daily:.0f} draws/day)"
    elif daily >= 2:
        target_accuracy = 0.86
        max_iterations = 30
        train_size = 0.30
        n_estimators = 160
        max_depth = 12
        window_size = 2
        blend_step = 0.06
        reason_freq = f"high frequency (~{daily:.1f} draws/day)"
    elif daily >= 1:
        target_accuracy = 0.88
        max_iterations = 35
        n_estimators = 200
        max_depth = 16
        window_size = 3
        reason_freq = f"daily cadence (~{daily:.1f} draws/day)"
    else:
        target_accuracy = 0.91
        max_iterations = 45
        train_size = 0.22
        n_estimators = 300
        max_depth = 20
        window_size = 4
        blend_step = 0.04
        reason_freq = f"low frequency (~{daily:.2f} draws/day)"

    # Large outcome spaces need more trees / depth and a slightly higher bar.
    if log_space >= 8:  # jackpot-scale
        n_estimators = max(n_estimators, 350)
        max_depth = max(max_depth, 22)
        max_iterations = max(max_iterations, 50)
        target_accuracy = min(0.94, target_accuracy + 0.03)
        window_size = max(window_size, 5)
        train_size = min(train_size, 0.22)
        reason_space = f"large outcome space (~1e{log_space:.1f})"
    elif log_space >= 6:
        n_estimators = max(n_estimators, 300)
        max_depth = max(max_depth, 20)
        target_accuracy = min(0.93, target_accuracy + 0.02)
        window_size = max(window_size, 4)
        reason_space = f"medium-large outcome space (~1e{log_space:.1f})"
    elif log_space <= 3:
        n_estimators = min(n_estimators, 180)
        max_depth = min(max_depth, 14)
        target_accuracy = max(0.80, target_accuracy - 0.03)
        reason_space = f"small outcome space (~1e{log_space:.1f})"
    else:
        reason_space = f"moderate outcome space (~1e{log_space:.1f})"

    bonus_count = int(fmt.get("bonus_count") or rules.get("bonus_count") or 0)
    if bonus_count > 0:
        n_estimators = min(600, n_estimators + 30)
        reason_bonus = f"includes {bonus_count} bonus ball(s) in suggestion"
    else:
        reason_bonus = "main-board-only suggestion"

    main_count = int(fmt.get("main_count") or rules.get("primary_count") or 0)
    reasoning = (
        f"{GAME_TITLES.get(game, game)}: {reason_freq}; {reason_space}; {reason_bonus}. "
        f"Suggestion expects {main_count} main number(s)"
        + (f" + {bonus_count} bonus" if bonus_count else "")
        + "."
    )

    return {
        "target_accuracy": round(float(target_accuracy), 2),
        "max_iterations": int(max_iterations),
        "train_size": round(float(train_size), 2),
        "n_estimators": int(n_estimators),
        "max_depth": int(max_depth),
        "random_state": 42,
        "blend_step": round(float(blend_step), 2),
        "data_limit": int(data_limit),
        "window_size": int(window_size),
        "auto_tune": bool(auto_tune),
        "validation_size": None,
        "model_strategy": "ensemble",
        "optimization_applied": True,
        "reasoning": reasoning,
        "derived_from": {
            "outcome_space": space,
            "log10_space": round(log_space, 3),
            "daily_draw_rate": round(daily, 3),
            "main_count": main_count,
            "bonus_count": bonus_count,
        },
    }


def _draw_counts_for(games: List[str], refresh: bool = False) -> Dict[str, int]:
    counts = {game: 0 for game in games}
    try:
        from services.chroma_client import chroma_client

        snapshots = chroma_client.get_collections_snapshot(
            games,
            timeout_seconds=8.0,
            refresh=refresh,
        )
        for snap in snapshots or []:
            name = snap.get("name")
            if name in counts:
                counts[name] = int(snap.get("count") or 0)
    except Exception:
        try:
            from state.draw_counts import get_all_draw_counts

            cached = get_all_draw_counts(games)
            for game in games:
                counts[game] = int(cached.get(game, 0) or 0)
        except Exception:
            pass
    return counts


def build_game_entry(game: str, draw_count: int = 0) -> Dict[str, Any]:
    """Full dynamic catalog entry for one game."""
    rules = deepcopy(GAME_CONFIGS.get(game) or {})
    title = GAME_TITLES.get(game, game)
    fmt = suggestion_format_for_game(game)
    schedule = deepcopy(GAME_PREDICTION_SCHEDULES.get(game) or {})
    training = compute_training_defaults_for_game(game)
    endpoints = list(DATASET_ENDPOINTS.get(game) or [])
    return {
        "id": game,
        "key": game,
        "name": title,
        "title": title,
        "aliases": list(GAME_ALIASES.get(game) or []),
        "rules": rules,
        "suggestion_format": fmt,
        "schedule": schedule,
        "dataset_endpoints": endpoints,
        "draw_count": int(draw_count or 0),
        "has_draws": int(draw_count or 0) > 0,
        "ready_for_training": int(draw_count or 0) >= 50,
        "ready_for_suggestions": int(draw_count or 0) >= 20,
        "training_defaults": training,
    }


def get_game_catalog(*, refresh_counts: bool = False) -> Dict[str, Any]:
    """
    Dynamically build the full game catalog for the API/frontend.

    Returns both legacy fields (games, titles) and a rich ``catalog`` list.
    """
    game_keys = list(GAME_CONFIGS.keys())
    counts = _draw_counts_for(game_keys, refresh=refresh_counts)
    catalog = [build_game_entry(game, counts.get(game, 0)) for game in game_keys]
    return {
        "games": game_keys,
        "titles": {game: GAME_TITLES.get(game, game) for game in game_keys},
        "catalog": catalog,
        "total_games": len(game_keys),
        "populated_games": sum(1 for item in catalog if item["has_draws"]),
    }


def get_game_detail(game: str, *, refresh_counts: bool = True) -> Optional[Dict[str, Any]]:
    from config import resolve_game_key

    key = resolve_game_key(game)
    if not key:
        return None
    counts = _draw_counts_for([key], refresh=refresh_counts)
    return build_game_entry(key, counts.get(key, 0))
