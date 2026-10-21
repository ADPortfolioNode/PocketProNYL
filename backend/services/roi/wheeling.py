"""Lottery wheels with a verified coverage guarantee.

A wheel spreads a pool of ``n`` chosen numbers over several ``k``-number
tickets. An abbreviated wheel guarantees "at least ``t`` matches on one ticket
IF ``m`` of the drawn numbers are in your pool". The tickets are built by a
greedy covering search and the guarantee is then checked exhaustively against
every ``m``-subset of the pool, so a reported guarantee is always true.

A wheel changes how many tickets you buy and how matches are spread across
them. It does not change the odds of any single ticket.
"""

from __future__ import annotations

from itertools import combinations
from math import comb

import numpy as np

from services.game_odds import GAME_PLAY, PICKS_CANNOT_CHANGE_ODDS, hypergeom_pmf

MAX_POOL = 15
MAX_FULL_WHEEL_TICKETS = 5005


def _verify(pool: list[int], tickets: list[tuple[int, ...]], t: int, m: int) -> bool:
    ticket_sets = [set(x) for x in tickets]
    return all(any(len(ts & set(subset)) >= t for ts in ticket_sets) for subset in combinations(pool, m))


def build_wheel(pool: list[int], ticket_size: int, guarantee: int, if_match: int) -> dict:
    pool = sorted(set(int(n) for n in pool))
    n, k, t, m = len(pool), int(ticket_size), int(guarantee), int(if_match)
    if not (1 <= t <= k <= n) or not (t <= m <= n):
        raise ValueError("Need 1 <= guarantee <= ticket size <= pool size and guarantee <= if_match <= pool size.")
    if t > min(k, m):
        raise ValueError("guarantee cannot exceed the ticket size or the matched count.")
    if n > MAX_POOL:
        raise ValueError(f"Pool is limited to {MAX_POOL} numbers.")
    candidates = list(combinations(pool, k))
    if t == k and m == n:
        # Full wheel: the only way to guarantee every combination.
        tickets = candidates
        if len(tickets) > MAX_FULL_WHEEL_TICKETS:
            raise ValueError("Full wheel is too large.")
    else:
        index = {v: i for i, v in enumerate(pool)}
        cand = np.zeros((len(candidates), n), dtype=np.int8)
        for row, combo in enumerate(candidates):
            cand[row, [index[v] for v in combo]] = 1
        subsets = list(combinations(pool, m))
        subs = np.zeros((len(subsets), n), dtype=np.int8)
        for row, combo in enumerate(subsets):
            subs[row, [index[v] for v in combo]] = 1
        covers = (cand.astype(np.int16) @ subs.T.astype(np.int16)) >= t
        uncovered = np.ones(len(subsets), dtype=bool)
        chosen: list[int] = []
        while uncovered.any():
            gains = covers[:, uncovered].sum(axis=1)
            best = int(np.argmax(gains))
            if gains[best] == 0:
                break
            chosen.append(best)
            uncovered &= ~covers[best]
        tickets = [candidates[i] for i in chosen]
    verified = _verify(pool, tickets, t, m)
    return {
        "pool": pool,
        "ticket_size": k,
        "tickets": [list(x) for x in tickets],
        "ticket_count": len(tickets),
        "full_wheel_ticket_count": comb(n, k),
        "guarantee": f"At least {t} matches on one ticket if {m} of the drawn numbers are in your {n}-number pool.",
        "guarantee_verified": verified,
    }


def wheel_for_game(game: str, pool: list[int], guarantee: int, if_match: int,
                   ticket_size: int | None = None) -> dict:
    from prediction.config.loader import game_rules_from_config

    rules = game_rules_from_config(game)
    play = GAME_PLAY.get(game) or {}
    if not rules.primary_unique:
        raise ValueError("Wheels apply to games where each number is drawn at most once.")
    if any(n < rules.primary_min or n > rules.primary_max for n in pool):
        raise ValueError(f"Pool numbers must be between {rules.primary_min} and {rules.primary_max}.")
    k = int(ticket_size or play.get("play_size") or rules.primary_count)
    result = build_wheel(pool, k, guarantee, if_match)
    universe = rules.primary_max - rules.primary_min + 1
    drawn = rules.primary_count
    n = len(result["pool"])
    p_condition = sum(hypergeom_pmf(universe, drawn, n, h) for h in range(int(if_match), min(n, drawn) + 1))
    price = float(play.get("price") or 1.0)
    return {
        **result,
        "game": game,
        "cost": round(result["ticket_count"] * price, 2),
        "price_per_ticket": price,
        "probability_condition_met": round(p_condition, 8),
        "condition_note": (f"The guarantee only applies when {if_match}+ of the {drawn} drawn numbers land in your pool; "
                           f"that happens about 1 in {round(1 / p_condition):,} draws." if p_condition > 0 else None),
        "disclaimer": PICKS_CANNOT_CHANGE_ODDS,
    }
