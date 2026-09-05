"""Convert Chroma metadata or raw lists into Draw sequences."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from prediction.config.loader import game_rules_from_config
from prediction.core.types import Draw, GameRules


def _parse_numbers(raw_value: Any) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", str(raw_value or ""))]


def _extract_primary_candidate(metadata: dict) -> list[int]:
    preferred: list[Any] = []
    fallback: list[Any] = []
    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        if "draw_number" in key_lower or not str(value or "").strip():
            continue
        if key_lower in ("winning_numbers", "winningnumbers"):
            preferred.append(value)
        elif "winning" in key_lower and "number" in key_lower:
            fallback.append(value)
        elif "numbers" in key_lower or "result" in key_lower:
            fallback.append(value)
    for candidate in preferred + fallback:
        numbers = _parse_numbers(candidate)
        if numbers:
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
    global GAME_BONUS_KEYS
    if GAME_BONUS_KEYS:
        return
    from config import GAME_CONFIGS

    for game, cfg in GAME_CONFIGS.items():
        GAME_BONUS_KEYS[game] = list(cfg.get("bonus_keys") or [])


def metadata_to_draw(metadata: dict, game: str, draw_id: str | None = None) -> Draw | None:
    """Parse one Chroma metadata record into a Draw."""
    _ensure_bonus_keys()
    rules = game_rules_from_config(game)
    winning = _extract_primary_candidate(metadata)
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
    global GAME_CONFIGS_EMBEDDED
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
        draw = metadata_to_draw(meta, game, draw_id=draw_id)
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


def from_chroma(game: str, limit: int = 500) -> list[Draw]:
    """Load draw history from ChromaDB (newest first in DB, returned oldest-first)."""
    from services.chroma_client import chroma_client

    collection = chroma_client.client.get_collection(game)
    data = collection.get(limit=limit, include=["metadatas"])
    metadatas = data.get("metadatas") or []
    ids = data.get("ids") or []
    draws = draws_from_metadatas(list(reversed(metadatas)), game, list(reversed(ids)))
    return draws