"""Grade ledger suggestions against the real draws of the same game.

Draws come only from the game's own collection (``from_chroma(game)``, which
already drops rows that belong to another game) plus that game's own feedback
file, plus (live only) verified official draws for missing dates.

Rule: a suggestion is graded against the immediate draw after it was
served -- the first draw of that game whose moment is after ``served_at``.
That is the draw the pick actually faced. The recorded target date is kept
as a cross-check and as the rule for explicitly requested targets, but it
is not the verdict when it is wrong: a skipped or postponed target draw
used to leave the entry pending forever, and a session mismatch used to
grade against the wrong draw of the day. Entries served after their target
draw's moment are still refused as not gradable (tamper/leakage guard).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


def _draw_moment(draw: Draw, game: str) -> datetime | None:
    """When a draw happened, in the lottery timezone, or None if unknown.

    Prefers a timestamp the draw itself carries, then the game's scheduled
    draw time for that date/session (session games draw 14:30/22:30 ET,
    powerball 22:59, megamillions 23:00, millionaireforlife 23:15).

    A timestamp of exactly midnight is NOT a draw time: ingestion stores
    date-only draws as midnight (e.g. draw_datetime "2026-10-28T00:00:00"),
    and no NY game draws at midnight. Trusting it would make every draw
    sort before any same-day suggestion, so same-day suggestions would
    wrongly grade against the next day's draw. Midnight values fall
    through to the scheduled time below.
    """
    meta = draw.metadata or {}
    for key in ("draw_datetime", "drawn_at"):
        parsed = _parse_iso(meta.get(key))
        if parsed is not None and not (
                parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0):
            return parsed
    raw_time = meta.get("draw_time")
    if raw_time and draw.draw_date:
        parsed = _parse_iso(raw_time)
        if parsed is not None:
            return parsed
        try:
            from services.draw_schedule import lottery_timezone

            hours, minutes = str(raw_time).split(":")[:2]
            return datetime.combine(draw.draw_date, datetime.min.time().replace(
                hour=int(hours), minute=int(minutes)), tzinfo=lottery_timezone())
        except (ValueError, TypeError, AttributeError):
            pass
    if draw.draw_date:
        try:
            from services.draw_schedule import scheduled_draw_time

            scheduled = scheduled_draw_time(game, draw.draw_date, _session(draw) or None)
            if scheduled is not None:
                return scheduled
        except Exception:
            pass
    return None


def _draw_sort_key(draw: Draw, game: str) -> tuple:
    """Order draws chronologically; time-less draws sort at end of their day."""
    moment = _draw_moment(draw, game)
    if moment is not None:
        return (moment.timestamp(), SESSION_ORDER.get(_session(draw), 9),
                (draw.metadata or {}).get("source") == "user_reported")
    if draw.draw_date:
        try:
            from services.draw_schedule import lottery_timezone

            end_of_day = datetime.combine(draw.draw_date, datetime.min.time().replace(
                hour=23, minute=59, second=59), tzinfo=lottery_timezone())
            return (end_of_day.timestamp(), SESSION_ORDER.get(_session(draw), 9),
                    (draw.metadata or {}).get("source") == "user_reported")
        except Exception:
            pass
    return (float("inf"), SESSION_ORDER.get(_session(draw), 9), True)


def _resolve_immediate_next(entry: dict, index: dict[str, list[Draw]], game: str) -> Draw | None:
    """First draw of this game after the suggestion was served, or None."""
    served = _parse_iso(entry.get("served_at"))
    if served is None:
        return None
    try:
        from services.draw_schedule import lottery_timezone

        served_day = served.astimezone(lottery_timezone()).date()
    except Exception:
        served_day = None
    candidates = []
    for bucket in index.values():
        for draw in bucket:
            if not draw.draw_date:
                continue
            moment = _draw_moment(draw, game)
            if moment is not None:
                if moment > served:
                    candidates.append(draw)
            elif served_day is not None and draw.draw_date > served_day:
                candidates.append(draw)
    if not candidates:
        return None
    candidates.sort(key=lambda d: _draw_sort_key(d, game))
    return candidates[0]


def _target_moment(entry: dict, game: str) -> datetime | None:
    """When the entry's target draw was scheduled, or None if unknown."""
    parsed = _parse_iso(entry.get("target_draw_time"))
    if parsed is not None:
        return parsed
    try:
        target = str(entry.get("target_draw_date") or "")[:10]
        day = date.fromisoformat(target)
    except (TypeError, ValueError):
        return None
    try:
        from services.draw_schedule import scheduled_draw_time

        return scheduled_draw_time(game, day, entry.get("target_draw_session") or None)
    except Exception:
        return None


