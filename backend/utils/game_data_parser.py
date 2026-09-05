import re
import logging
from typing import Any, Dict, List, Optional

from config import GAME_CONFIGS

logger = logging.getLogger(__name__)

def _parse_numbers(raw_value: Any) -> List[int]:
    """Parses a raw value (string, list, etc.) into a list of integers."""
    if isinstance(raw_value, list):
        return [int(token) for token in raw_value if isinstance(token, (int, float))]
    
    # Handle string representation of a list, e.g., "[1, 2, 3]"
    if isinstance(raw_value, str) and raw_value.strip().startswith("[") and raw_value.strip().endswith("]"):
        try:
            list_from_str = eval(raw_value) # Use eval carefully, assuming trusted input
            if isinstance(list_from_str, list):
                return [int(token) for token in list_from_str if isinstance(token, (int, float))]
        except (SyntaxError, NameError):
            pass # Fallback to regex if not a valid list string

    tokens = re.findall(r"\d+", str(raw_value or ""))
    return [int(token) for token in tokens]

def _parse_pick3_digits(raw_value: Any) -> List[int]:
    """Parses a raw value specifically for pick3, ensuring 3 digits."""
    text = str(raw_value or "").strip()
    if not text:
        return []
    
    # Handle string representation of a list, e.g., "[1, 2, 3]"
    if text.startswith("[") and text.endswith("]"):
        try:
            list_from_str = eval(text)
            if isinstance(list_from_str, list):
                digits = [str(int(d)) for d in list_from_str if isinstance(d, (int, float))]
                if len(digits) == 3:
                    return [int(d) for d in digits]
        except (SyntaxError, NameError):
            pass

    # Extract all digits, then pad/truncate to exactly 3
    all_digits = re.findall(r"\d", text) # Find individual digits
    if not all_digits:
        return []
    
    # Join, pad/truncate, and convert to int list
    padded = "".join(all_digits).zfill(3)[-3:]
    return [int(digit) for digit in padded]

def _parse_fixed_digits(raw_value: Any, count: int) -> List[int]:
    """Parse a fixed-width lottery digit result while preserving leading zeroes."""
    digits = re.findall(r"\d", str(raw_value or ""))
    return [int(digit) for digit in digits[-count:]] if len(digits) >= count else []

def _get_rules(game: str) -> Dict[str, Any]:
    """Returns the game rules, merging defaults with configured values."""
    base = {
        "primary_count": 5,
        "primary_min": 1,
        "primary_max": 99,
        "primary_unique": True,
        "bonus_count": 0,
        "bonus_min": 1,
        "bonus_max": 99,
        "bonus_keys": [],
    }
    configured = GAME_CONFIGS.get(game, {}) or {}
    merged = {**base, **configured}
    merged["bonus_keys"] = [str(k).lower() for k in (merged.get("bonus_keys") or [])]
    return merged

def _clamp_primary(numbers: List[int], rules: Dict[str, Any]) -> List[int]:
    """Clamps and ensures uniqueness for primary numbers based on game rules."""
    valid = [
        int(n)
        for n in numbers
        if rules["primary_min"] <= int(n) <= rules["primary_max"]
    ]

    if not valid:
        return []

    if rules.get("primary_unique", True):
        seen = set()
        uniq = []
        for value in valid:
            if value not in seen:
                seen.add(value)
                uniq.append(value)
        valid = uniq

    if len(valid) < rules["primary_count"]:
        return []
    return valid[:rules["primary_count"]]

def _extract_bonus_values(metadata: Dict[str, Any], rules: Dict[str, Any]) -> List[int]:
    """Extracts bonus numbers from metadata based on game rules."""
    bonus_count = int(rules.get("bonus_count", 0) or 0)
    if bonus_count <= 0:
        return []

    values = []
    bonus_min = int(rules.get("bonus_min", 1))
    bonus_max = int(rules.get("bonus_max", 99))

    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        if key_lower in rules["bonus_keys"] or ("bonus" in key_lower and "winning" not in key_lower):
            parsed = _parse_numbers(value)
            for item in parsed:
                if bonus_min <= item <= bonus_max:
                    values.append(item)
                    if len(values) >= bonus_count:
                        return values[:bonus_count]
    return values[:bonus_count]

