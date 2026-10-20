"""Convert Chroma metadata or raw lists into Draw sequences."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any

from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw, GameRules


def _parse_numbers(raw_value: Any) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", str(raw_value or ""))]


def _parse_fixed_digits(raw_value: Any, count: int) -> list[int]:
    digits = re.findall(r"\d", str(raw_value or ""))
    return [int(digit) for digit in digits[-count:]] if len(digits) >= count else []


def _extract_primary_candidate(metadata: dict, game: str | None = None) -> list[int]:
    preferred: list[Any] = []
    fallback: list[Any] = []
    pick3_fields: list[Any] = []
    win4_fields: list[Any] = []
    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        key_norm = re.sub(r"[^a-z0-9]+", "", key_lower)
        if "draw_number" in key_lower or not str(value or "").strip():
            continue
        if key_lower in ("winning_numbers", "winningnumbers"):
            preferred.append(value)
        elif key_norm in {"middaydaily", "eveningdaily"}:
            pick3_fields.append(value)
        elif key_norm in {"middaywin4", "eveningwin4"}:
            win4_fields.append(value)
        elif "winning" in key_lower and "number" in key_lower:
            fallback.append(value)
        elif "numbers" in key_lower or "result" in key_lower:
            fallback.append(value)

    normalized_game = str(game or "").lower()
    if normalized_game in {"pick3", "numbers"}:
        parse_value = lambda value: _parse_fixed_digits(value, 3)
        candidates = preferred + pick3_fields + fallback
        expected = 3
    elif normalized_game == "win4":
        parse_value = lambda value: _parse_fixed_digits(value, 4)
        candidates = preferred + win4_fields + fallback
        expected = 4
    else:
        parse_value = _parse_numbers
        candidates = preferred + pick3_fields + win4_fields + fallback
        expected = 0

    for candidate in candidates:
        numbers = parse_value(candidate)
        if expected and len(numbers) == expected:
            return numbers
        if not expected and numbers:
            return numbers
    return []


def _extract_bonus_values(metadata: dict, rules: GameRules) -> list[int]:
    if rules.bonus_count <= 0:
        return []
    bonus_keys = [str(k).lower() for k in (GAME_BONUS_KEYS.get(rules.game, []) or [])]
    values: list[int] = []
    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        if key_lower in bonus_keys or ("bonus" in key_lower and "winning" not in key_lower):
            for item in _parse_numbers(value):
                if rules.bonus_min <= item <= rules.bonus_max:
                    values.append(item)
                    if len(values) >= rules.bonus_count:
                        return values[: rules.bonus_count]
    return values[: rules.bonus_count]


# Lazy import from config to avoid circular deps at module level
GAME_BONUS_KEYS: dict[str, list[str]] = {}


def _extract_draw_date(metadata: dict) -> date | None:
    """Extract the calendar date shared by all draw metadata variants."""
    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        if key_lower not in {"draw_date", "drawdate", "date", "drawn_at", "draw_datetime"}:
            continue
        if value in (None, ""):
            continue
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw).date()
        except ValueError:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return None


def _ensure_bonus_keys() -> None:
    if GAME_BONUS_KEYS:
        return
    from config import GAME_CONFIGS

    for game, cfg in GAME_CONFIGS.items():
        GAME_BONUS_KEYS[game] = list(cfg.get("bonus_keys") or [])


def metadata_to_draw(metadata: dict, game: str, draw_id: str | None = None) -> Draw | None:
    """Parse one Chroma metadata record into a Draw."""
    _ensure_bonus_keys()
    _ensure_embedded_flags()
    rules = game_rules_from_config(game)
    winning = _extract_primary_candidate(metadata, game=game)
    if not winning:
        return None

    embedded_bonus: list[int] = []
    if (
        bool((GAME_CONFIGS_EMBEDDED.get(game)))
        and rules.bonus_count > 0
        and len(winning) >= rules.primary_count + rules.bonus_count
    ):
        embedded_bonus = winning[rules.primary_count : rules.primary_count + rules.bonus_count]
        winning = winning[: rules.primary_count]

    primary = _clamp_primary(winning, rules)
    if len(primary) != rules.primary_count:
        return None

    bonus = _extract_bonus_values(metadata, rules)
    if not bonus and embedded_bonus:
        bonus = [
            int(n) for n in embedded_bonus
            if rules.bonus_min <= int(n) <= rules.bonus_max
        ][: rules.bonus_count]

    return Draw(
        primary=primary,
        bonus=bonus,
        draw_id=draw_id,
        metadata=dict(metadata),
        date=_extract_draw_date(metadata),
    )


GAME_CONFIGS_EMBEDDED: dict[str, bool] = {}


def _ensure_embedded_flags() -> None:
    if GAME_CONFIGS_EMBEDDED:
        return
    from config import GAME_CONFIGS

    for game, cfg in GAME_CONFIGS.items():
        GAME_CONFIGS_EMBEDDED[game] = bool(cfg.get("embedded_bonus_in_winning_numbers"))


def _clamp_primary(numbers: list[int], rules: GameRules) -> list[int]:
    valid = [n for n in numbers if rules.primary_min <= int(n) <= rules.primary_max]
    if not valid:
        return []
    if rules.primary_unique:
        seen: set[int] = set()
        uniq: list[int] = []
        for value in valid:
            if value not in seen:
                seen.add(value)
                uniq.append(value)
        valid = uniq
    if len(valid) < rules.primary_count:
        return []
    return valid[: rules.primary_count]


# Games whose source rows carry one board per draw session in separate fields.
_SESSION_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "take5": (("midday_winning_numbers", "midday"), ("evening_winning_numbers", "evening")),
    "pick3": (("midday_daily", "midday"), ("evening_daily", "evening")),
}


def _expand_sessions(metadata: dict, game: str) -> list[dict]:
    """Split a source row holding midday and evening boards into one record per draw."""
    fields = _SESSION_FIELDS.get(str(game or "").lower())
    if not fields or not isinstance(metadata, dict):
        return [metadata]
    if metadata.get("winning_numbers") or metadata.get("draw_session"):
        return [metadata]
    session_keys = {name for name, _ in fields}
    expanded: list[dict] = []
    for field, session in fields:
        raw = metadata.get(field)
        if raw is None or not str(raw).strip():
            continue
        row = {key: value for key, value in metadata.items() if key not in session_keys}
        row["winning_numbers"] = raw
        row["draw_session"] = session
        expanded.append(row)
    return expanded or [metadata]


def draws_from_metadatas(
    metadatas: list[dict],
    game: str,
    ids: list[str] | None = None,
) -> list[Draw]:
    """Parse metadata list into Draw objects (oldest-first order)."""
    _ensure_embedded_flags()
    _ensure_bonus_keys()
    draws: list[Draw] = []
    id_list = ids or []
    for index, meta in enumerate(metadatas or []):
        if not isinstance(meta, dict):
            continue
        draw_id = id_list[index] if index < len(id_list) else None
        rows = _expand_sessions(meta, game)
        for row in rows:
            row_id = draw_id
            if draw_id and len(rows) > 1:
                row_id = f"{draw_id}:{row.get('draw_session')}"
            draw = metadata_to_draw(row, game, draw_id=row_id)
            if draw:
                draws.append(draw)
    return draws


def draws_from_lists(rows: list[list[int]], game: str) -> list[Draw]:
    """Build Draw list from raw number rows (for tests)."""
    rules = game_rules_from_config(game)
    draws: list[Draw] = []
    for row in rows:
        primary = row[: rules.primary_count]
        bonus = row[rules.primary_count : rules.primary_count + rules.bonus_count]
        if len(primary) == rules.primary_count:
            draws.append(Draw(primary=list(primary), bonus=list(bonus)))
    return draws


def _session_rank(metadata: dict | None) -> str:
    session = str((metadata or {}).get("draw_session") or "").strip().lower()
    if session == "midday":
        return "0"
    if session == "evening":
        return "1"
    return session or "9"


FULL_SCAN_MAX_ROWS = int(os.environ.get("DRAW_LOADER_FULL_SCAN_MAX", "200000"))
_PAGE_SIZE = 5000


def _dedupe_key(draw: Draw) -> tuple:
    meta = draw.metadata or {}
    return (
        draw.draw_date,
        str(meta.get("draw_session") or "").lower(),
        str(meta.get("draw_number") or ""),
        str(meta.get("draw_time") or ""),
        tuple(draw.primary),
        tuple(draw.bonus or []),
    )


def _read_all(collection, total: int) -> tuple[list, list]:
    metadatas: list = []
    ids: list = []
    offset = 0
    while offset < total:
        page = collection.get(limit=min(_PAGE_SIZE, total - offset), offset=offset, include=["metadatas"])
        batch = list((page or {}).get("metadatas") or [])
        batch_ids = list((page or {}).get("ids") or [])
        if not batch:
            break
        metadatas.extend(batch)
        ids.extend(batch_ids)
        offset += len(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return metadatas, ids


def from_chroma(game: str, limit: int = 500) -> list[Draw]:
    """Load the most recent draw history of ``game`` from its own collection, oldest-first.

    Chroma's storage order is insertion order, not draw date, so the newest
    ``limit`` draws are picked after sorting by date, never by offset. Rows that
    belong to another game (foreign dataset columns, numbers that do not fit)
    are dropped, and exact duplicates are collapsed.
    """
    from services.chroma_client import chroma_client
    from utils.game_validation import validate_game_record

    game_key = str(game or "").strip().lower()
    collection = chroma_client.client.get_collection(game_key)
    total = int(collection.count() or 0)
    if total <= 0:
        return []

    if total <= FULL_SCAN_MAX_ROWS:
        metadatas, ids = _read_all(collection, total)
    else:
        # Very large collections (Quick Draw): bounded tail read, as before.
        fetch_limit = min(total, max(int(limit), 1) * 4)
        data = collection.get(limit=fetch_limit, offset=max(0, total - fetch_limit), include=["metadatas"])
        metadatas = list(data.get("metadatas") or [])
        ids = list(data.get("ids") or [])

    kept_meta: list[dict] = []
    kept_ids: list = []
    for index, meta in enumerate(metadatas):
        ok, _reason = validate_game_record(game_key, meta, strict=False)
        if ok:
            kept_meta.append(meta)
            kept_ids.append(ids[index] if index < len(ids) else None)

    draws = draws_from_metadatas(kept_meta, game_key, kept_ids)
    draws.sort(
        key=lambda draw: (
            draw.draw_date or date.min,
            _session_rank(draw.metadata),
            str((draw.metadata or {}).get("draw_number") or "").zfill(12),
            str(draw.draw_id or ""),
        )
    )
    unique: list[Draw] = []
    seen: set[tuple] = set()
    for draw in draws:
        key = _dedupe_key(draw)
        if key in seen:
            continue
        seen.add(key)
        unique.append(draw)

    fetch_limit = max(int(limit), 1)
    if len(unique) > fetch_limit:
        unique = unique[-fetch_limit:]
    return unique