def _guard(entry: dict, game: str) -> tuple[bool, str | None]:
    """Was the suggestion served before its target draw? (leakage guard).

    Same verdicts as the legacy check, except a target whose moment can be
    derived from the schedule is compared by clock, not just by date.
    """
    served = _parse_iso(entry.get("served_at"))
    if served is None:
        return False, "missing_served_at"
    moment = _target_moment(entry, game)
    if moment is not None:
        return (served < moment), (None if served < moment else "served_after_draw")
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


def _score_against(entry: dict, draw: Draw, base: dict, pmf: list,
                   rules: GameRules, game: str, graded_against: str) -> dict:
    ticket = base["numbers"]
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
        "graded_against": graded_against,
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
        "recent_k": entry.get("recent_k"),
        # Attribution carried through for graded reweighting (absent on old entries).
        "strategy_weights": entry.get("strategy_weights") or {},
        "strategy_contributions": entry.get("strategy_contributions") or {},
    }
    # Date labels on EVERY row, whatever its status: when the suggestion
    # was served, and which draw it is expected to be judged against.
    # Pending rows need these most — they say which draw is awaited.
    # Labeling never fails grading.
    try:
        from services.suggestion_dates import expected_draw as _expected_draw
        from services.suggestion_dates import suggestion_date as _suggestion_date

        base["suggestion_date"] = _suggestion_date(entry.get("served_at"))
        exp_date, exp_session = _expected_draw(
            game, entry.get("served_at"),
            target_date=entry.get("target_draw_date"),
            target_session=entry.get("target_draw_session"),
            target_basis=entry.get("target_basis"),
            expected_date=entry.get("expected_draw_date"),
            expected_session=entry.get("expected_draw_session"))
        base["expected_draw_date"] = exp_date
        base["expected_draw_session"] = exp_session
    except Exception:
        base["suggestion_date"] = None
        base["expected_draw_date"] = None
        base["expected_draw_session"] = None
    try:
        pmf = accuracy_stats.hit_distribution(ticket, rules)
    except ValueError:
        pmf = []
    base["expected_hits_by_chance"] = round(accuracy_stats.expected_hits(pmf), 4) if pmf else None
    if str(entry.get("game") or game) != game:
        return {**base, "status": "rejected_other_game"}
    if entry.get("target_basis") == "experiment":
        # Experiments carry no target claim (their next_draw_date is the
        # serve date, not the draw date), so there is no target guard to
        # fail: the immediate-next resolution itself is the guard, since
        # the graded draw is always after served_at by construction.
        draw = _resolve_immediate_next(entry, index, game)
        if draw is None:
            return {**base, "status": "pending", "reason": "next draw not in yet"}
        return _score_against(entry, draw, base, pmf, rules, game, "immediate_next")
    if entry.get("target_basis") == "requested" or _parse_iso(entry.get("served_at")) is None:
        # Explicitly requested target, or no serve time to order by: legacy
        # target-date rule, unchanged.
        ok, reason = _served_before_draw(entry)
        if not ok and reason != "same_day_unverified":
            return {**base, "status": "not_gradable", "reason": reason}
        draw = _match_draw(entry, index)
        if draw is None:
            return {**base, "status": "pending", "reason": "target draw not ingested yet"}
        if not ok:
            return {**base, "status": "not_gradable", "reason": reason}
        return _score_against(entry, draw, base, pmf, rules, game, "target")
    # Immediate-next rule: the first draw after the suggestion was served.
    ok, reason = _guard(entry, game)
    if not ok and reason != "same_day_unverified":
        return {**base, "status": "not_gradable", "reason": reason}
    draw = _resolve_immediate_next(entry, index, game)
    if draw is None:
        return {**base, "status": "pending", "reason": "next draw not in yet"}
    if not ok:
        return {**base, "status": "not_gradable", "reason": reason}
    return _score_against(entry, draw, base, pmf, rules, game, "immediate_next")


