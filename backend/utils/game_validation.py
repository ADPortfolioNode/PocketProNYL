"""Per-game draw validation: keep every game's history to its own dataset and rules.

Two checks guard against "game bleed" (one game's draws stored or trained as
another game's):

1. Provenance. Each NY Open Data dataset has its own columns. A row that carries
   a column only another game's dataset has (``cash_ball`` in Take 5, ``bonus``
   from NY Lotto, ``draw_number`` from Quick Draw in Pick 10), or that was tagged
   with a ``source_dataset`` this game does not use, came from the wrong dataset.
2. Numbers. The draw must fit the game: exact count, range, uniqueness, bonus.

``strict=True`` (ingest) also requires the game's own number column, because at
ingest we know the source schema. ``strict=False`` (repair and history loading)
only rejects on positive evidence (foreign columns, wrong dataset, or numbers
that do not fit), so a valid row imported some other way is never deleted.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable
from urllib.parse import urlparse

from config import DATASET_ENDPOINTS, GAME_CONFIGS, GAME_PREDICTION_FORMATS

# Column names of each game's NY Open Data dataset(s), from
# https://data.ny.gov/api/views/<id>.json (checked 2026-10-20).
_TAKE5_FIELDS = {
    "draw_date", "evening_winning_numbers", "evening_bonus",
    "midday_winning_numbers", "midday_bonus",
}
_DAILY_NUMBERS_WIN4_FIELDS = {
    "draw_date", "midday_daily", "midday_daily_sum", "evening_daily",
    "evening_daily_sum", "midday_win_4", "midday_win_4_sum", "evening_win_4",
    "evening_win_4_sum", "midday_daily_booster", "evening_daily_booster",
    "midday_win_4_booster", "evening_win_4_booster",
}
GAME_SOURCE_FIELDS: dict[str, set[str]] = {
    "take5": _TAKE5_FIELDS,
    # Pick 3 reads n4w8-wxte (draw_date, midday_daily, evening_daily) and the
    # shared Daily Numbers / Win 4 dataset hsys-3def.
    "pick3": set(_DAILY_NUMBERS_WIN4_FIELDS),
    "numbers": set(_DAILY_NUMBERS_WIN4_FIELDS),
    "win4": set(_DAILY_NUMBERS_WIN4_FIELDS),
    "powerball": {"draw_date", "winning_numbers", "multiplier", "double_play_winning_numbers"},
    "megamillions": {"draw_date", "winning_numbers", "mega_ball", "multiplier"},
    "pick10": {"draw_date", "winning_numbers"},
    "cash4life": {"draw_date", "winning_numbers", "cash_ball"},
    "quickdraw": {
        "draw_date", "draw_number", "draw_time", "winning_numbers", "extra_multiplier",
        "money_dots_winning_number", "money_dots_prize_amount",
    },
    "nylotto": {"draw_date", "winning_numbers", "bonus", "extra"},
    "millionaireforlife": {"draw_date", "winning_numbers", "mill_ball"},
}

# Fields the ingest pipeline adds itself; never evidence of a foreign dataset.
DERIVED_FIELDS = {"draw_date", "draw_datetime", "draw_session", "winning_numbers", "source_dataset"}

# The game's own number column(s). Strict validation needs at least one.
GAME_NUMBER_FIELDS: dict[str, tuple[str, ...]] = {
    "take5": ("midday_winning_numbers", "evening_winning_numbers"),
    "pick3": ("midday_daily", "evening_daily", "winning_numbers"),
    "numbers": ("winning_numbers", "midday_daily", "evening_daily"),
    "win4": ("winning_numbers", "midday_win_4", "evening_win_4"),
}

# Columns every row of the game's dataset has that tell it apart from a look-alike
# game. Pick 10 rows (draw_date + 20 numbers of 1..80) fit Quick Draw's number
# rules, but every Quick Draw row carries a draw number / draw time.
GAME_SIGNATURE_FIELDS: dict[str, tuple[str, ...]] = {
    "quickdraw": ("draw_number", "draw_time"),
}

# Digit games: every number field holds exactly this many digits.
DIGIT_GAMES = {"pick3": 3, "numbers": 3, "win4": 4}
_DIGIT_GAME_FIELDS = {
    "pick3": ("midday_daily", "evening_daily"),
    "numbers": ("midday_daily", "evening_daily"),
    "win4": ("midday_win_4", "evening_win_4"),
}

# Storage-only tolerance for real historical draws under older matrices, so the
# repair step never deletes a genuine old draw. Training still applies the
# current GAME_CONFIGS rules (out-of-range old draws are skipped there, as before).
#   Mega Millions: 5/52 (2002), 5/56+1/46 (2005), 5/75+1/15 (2013), 5/70+1/25 (2017).
#   Powerball (dataset starts 2010): 5/59+1/39 until 2012, 5/59+1/35, 5/69+1/26 (2015).
HISTORICAL_RANGE_OVERRIDES: dict[str, dict[str, int]] = {
    "megamillions": {"primary_max": 75, "bonus_max": 52},
    "powerball": {"primary_max": 69, "bonus_max": 39},
}


def dataset_id_from_url(url: str | None) -> str | None:
    """Return the Socrata dataset id (e.g. ``dg63-4siq``) from an endpoint URL."""
    path = urlparse(str(url or "")).path
    match = re.search(r"/(?:api/views|resource|d)/([a-z0-9]{4}-[a-z0-9]{4})", path)
    return match.group(1) if match else None


def tag_rows_with_source(rows: list, endpoint: str | None) -> list:
    """Stamp each dict row with the dataset id it was fetched from (provenance)."""
    source = dataset_id_from_url(endpoint)
    if not source:
        return list(rows or [])
    return [{**row, "source_dataset": source} if isinstance(row, dict) else row for row in rows or []]


def allowed_dataset_ids(game: str) -> set[str]:
    return {
        dataset_id
        for dataset_id in (dataset_id_from_url(url) for url in DATASET_ENDPOINTS.get(game, []) or [])
        if dataset_id
    }


def foreign_fields_for(game: str) -> set[str]:
    """Columns that exist only in other games' datasets."""
    own = GAME_SOURCE_FIELDS.get(game, set()) | DERIVED_FIELDS
    others: set[str] = set()
    for other_game, fields in GAME_SOURCE_FIELDS.items():
        if other_game != game:
            others |= fields
    return others - own


