"""Hooks called from ingest when new draws are detected."""

from __future__ import annotations

import os

from prediction.core.draw_loader import metadata_to_draw


def on_new_draw_metadata(game: str, metadata: dict, draw_id: str | None = None) -> dict | None:
    """
    Update ensemble weights when a new real draw is ingested.

    Returns update summary or None if modular engine is disabled / parse fails.
    """
    if os.environ.get("PREDICTION_ENGINE", "modular").lower() == "legacy":
        return None

    from utils.game_validation import validate_game_record

    if not validate_game_record(game, metadata, strict=False)[0]:
        return None
    draw = metadata_to_draw(metadata, game, draw_id=draw_id)
    if draw is None:
        return None

    try:
        from prediction.engine import LotteryPredictionEngine

        engine = LotteryPredictionEngine()
        return engine.update_weights(game, draw)
    except Exception as exc:
        return {"game": game, "status": "weight_update_skipped", "error": str(exc)}