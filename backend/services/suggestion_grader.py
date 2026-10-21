"""Grade ledger suggestions against the real draws of the same game.

Draws come only from the game's own collection (``from_chroma(game)``, which
already drops rows that belong to another game) plus that game's own feedback
file. A suggestion is graded only against the draw it targeted, and only when
that draw happened after the suggestion was served.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, Iterable

from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw, GameRules
from services import accuracy_stats, game_odds

DrawLoader = Callable[[str], list[Draw]]

SESSION_ORDER = {"midday": 0, "evening": 1}
_BONUS_POOL_GAMES = {"powerball", "megamillions", "millionaireforlife", "cash4life"}


def default_draw_loader(game: str, limit: int = 3000) -> list[Draw]:
    from prediction.core.draw_loader import from_chroma

    return from_chroma(game, limit=limit)


def feedback_draws(records: Iterable[dict], rules: GameRules) -> list[Draw]:
    draws: list[Draw] = []
    for record in records or []:
        try:
            primary = [int(n) for n in record.get("primary") or []]
            if len(primary) != rules.primary_count:
                continue
            day = date.fromisoformat(str(record.get("draw_date"))[:10]) if record.get("draw_date") else None
        except (TypeError, ValueError):
            continue
        meta = {"source": "user_reported"}
        if record.get("draw_session"):
            meta["draw_session"] = str(record["draw_session"]).lower()
        draws.append(Draw(primary=primary, bonus=[int(n) for n in record.get("bonus") or []],
                          draw_id=record.get("draw_id"), metadata=meta, date=day))
    return draws


def _session(draw: Draw) -> str:
    return str((draw.metadata or {}).get("draw_session") or "").strip().lower()


def index_draws(official: list[Draw], reported: list[Draw] | None = None) -> dict[str, list[Draw]]:
    """date ISO -> draws on that date (official first, then user-reported), session-ordered."""
    index: dict[str, list[Draw]] = {}
    for draw in official or []:
        if draw.draw_date:
            index.setdefault(draw.draw_date.isoformat(), []).append(draw)
    for draw in reported or []:
        if not draw.draw_date:
            continue
        bucket = index.setdefault(draw.draw_date.isoformat(), [])
        if any(_session(d) == _session(draw) and (d.metadata or {}).get("source") != "user_reported"
               for d in bucket):
            continue  # an official draw always wins over a user report
        bucket.append(draw)
    for bucket in index.values():
        bucket.sort(key=lambda d: (SESSION_ORDER.get(_session(d), 9),
                                   (d.metadata or {}).get("source") == "user_reported"))
    return index


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _match_draw(entry: dict, index: dict[str, list[Draw]]) -> Draw | None:
    target = entry.get("target_draw_date")
    if not target:
        return None
    bucket = index.get(str(target)[:10]) or []
    if not bucket:
        return None
    wanted = str(entry.get("target_draw_session") or "").lower()
    if wanted:
        for draw in bucket:
            if _session(draw) == wanted:
                return draw
        return None
    return bucket[0]


def _served_before_draw(entry: dict) -> tuple[bool, str | None]:
    """Was the suggestion served before its target draw? (leakage guard)."""
    served = _parse_iso(entry.get("served_at"))
    draw_time = _parse_iso(entry.get("target_draw_time"))
    if served is None:
        return False, "missing_served_at"
    if draw_time is not None:
        return (served < draw_time), (None if served < draw_time else "served_after_draw")
    try:
        from services.draw_schedule import lottery_timezone

        served_day = served.astimezone(lottery_timezone()).date()
        target_day = date.fromisoformat(str(entry.get("target_draw_date"))[:10])
    except (TypeError, ValueError):
        return False, "bad_target_date"
    if target_day > served_day:
        return True, None
    if target_day == served_day:
        return False, "same_day_unverified"
    return False, "served_after_draw"


def grade_entry(entry: dict, index: dict[str, list[Draw]], rules: GameRules, game: str) -> dict:
    ticket = [int(n) for n in entry.get("primary") or []]
    base = {
        "id": entry.get("id"),
        "served_at": entry.get("served_at"),
        "target_draw_date": entry.get("target_draw_date"),
        "target_draw_session": entry.get("target_draw_session"),
        "numbers": ticket,
        "bonus_numbers": list(entry.get("bonus") or []),
        "model": entry.get("model"),
        "model_version": entry.get("model_version"),
        "source": entry.get("source"),
    }
    try:
        pmf = accuracy_stats.hit_distribution(ticket, rules)
    except ValueError:
        pmf = []
    base["expected_hits_by_chance"] = round(accuracy_stats.expected_hits(pmf), 4) if pmf else None
    if str(entry.get("game") or game) != game:
        return {**base, "status": "rejected_other_game"}
    ok, reason = _served_before_draw(entry)
    if not ok and reason != "same_day_unverified":
        return {**base, "status": "not_gradable", "reason": reason}
    draw = _match_draw(entry, index)
    if draw is None:
        return {**base, "status": "pending", "reason": "target draw not ingested yet"}
    if not ok:
        return {**base, "status": "not_gradable", "reason": reason}
    hits_set = sorted(set(ticket) & set(draw.primary))
    hits = len(hits_set)
    bonus_hit = None
    if game in _BONUS_POOL_GAMES and entry.get("bonus") and draw.bonus:
        bonus_hit = int(entry["bonus"][0]) == int(draw.bonus[0])
    tier = game_odds.tier_for(
        game, ticket, list(draw.primary), bonus_hit=bonus_hit,
        nylotto_bonus_ball=(int(draw.bonus[0]) if game == "nylotto" and draw.bonus else None),
    )
    return {
        **base,
        "status": "graded",
        "actual_numbers": list(draw.primary),
        "actual_bonus_numbers": list(draw.bonus or []),
        "actual_draw_date": draw.draw_date.isoformat() if draw.draw_date else None,
        "actual_draw_session": _session(draw) or None,
        "draw_source": (draw.metadata or {}).get("source") or "ingested",
        "matched_numbers": hits_set,
        "hits": hits,
        "bonus_hit": bonus_hit,
        "p_value_at_least_these_hits": round(accuracy_stats.upper_tail(pmf, hits), 6) if pmf else None,
        "hit_distribution_by_chance": [round(p, 6) for p in pmf],
        "tier": tier,
    }


def grade_entries(
    game: str,
    entries: list[dict],
    *,
    draws: list[Draw] | None = None,
    reported: list[dict] | None = None,
    loader: DrawLoader | None = None,
) -> tuple[list[dict], dict]:
    rules = game_rules_from_config(game)
    info = {"draws_loaded": 0, "draws_error": None}
    if draws is None:
        try:
            draws = (loader or default_draw_loader)(game)
        except Exception as exc:  # Chroma down / empty: report pending, not a crash
            draws = []
            info["draws_error"] = str(exc)
    info["draws_loaded"] = len(draws)
    index = index_draws(draws, feedback_draws(reported or [], rules))
    return [grade_entry(e, index, rules, game) for e in entries], info