def _merge_verified(index: dict[str, list[Draw]], fetched: list[Draw], info: dict) -> None:
    for draw in fetched:
        if not draw.draw_date:
            continue
        key = draw.draw_date.isoformat()
        bucket = index.setdefault(key, [])
        if any(_session(d) == _session(draw)
               and (d.metadata or {}).get("source") != "user_reported"
               for d in bucket):
            continue
        bucket.append(draw)
        info["verified_draws_fetched"] += 1
    for bucket in index.values():
        bucket.sort(key=lambda d: (SESSION_ORDER.get(_session(d), 9),
                                   (d.metadata or {}).get("source") == "user_reported"))


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
    live = draws is None
    if draws is None:
        try:
            draws = (loader or default_draw_loader)(game)
        except Exception as exc:  # Chroma down / empty: report pending, not a crash
            draws = []
            info["draws_error"] = str(exc)
    info["draws_loaded"] = len(draws)
    index = index_draws(draws, feedback_draws(reported or [], rules))
    graded = [grade_entry(e, index, rules, game) for e in entries]
    # Verified fallback, live path only: explicit draws are a closed world
    # (tests, backtests) and must never trigger network fetches. Read-time
    # only: fetched draws live in this index, never in Chroma, the ledger,
    # or feedback. Only past dates are fetched; today's and future draws
    # stay pending.
    info["verified_draws_fetched"] = 0
    info["verified_draws_error"] = None
    if live:
        try:
            from services import verified_draws as _verified
            from services.draw_schedule import lottery_timezone

            if _verified.verified_draws_enabled():
                today = datetime.now(lottery_timezone()).date()
                pending_idx = [i for i, g in enumerate(graded) if g["status"] == "pending"]
                if pending_idx:
                    # Requested targets: fetch the target date itself.
                    wanted_dates: set[str] = set()
                    served_dates: set[str] = set()
                    for i in pending_idx:
                        entry = entries[i]
                        if entry.get("target_basis") == "requested":
                            target = str(entry.get("target_draw_date") or "")[:10]
                            try:
                                if target and date.fromisoformat(target) < today:
                                    wanted_dates.add(target)
                            except ValueError:
                                pass
                        else:
                            served = _parse_iso(entry.get("served_at"))
                            if served is not None:
                                served_dates.add(served.astimezone(lottery_timezone()).date())
                    for target in sorted(wanted_dates):
                        fetched, error = _verified.official_draws_for_date(
                            game, date.fromisoformat(target))
                        if error and info["verified_draws_error"] is None:
                            info["verified_draws_error"] = error
                        _merge_verified(index, fetched, info)
                    # Immediate-next targets: one range fetch per served
                    # date, covering the next 31 days (past dates only).
                    for served_day in sorted(served_dates):
                        end = min(today - timedelta(days=1), served_day + timedelta(days=31))
                        if end < served_day:
                            continue
                        fetched, error = _verified.official_draws_in_range(
                            game, served_day, end)
                        if error and info["verified_draws_error"] is None:
                            info["verified_draws_error"] = error
                        _merge_verified(index, fetched, info)
                    for i in pending_idx:
                        graded[i] = grade_entry(entries[i], index, rules, game)
        except Exception as exc:  # verified fetch must never break grading
            if info["verified_draws_error"] is None:
                info["verified_draws_error"] = str(exc)
    return graded, info
