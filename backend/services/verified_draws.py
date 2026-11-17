"""Fetch official NY Lottery draws on demand for grading, when a target draw
is not in the local Chroma collection.

Read-time only: fetched draws are used to grade ledger entries in memory.
Nothing is written to the ledger, to Chroma, or to feedback files, so a
verified grade never changes stored data. Only past draw dates are fetched
(target date strictly before today in the lottery timezone); today's and
future draws stay pending until the draw has happened and the date has
passed. Quick Draw is excluded: it draws every few minutes, so a date alone
cannot pin one draw.

The source is the game's own configured NY Open Data dataset (the same
DATASET_ENDPOINTS the ingest pipeline uses), queried for one day through
the Socrata resource API. Set NYL_VERIFIED_DRAWS=0 to disable the fallback
(grading then behaves exactly as before: un-ingested draws stay pending).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from config import DATASET_ENDPOINTS
from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw

USER_AGENT = "PocketProNYL/1.0"
TIMEOUT_SECONDS = 10
SOCRATA_DOMAIN = "data.ny.gov"

# Bonus-ball column per game, from the game's NY Open Data dataset fields.
BONUS_COLUMNS = {
    "megamillions": "mega_ball",
    "cash4life": "cash_ball",
    "nylotto": "bonus",
    "millionaireforlife": "mill_ball",
    "powerball": "powerball",
}

# Session games have no single winning_numbers column: each row carries one
# column per session.
SESSION_NUMBER_COLUMNS = {
    "take5": ("midday_winning_numbers", "evening_winning_numbers"),
    "pick3": ("midday_daily", "evening_daily"),
    "numbers": ("midday_daily", "evening_daily"),
    "win4": ("midday_win_4", "evening_win_4"),
}
SESSION_NAMES = ("midday", "evening")

# Games whose draws cannot be pinned by a date alone.
UNSUPPORTED_GAMES = {"quickdraw"}


def _numbers(value) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", str(value or ""))]


def _bonus_from_row(game: str, row: dict) -> list[int]:
    column = BONUS_COLUMNS.get(game)
    if column and row.get(column) not in (None, ""):
        try:
            return [int(str(row[column]).strip())]
        except (TypeError, ValueError):
            return []
    return []


def _draws_from_row(game: str, row: dict, rules, dataset_id: str) -> list[Draw]:
    try:
        day = date.fromisoformat(str(row.get("draw_date"))[:10])
    except (TypeError, ValueError):
        return []
    bonus = _bonus_from_row(game, row)
    meta_base = {"source": "verified_official", "dataset": dataset_id}
    if row.get("winning_numbers"):
        primary = _numbers(row["winning_numbers"])
        if len(primary) == rules.primary_count:
            pass
        elif (rules.bonus_count > 0
                and len(primary) == rules.primary_count + rules.bonus_count):
            # NY's powerball dataset appends the bonus ball to
            # winning_numbers ("01 21 44 50 57 05" = 5 mains + Powerball
            # 05) instead of a separate column. Split it the same way
            # the Chroma path does (metadata_to_draw's embedded bonus),
            # so a verified powerball draw grades instead of being
            # silently dropped for having one number too many. An
            # explicit bonus column, when present, still wins.
            if not bonus:
                bonus = primary[rules.primary_count:]
            primary = primary[:rules.primary_count]
        else:
            return []
        return [Draw(primary=primary, bonus=bonus, draw_id=None,
                     metadata=dict(meta_base), date=day)]
    columns = SESSION_NUMBER_COLUMNS.get(game)
    if not columns:
        return []
    draws: list[Draw] = []
    for session, column in zip(SESSION_NAMES, columns):
        if not row.get(column):
            continue
        primary = _numbers(row[column])
        if len(primary) != rules.primary_count:
            continue
        draws.append(Draw(primary=primary, bonus=bonus, draw_id=None,
                          metadata={**meta_base, "draw_session": session}, date=day))
    return draws


def official_draws_in_range(game: str, start: date, end: date) -> tuple[list[Draw], str | None]:
    """Official draws for an inclusive date range. Returns ([draws], error-or-None).

    Every configured dataset for the game is queried, not just the first:
    pick3 has two datasets (one per session feed), and querying one alone
    silently drops the other session's draws. Rows are merged and deduped
    by (date, session, numbers). A date range keeps immediate-next-draw
    grading to one fetch per served date instead of one per missing day.
    """
    game = str(game or "").strip().lower()
    if game in UNSUPPORTED_GAMES:
        return [], None
    endpoints = DATASET_ENDPOINTS.get(game) or []
    if not endpoints:
        return [], None
    try:
        rules = game_rules_from_config(game)
    except Exception as exc:
        return [], f"unknown game rules for {game}: {exc}"
    try:
        from utils.game_validation import dataset_id_from_url
    except Exception:
        return [], None
    day_after = end + timedelta(days=1)
    where = (
        f"draw_date >= '{start.isoformat()}T00:00:00' "
        f"AND draw_date < '{day_after.isoformat()}T00:00:00'"
    )
    params = urllib.parse.urlencode({"$where": where, "$limit": "500", "$order": "draw_date ASC"})
    draws: list[Draw] = []
    seen: set = set()
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            dataset_id = dataset_id_from_url(endpoint)
        except Exception:
            continue
        if not dataset_id:
            continue
        url = f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json?{params}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                rows = json.load(response)
        except Exception as exc:
            errors.append(f"official draw fetch failed for {game} {start.isoformat()}..{end.isoformat()}: {exc}")
            continue
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for draw in _draws_from_row(game, row, rules, dataset_id):
                key = (draw.draw_date.isoformat() if draw.draw_date else None,
                       _session_of(draw), tuple(draw.primary), tuple(draw.bonus or []))
                if key in seen:
                    continue
                seen.add(key)
                draws.append(draw)
    if not draws and errors:
        return [], errors[0]
    return draws, None


def _session_of(draw: Draw) -> str:
    return str((draw.metadata or {}).get("draw_session") or "").strip().lower()


def official_draws_for_date(game: str, target: date) -> tuple[list[Draw], str | None]:
    """Official draw(s) for one past date. Returns ([draws], error-or-None).

    An empty list with no error means the game has no configured dataset,
    is unsupported for date lookup, or the dataset has no row for that date
    (a draw that did not happen, or not published yet). Network and parse
    failures return an error string instead of raising, so grading can
    leave the entry pending rather than crash.
    """
    return official_draws_in_range(game, target, target)


def verified_draws_enabled() -> bool:
    return os.environ.get("NYL_VERIFIED_DRAWS", "1") != "0"
