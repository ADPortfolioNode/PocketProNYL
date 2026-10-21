"""ROI routes: EV ranking, anti-popular filter, wheels, bankroll.

Every response carries the plain statement that picks cannot change the odds.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import GAME_CONFIGS
from prediction.config.loader import game_rules_from_config
from services.roi import bankroll
from services.roi.ev_ranking import ev_ranking
from services.roi.popularity import anti_popular_adjust
from services.roi.wheeling import wheel_for_game
from utils.validation import _require_game_key

router = APIRouter()


def _game(raw: str) -> str:
    try:
        return _require_game_key(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _parse_jackpots(raw: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (raw or "").split(","):
        if ":" not in part:
            continue
        game, value = part.split(":", 1)
        try:
            out[_game(game.strip())] = float(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Bad jackpot value: {part}")
    return out


@router.get("/api/roi/ev_ranking")
async def roi_ev_ranking(jackpots: Optional[str] = Query(None, description="game:cash,... e.g. powerball:267000000")):
    return ev_ranking(_parse_jackpots(jackpots))


class AntiPopularRequest(BaseModel):
    game: str
    numbers: List[int]


@router.post("/api/roi/anti_popular")
async def roi_anti_popular(request: AntiPopularRequest):
    game = _game(request.game)
    rules = game_rules_from_config(game)
    if len(request.numbers) != rules.primary_count:
        raise HTTPException(status_code=400, detail=f"{game} plays have {rules.primary_count} numbers.")
    return anti_popular_adjust(game, list(request.numbers), rules)


class WheelRequest(BaseModel):
    game: str
    pool: List[int]
    guarantee: int
    if_match: int
    ticket_size: Optional[int] = None


@router.post("/api/roi/wheel")
async def roi_wheel(request: WheelRequest):
    game = _game(request.game)
    try:
        return wheel_for_game(game, request.pool, request.guarantee, request.if_match, request.ticket_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class BankrollEntry(BaseModel):
    game: str
    stake: float
    winnings: float = 0.0
    tickets: Optional[int] = None
    date: Optional[str] = None
    note: Optional[str] = None


def _ev_lookup() -> dict[str, float]:
    return {r["game"]: r["ev_per_dollar"] for r in ev_ranking()["ranking"]}


@router.get("/api/roi/bankroll")
async def roi_bankroll(game: Optional[str] = None):
    if game:
        key = _game(game)
        return {"status": "ok", **bankroll.summary(key, _ev_lookup().get(key))}
    return bankroll.all_summaries(list(GAME_CONFIGS.keys()), _ev_lookup())


@router.post("/api/roi/bankroll")
async def roi_bankroll_add(entry: BankrollEntry):
    key = _game(entry.game)
    try:
        saved = bankroll.record(key, entry.stake, entry.winnings, tickets=entry.tickets, on=entry.date, note=entry.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "entry": saved, "summary": bankroll.summary(key, _ev_lookup().get(key))}
