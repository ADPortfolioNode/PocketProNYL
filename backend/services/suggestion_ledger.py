"""Append-only ledger of every suggestion the API serves.

One JSONL file per game under ``$DATA_DIR/ledger/<game>/`` so games never share
a file (PR #14 isolation):

    suggestions.jsonl   one line per served suggestion (never rewritten)
    feedback.jsonl      draws a user reported through /api/prediction/feedback

Each suggestion records the game, when it was served, the draw it targets,
the numbers, and the model/version that produced it. Grading happens at read
time against ingested draws, so the ledger itself is never edited.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from utils.paths import data_dir

LEDGER_VERSION = 1
_GAME_RE = re.compile(r"^[a-z0-9]+$")
_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def ledger_root() -> str:
    return os.environ.get("LEDGER_DIR") or os.path.join(data_dir(), "ledger")


def _game_dir(game: str) -> str:
    key = str(game or "").strip().lower()
    if not _GAME_RE.match(key):
        raise ValueError(f"Invalid game key for ledger: {game!r}")
    return os.path.join(ledger_root(), key)


def suggestions_path(game: str) -> str:
    return os.path.join(_game_dir(game), "suggestions.jsonl")


def feedback_path(game: str) -> str:
    return os.path.join(_game_dir(game), "feedback.jsonl")


def _append_line(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    with _LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            handle.write(line)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def _read_lines(path: str, game: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # A record that names another game is never read for this one.
            if isinstance(record, dict) and str(record.get("game") or "").lower() == game:
                records.append(record)
    return records


def model_version(result: dict) -> str:
    """Stable fingerprint of what produced a suggestion (engine, strategy, weights)."""
    payload = {
        "engine": result.get("engine"),
        "model_strategy": result.get("model_strategy") or result.get("strategy_used"),
        "weights": result.get("strategy_weights") or {},
        "champion": result.get("model_champion"),
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return digest[:12]


def _numbers_from_result(result: dict) -> tuple[list[int], list[int]]:
    formatted = result.get("formatted_prediction") or {}
    primary = (
        result.get("predicted_main_numbers")
        or formatted.get("main_numbers")
        or result.get("predicted_numbers")
        or []
    )
    bonus = result.get("predicted_bonus_numbers") or formatted.get("bonus_numbers") or []
    if not result.get("predicted_main_numbers") and not formatted.get("main_numbers"):
        try:
            from config import GAME_CONFIGS

            count = int((GAME_CONFIGS.get(result.get("game"), {}) or {}).get("primary_count") or len(primary))
            primary, bonus = primary[:count], (bonus or primary[count:])
        except Exception:
            pass
    return [int(n) for n in primary], [int(n) for n in bonus]


class SuggestionLedger:
    """Read/append access to the per-game ledgers. Paths resolve at call time."""

    def append_suggestion(self, game: str, entry: dict) -> dict:
        key = str(game).strip().lower()
        record = {
            **entry,
            "id": entry.get("id") or uuid.uuid4().hex,
            "game": key,
            "ledger_version": LEDGER_VERSION,
        }
        _append_line(suggestions_path(key), record)
        return record

    def read_suggestions(self, game: str) -> list[dict]:
        key = str(game).strip().lower()
        return _read_lines(suggestions_path(key), key)

    def append_feedback(self, game: str, record: dict) -> dict:
        key = str(game).strip().lower()
        stored = {**record, "id": record.get("id") or uuid.uuid4().hex, "game": key,
                  "ledger_version": LEDGER_VERSION}
        _append_line(feedback_path(key), stored)
        return stored

    def read_feedback(self, game: str) -> list[dict]:
        key = str(game).strip().lower()
        return _read_lines(feedback_path(key), key)

    def record_served(
        self,
        game: str,
        result: dict,
        *,
        source: str,
        recent_k: int | None = None,
        target_draw_date: str | None = None,
        now: datetime | None = None,
    ) -> dict | None:
        """Persist one served suggestion. Never raises: serving must not fail on disk errors."""
        try:
            key = str(game).strip().lower()
            if str(result.get("game") or key).strip().lower() != key:
                return None
            primary, bonus = _numbers_from_result({**result, "game": key})
            if not primary:
                return None
            from services.draw_schedule import next_draw

            served = now or datetime.now(timezone.utc)
            target = next_draw(key, served)
            if target_draw_date:
                target = {"draw_date": str(target_draw_date)[:10], "draw_session": None,
                          "draw_time": None, "basis": "requested"}
            entry = {
                "served_at": served.astimezone(timezone.utc).isoformat(),
                "served_at_epoch": served.timestamp(),
                "target_draw_date": target.get("draw_date"),
                "target_draw_session": target.get("draw_session"),
                "target_draw_time": target.get("draw_time"),
                "target_basis": target.get("basis"),
                "primary": primary,
                "bonus": bonus,
                "model": result.get("model_strategy") or result.get("strategy_used") or "unknown",
                "engine": result.get("engine") or "legacy",
                "model_version": model_version(result),
                "source": source,
                "recent_k": recent_k,
            }
            return self.append_suggestion(key, entry)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not record served suggestion for %s: %s", game, exc)
            return None


suggestion_ledger = SuggestionLedger()
