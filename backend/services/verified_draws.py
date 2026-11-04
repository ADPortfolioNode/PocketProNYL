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
        if len(primary) != rules.primary_count:
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


def official_draws_for_date(game: str, target: date) -> tuple[list[Draw], str | None]:
    """Official draw(s) for one past date. Returns ([draws], error-or-None).

    An empty list with no error means the game has no configured dataset,
    is unsupported for date lookup, or the dataset has no row for that date
    (a draw that did not happen, or not published yet). Network and parse
    failures return an error string instead of raising, so grading can
    leave the entry pending rather than crash.
    """
    game = str(game or "").strip().lower()
    if game in UNSUPPORTED_GAMES:
        return [], None
    endpoints = DATASET_ENDPOINTS.get(game) or []
    if not endpoints:
        return [], None
    try:
        from utils.game_validation import dataset_id_from_url

        dataset_id = dataset_id_from_url(endpoints[0])
    except Exception:
        dataset_id = None
    if not dataset_id:
        return [], None
    try:
        rules = game_rules_from_config(game)
    except Exception as exc:
        return [], f"unknown game rules for {game}: {exc}"
    day_after = target + timedelta(days=1)
    where = (
        f"draw_date >= '{target.isoformat()}T00:00:00' "
        f"AND draw_date < '{day_after.isoformat()}T00:00:00'"
    )
    params = urllib.parse.urlencode({"$where": where, "$limit": "100"})
    url = f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            rows = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [], f"official draw fetch failed for {game} {target.isoformat()}: {exc}"
    except Exception as exc:  # pragma: no cover - defensive
        return [], f"official draw fetch failed for {game} {target.isoformat()}: {exc}"
    draws: list[Draw] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        draws.extend(_draws_from_row(game, row, rules, dataset_id))
    return draws, None


def verified_draws_enabled() -> bool:
    return os.environ.get("NYL_VERIFIED_DRAWS", "1") != "0"
