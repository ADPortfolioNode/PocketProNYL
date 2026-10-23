"""Pattern-search settings from the environment (no hard-coded knobs).

Every key can be overridden per game with a ``_<GAME>`` suffix, e.g.
``PATTERN_WINDOWS_TAKE5=50,100,all`` or ``PATTERN_FDR_Q_POWERBALL=0.05``.

    PATTERN_AUTO_ENABLED            1   rerun the scan after ingests that add draws
    PATTERN_RERUN_DEBOUNCE_SECONDS  30  wait this long after the last ingest before scanning
    PATTERN_WINDOWS                 25,50,100,200,all   lookback windows per signal
    PATTERN_FDR_Q                   0.10  BH-FDR q over every signal x window tested
    PATTERN_MIN_DRAWS               150   fewer draws -> no scan (status insufficient_data)
    PATTERN_HISTORY_LIMIT           1000  most recent draws loaded per scan
    PATTERN_AUTO_PROMOTE            1   run champion/challenger for validation survivors
    PATTERN_DEMOTE_MIN_NEW_DRAWS    30  new draws needed before a promoted signal is re-judged
    PATTERN_CHAIN_HISTORY_LIMIT     400 draws given to the champion/challenger test (as /api/chain/evaluate)
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from prediction.patterns.search import parse_windows

DEFAULT_WINDOWS = "25,50,100,200,all"


def _get(key: str, game: str | None, default: str) -> str:
    if game:
        raw = os.getenv(f"{key}_{str(game).upper()}")
        if raw not in (None, ""):
            return raw
    raw = os.getenv(key)
    return default if raw in (None, "") else raw


def _flag(key: str, game: str | None, default: str) -> bool:
    return _get(key, game, default).strip().lower() not in ("0", "false", "no", "off")


def _num(key: str, game: str | None, default, cast):
    try:
        return cast(_get(key, game, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PatternSettings:
    enabled: bool
    debounce_seconds: float
    windows: tuple
    fdr_q: float
    min_draws: int
    history_limit: int
    auto_promote: bool
    demote_min_new_draws: int
    chain_history_limit: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["windows"] = ["all" if w == 0 else w for w in self.windows]
        return data


def load_settings(game: str | None = None) -> PatternSettings:
    windows = tuple(parse_windows(_get("PATTERN_WINDOWS", game, DEFAULT_WINDOWS))) or (0,)
    q = _num("PATTERN_FDR_Q", game, 0.10, float)
    return PatternSettings(
        enabled=_flag("PATTERN_AUTO_ENABLED", game, "1"),
        debounce_seconds=max(0.0, _num("PATTERN_RERUN_DEBOUNCE_SECONDS", game, 30.0, float)),
        windows=windows,
        fdr_q=q if 0 < q < 1 else 0.10,
        min_draws=max(30, _num("PATTERN_MIN_DRAWS", game, 150, int)),
        history_limit=max(100, min(_num("PATTERN_HISTORY_LIMIT", game, 1000, int), 5000)),
        auto_promote=_flag("PATTERN_AUTO_PROMOTE", game, "1"),
        demote_min_new_draws=max(1, _num("PATTERN_DEMOTE_MIN_NEW_DRAWS", game, 30, int)),
        chain_history_limit=max(100, min(_num("PATTERN_CHAIN_HISTORY_LIMIT", game, 400, int), 2000)),
    )