def _extract_primary_candidate(metadata: Dict[str, Any], game: str | None = None) -> List[int]:
    """Extracts a candidate list of primary numbers from metadata."""
    preferred = []
    fallback = []
    pick3_specific_fields = []

    for key, value in (metadata or {}).items():
        key_lower = str(key).lower()
        if "draw_number" in key_lower or not str(value or "").strip():
            continue

        if key_lower in ("winning_numbers", "winningnumbers"):
            preferred.append(value)
        elif key_lower in ("midday_daily", "evening_daily"):
            pick3_specific_fields.append(value)
        elif "winning" in key_lower and "number" in key_lower:
            fallback.append(value)
        elif "numbers" in key_lower or "result" in key_lower:
            fallback.append(value)

    normalized_game = str(game or "").lower()
    parse_value = _parse_pick3_digits if normalized_game in ("pick3", "numbers") else _parse_numbers
    if normalized_game == "win4":
        parse_value = lambda value: _parse_fixed_digits(value, 4)

    # For pick3, prioritize specific daily fields if winning_numbers is not immediately parsable
    if normalized_game in ("pick3", "numbers", "win4"):
        for candidate in preferred + pick3_specific_fields + fallback:
            numbers = parse_value(candidate)
            if len(numbers) == 3: # Ensure exactly 3 digits for pick3
                return numbers
    else:
        for candidate in preferred + pick3_specific_fields + fallback:
            numbers = parse_value(candidate)
            if numbers:
                return numbers

    return []

def _rows_to_dicts(rows: List[Any], column_names: List[str]) -> List[Dict[str, Any]]:
    """Convert list-of-lists (SODA2) or mixed rows into list-of-dicts."""
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [row for row in rows if isinstance(row, dict)]

    dicts: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            dicts.append(row)
            continue
        if not isinstance(row, (list, tuple)):
            continue
        # SODA2 often prefixes each row with sid/id metadata cells before field values.
        values = list(row)
        if column_names and len(values) > len(column_names):
            values = values[-len(column_names) :]
        mapped: Dict[str, Any] = {}
        for idx, value in enumerate(values):
            key = column_names[idx] if idx < len(column_names) else f"col_{idx}"
            mapped[key] = value
        if mapped:
            dicts.append(mapped)
    return dicts


def _extract_rows_and_columns(data: Any) -> tuple[List[Dict[str, Any]], List[str]]:
    """Normalize Socrata responses to (rows as dicts, column_names) for ingestion."""
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            meta = data.get("meta")
            view = meta.get("view") if isinstance(meta, dict) else {}
            columns_meta = view.get("columns") if isinstance(view, dict) else []
            column_names: List[str] = []
            if isinstance(columns_meta, list):
                for idx, col in enumerate(columns_meta):
                    if isinstance(col, dict):
                        column_names.append(str(col.get("fieldName") or col.get("name") or f"col_{idx}"))
                    else:
                        column_names.append(f"col_{idx}")
            return _rows_to_dicts(rows, column_names), column_names

        # Fallback for flat-object payloads where records are under common keys
        for key in ("results", "records", "rows"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                if candidate and isinstance(candidate[0], dict):
                    all_keys: set[str] = set()
                    for item in candidate:
                        if isinstance(item, dict):
                            all_keys.update(item.keys())
                    return [item for item in candidate if isinstance(item, dict)], list(all_keys)
                return _rows_to_dicts(candidate, []), []

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            all_keys = set()
            for item in data:
                if isinstance(item, dict):
                    all_keys.update(item.keys())
            return [item for item in data if isinstance(item, dict)], list(all_keys)
        return _rows_to_dicts(data, []), []
    return [], []

def _extract_record_sequence(metadata: Dict[str, Any], game: str) -> List[int]:
    """Extracts the full winning number sequence (primary + bonus) from metadata."""
    rules = _get_rules(game)
    winning_numbers = _extract_primary_candidate(metadata, game=game)
    if not winning_numbers:
        return []

    primary_count = int(rules["primary_count"])
    bonus_count = int(rules.get("bonus_count", 0) or 0)

    embedded_bonus = []
    if rules.get("embedded_bonus_in_winning_numbers") and bonus_count > 0 and len(winning_numbers) >= primary_count + bonus_count:
        embedded_bonus = winning_numbers[primary_count:primary_count + bonus_count]
        winning_numbers = winning_numbers[:primary_count]

    primary_numbers = _clamp_primary(winning_numbers, rules)
    if len(primary_numbers) != primary_count:
        logger.debug(f"[{game}] _extract_record_sequence: Clamped primary numbers length mismatch. Expected {primary_count}, got {len(primary_numbers)}. Metadata: {metadata}")
        return []

    bonus_numbers = _extract_bonus_values(metadata, rules)
    if not bonus_numbers and embedded_bonus:
        bonus_min = int(rules.get("bonus_min", 1))
        bonus_max = int(rules.get("bonus_max", 99))
        bonus_numbers = [int(n) for n in embedded_bonus if bonus_min <= int(n) <= bonus_max][:bonus_count]

    if bonus_count > 0 and bonus_numbers:
        return primary_numbers + bonus_numbers[:bonus_count]
    
    return primary_numbers