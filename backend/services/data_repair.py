"""Remove rows that do not belong in a game's Chroma collection.

Safe and idempotent: a row is removed only on positive evidence that it is not
this game's draw (it came from another game's dataset, carries another
dataset's columns, or its numbers do not fit the game). Running it twice
removes nothing the second time. It refuses to empty a collection unless
``allow_full_wipe=True``.

CLI (from ``backend/``)::

    python -m scripts.repair_game_data --game take5 --dry-run
    python -m scripts.repair_game_data --all
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from config import GAME_CONFIGS
from utils.game_validation import validate_game_record

logger = logging.getLogger(__name__)

PAGE_SIZE = 5000
DELETE_BATCH = 1000


def _get_collection(game: str):
    from routes.chroma_repository import chroma_repository

    return chroma_repository.get_collection(game)


def repair_game_collection(
    game: str,
    *,
    dry_run: bool = False,
    max_rows: int | None = None,
    allow_full_wipe: bool = False,
    collection: Any | None = None,
) -> dict[str, Any]:
    """Scan one game's collection and delete rows that fail that game's validation."""
    game_key = str(game or "").strip().lower()
    if game_key not in GAME_CONFIGS:
        return {"status": "error", "game": game_key, "message": "Unknown game."}

    coll = collection if collection is not None else _get_collection(game_key)
    if coll is None:
        return {"status": "ok", "game": game_key, "scanned": 0, "kept": 0, "removed": 0,
                "reasons": {}, "dry_run": dry_run, "message": "No collection."}

    total = int(coll.count() or 0)
    if max_rows is not None and max_rows > 0 and total > max_rows:
        message = f"Skipped: {total} rows exceeds repair limit {max_rows}."
        print(f"↳ [{game_key.upper()}] Repair {message}")
        return {"status": "skipped", "game": game_key, "scanned": 0, "kept": total, "removed": 0,
                "reasons": {}, "dry_run": dry_run, "message": message}

    bad_ids: list[str] = []
    reasons: Counter = Counter()
    scanned = 0
    offset = 0
    while offset < total:
        page = coll.get(limit=min(PAGE_SIZE, total - offset), offset=offset, include=["metadatas"])
        ids = list((page or {}).get("ids") or [])
        metas = list((page or {}).get("metadatas") or [])
        if not ids:
            break
        for index, row_id in enumerate(ids):
            meta = metas[index] if index < len(metas) else None
            ok, reason = validate_game_record(game_key, meta, strict=False)
            if not ok:
                bad_ids.append(row_id)
                reasons[reason] += 1
        scanned += len(ids)
        offset += len(ids)
        if len(ids) < PAGE_SIZE:
            break

    summary: dict[str, Any] = {
        "status": "ok",
        "game": game_key,
        "scanned": scanned,
        "kept": scanned - len(bad_ids),
        "removed": 0,
        "would_remove": len(bad_ids),
        "reasons": dict(sorted(reasons.items())),
        "dry_run": dry_run,
    }

    if bad_ids and len(bad_ids) == scanned and not allow_full_wipe:
        summary["status"] = "refused"
        summary["message"] = (
            "Every row failed validation; refusing to empty the collection "
            "(pass allow_full_wipe=True after checking the data)."
        )
        print(f"⚠ [{game_key.upper()}] Repair refused: all {scanned} rows failed validation {summary['reasons']}")
        return summary

    if bad_ids and not dry_run:
        for start in range(0, len(bad_ids), DELETE_BATCH):
            coll.delete(ids=bad_ids[start:start + DELETE_BATCH])
        summary["removed"] = len(bad_ids)
        try:
            from state.draw_counts import set_draw_count

            set_draw_count(game_key, int(coll.count() or 0))
        except Exception:
            pass

    verb = "would remove" if dry_run else "removed"
    print(
        f"✓ [{game_key.upper()}] Repair: scanned={scanned} kept={summary['kept']} "
        f"{verb}={len(bad_ids)} reasons={summary['reasons']}"
    )
    return summary


def repair_all_games(*, dry_run: bool = False, max_rows: int | None = None) -> list[dict[str, Any]]:
    results = []
    for game_key in GAME_CONFIGS.keys():
        try:
            results.append(repair_game_collection(game_key, dry_run=dry_run, max_rows=max_rows))
        except Exception as exc:
            results.append({"status": "error", "game": game_key, "message": str(exc)})
    return results