def _tokens(raw: Any) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", str(raw if raw is not None else ""))]


def _digits(raw: Any) -> list[int]:
    return [int(d) for d in re.findall(r"\d", str(raw if raw is not None else ""))]


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _rules(game: str) -> dict[str, Any]:
    cfg = dict(GAME_CONFIGS.get(game) or {})
    override = HISTORICAL_RANGE_OVERRIDES.get(game) or {}
    return {
        "primary_count": int(cfg.get("primary_count", 5)),
        "primary_min": int(cfg.get("primary_min", 1)),
        "primary_max": int(override.get("primary_max", cfg.get("primary_max", 99))),
        "primary_unique": bool(cfg.get("primary_unique", True)),
        "bonus_count": int(cfg.get("bonus_count", 0) or 0),
        "bonus_min": int(cfg.get("bonus_min", 1)),
        "bonus_max": int(override.get("bonus_max", cfg.get("bonus_max", 99))),
        "bonus_keys": [str(k).lower() for k in (cfg.get("bonus_keys") or [])],
        "embedded_bonus": bool(cfg.get("embedded_bonus_in_winning_numbers")),
        # Games whose suggestion format carries a bonus ball (Take 5 config lists
        # a bonus key but the game has no bonus; its old "bonus" fields are ignored).
        "validate_bonus": int((GAME_PREDICTION_FORMATS.get(game) or {}).get("bonus_count") or 0) > 0,
    }


def _check_board(raw: Any, rules: dict[str, Any]) -> str | None:
    """Validate one lotto-style board string. Returns a reason or None."""
    numbers = _tokens(raw)
    expected = rules["primary_count"] + (rules["bonus_count"] if rules["embedded_bonus"] else 0)
    if len(numbers) != expected:
        return "wrong_count"
    primary = numbers[: rules["primary_count"]]
    if any(n < rules["primary_min"] or n > rules["primary_max"] for n in primary):
        return "out_of_range"
    if rules["primary_unique"] and len(set(primary)) != len(primary):
        return "duplicate_numbers"
    if rules["embedded_bonus"] and rules["bonus_count"] > 0:
        bonus = numbers[rules["primary_count"]:]
        if any(n < rules["bonus_min"] or n > rules["bonus_max"] for n in bonus):
            return "bonus_out_of_range"
    return None


def _check_digits(raw: Any, count: int) -> str | None:
    return None if len(_digits(raw)) == count else "wrong_digit_count"


def validate_game_record(game: str, record: dict | None, *, strict: bool = True) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for one stored or incoming draw record of ``game``."""
    game_key = str(game or "").strip().lower()
    if game_key not in GAME_CONFIGS:
        return False, "unknown_game"
    if not isinstance(record, dict) or not record:
        return False, "empty_record"

    lowered = {str(k).lower(): v for k, v in record.items()}

    source = lowered.get("source_dataset")
    allowed = allowed_dataset_ids(game_key)
    if _has_value(source) and allowed and str(source).strip().lower() not in allowed:
        return False, "wrong_dataset"

    foreign = foreign_fields_for(game_key)
    if any(key in foreign and _has_value(value) for key, value in lowered.items()):
        return False, "foreign_fields"

    signature = GAME_SIGNATURE_FIELDS.get(game_key)
    if signature and not any(_has_value(lowered.get(field)) for field in signature):
        return False, "missing_signature_fields"

    rules = _rules(game_key)

    if game_key in DIGIT_GAMES:
        count = DIGIT_GAMES[game_key]
        winning = lowered.get("winning_numbers")
        if _has_value(winning):
            # Expanded child row (or compact import): winning_numbers is this game's.
            reason = _check_digits(winning, count)
            return (False, reason) if reason else (True, "ok")
        sessions = [lowered.get(f) for f in _DIGIT_GAME_FIELDS[game_key] if _has_value(lowered.get(f))]
        if not sessions:
            return False, "missing_numbers"
        if any(_check_digits(value, count) is None for value in sessions):
            return True, "ok"
        return False, "wrong_digit_count"

    if game_key == "take5":
        sessions = [
            lowered.get(f) for f in ("midday_winning_numbers", "evening_winning_numbers")
            if _has_value(lowered.get(f))
        ]
        winning = lowered.get("winning_numbers")
        if _has_value(winning):
            reason = _check_board(winning, rules)
            if reason:
                return False, reason
        if not sessions:
            if strict or not _has_value(winning):
                return False, "missing_numbers"
            return True, "ok"
        reasons = [_check_board(value, rules) for value in sessions]
        if any(reason is None for reason in reasons):
            return True, "ok"
        return False, reasons[0] or "invalid_numbers"

    winning = lowered.get("winning_numbers")
    if not _has_value(winning):
        return False, "missing_numbers"
    reason = _check_board(winning, rules)
    if reason:
        return False, reason

    if rules["validate_bonus"] and not rules["embedded_bonus"]:
        for key in rules["bonus_keys"]:
            value = lowered.get(key)
            if not _has_value(value):
                continue
            parsed = _tokens(value)
            if len(parsed) != 1 or not (rules["bonus_min"] <= parsed[0] <= rules["bonus_max"]):
                return False, "bonus_out_of_range"
    return True, "ok"


def partition_records(
    game: str,
    records: Iterable[dict],
    *,
    strict: bool = True,
) -> tuple[list[dict], Counter]:
    """Split records into (valid, Counter of rejection reasons)."""
    valid: list[dict] = []
    rejected: Counter = Counter()
    for record in records or []:
        ok, reason = validate_game_record(game, record, strict=strict)
        if ok:
            valid.append(record)
        else:
            rejected[reason] += 1
    return valid, rejected
