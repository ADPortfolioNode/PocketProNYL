"""Published prize structures and exact odds for the NY Lottery draw games.

Every prize below is copied from a published source (cited next to each game).
Odds are computed from combinatorics, and tests check them against the
published odds, so a typo in a table fails the suite instead of shipping.

Nothing here can change the odds of a draw. These tables only describe what a
play pays and how often, for grading served suggestions and for the expected
value (EV) ranking.

Sources (fetched 2026-10-21):
  [PB]   https://www.powerball.com/powerball-prize-chart   ($2 play, base prizes)
  [MM]   https://www.megamillions.com/How-to-Play.aspx     ($5 play, multiplier included)
  [MFL]  https://www.valottery.com/data/draw-games/millionaireforlife
         and https://www.powerball.com/millionaire-for-life ($5, 5/58 + 1/5, nightly 11:15 pm ET)
  [T5]   https://www.nylottery.org/take-5/odds-and-prizes  (pari-mutuel, 50% of sales to prizes)
  [LOT]  https://www.law.cornell.edu/regulations/new-york/9-NYCRR-5008.7 (Lotto odds, pool %)
         https://nylottery.ny.gov/draw-game/?game=lotto    (up to 40% of sales is prize money)
  [P10]  https://www.law.cornell.edu/regulations/new-york/9-NYCRR-5011.2 (Pick 10 fixed prizes)
  [NUM]  https://www.law.cornell.edu/regulations/new-york/9-NYCRR-5009.2 (Numbers prizes)
  [W4]   https://govt.westlaw.com/nycrr/Document/I8083920b431411e389e80000845b8d3e (9 NYCRR 5010.2, Win 4)
  [C4L]  https://www.lotterypost.com/news/358085 (Cash4Life final draw 2026-02-21; replaced by MFL)
"""

from __future__ import annotations

from math import comb
from typing import Any

PICKS_CANNOT_CHANGE_ODDS = (
    "Lottery draws are random. No pick, model, filter or wheel can change the odds of "
    "winning; these tools only describe odds, cost, and prize-sharing risk."
)


def hypergeom_pmf(pool: int, drawn: int, picked: int, hits: int) -> float:
    """P(exactly ``hits`` of ``picked`` chosen numbers are among ``drawn`` of ``pool``)."""
    if hits < 0 or hits > picked or hits > drawn or picked - hits > pool - drawn:
        return 0.0
    return comb(drawn, hits) * comb(pool - drawn, picked - hits) / comb(pool, picked)


def _bonus_split(pool: int, drawn: int, white_hits: int, bonus_pool: int) -> tuple[float, float]:
    """P(white_hits) * P(bonus hit) and P(white_hits) * P(bonus miss) for a 1-ball bonus pool."""
    p_white = hypergeom_pmf(pool, drawn, drawn, white_hits)
    return p_white / bonus_pool, p_white * (bonus_pool - 1) / bonus_pool


# --- Prize tables -----------------------------------------------------------------
# Each tier: (label, white_hits, bonus_hit or None, prize_usd or None, published_odds)
# prize None = pari-mutuel / jackpot (value depends on sales and winners).

POWERBALL_TIERS = [  # [PB] base prizes per $2 play
    ("Jackpot (5 + Powerball)", 5, True, None, 292_201_338.00),
    ("Match 5", 5, False, 1_000_000, 11_688_053.52),
    ("Match 4 + Powerball", 4, True, 50_000, 913_129.18),
    ("Match 4", 4, False, 100, 36_525.17),
    ("Match 3 + Powerball", 3, True, 100, 14_494.11),
    ("Match 3", 3, False, 7, 579.76),
    ("Match 2 + Powerball", 2, True, 7, 701.33),
    ("Match 1 + Powerball", 1, True, 4, 91.98),
    ("Powerball only", 0, True, 4, 38.32),
]

# [MM] prizes at the 2x column are the minimum. A multiplier (2x..10x) is always
# included; the base (1x) value below is the 2x prize / 2 and the expected
# multiplier comes from the published multiplier odds.
MEGAMILLIONS_TIERS_BASE = [
    ("Jackpot (5 + Mega Ball)", 5, True, None, 290_472_336),
    ("Match 5", 5, False, 1_000_000, 12_629_232),
    ("Match 4 + Mega Ball", 4, True, 10_000, 893_761),
    ("Match 4", 4, False, 500, 38_859),
    ("Match 3 + Mega Ball", 3, True, 200, 13_965),
    ("Match 3", 3, False, 10, 607),
    ("Match 2 + Mega Ball", 2, True, 10, 665),
    ("Match 1 + Mega Ball", 1, True, 7, 86),
    ("Mega Ball only", 0, True, 5, 35),
]
MEGAMILLIONS_MULTIPLIER_ODDS = {2: 1 / 2.13, 3: 1 / 3.2, 4: 1 / 8, 5: 1 / 16, 10: 1 / 32}  # [MM]

MILLIONAIRE_FOR_LIFE_TIERS = [  # [MFL] $5 play; top two tiers shown at published cash value
    ("Top prize (5 + Millionaire Ball)", 5, True, 18_000_000, 22_910_580.0),
    ("Match 5", 5, False, 2_200_000, 5_727_645.0),
    ("Match 4 + Millionaire Ball", 4, True, 7_500, 86_455.0),
    ("Match 4", 4, False, 500, 21_613.7),
    ("Match 3 + Millionaire Ball", 3, True, 250, 1_662.5),
    ("Match 3", 3, False, 50, 415.6),
    ("Match 2 + Millionaire Ball", 2, True, 25, 97.7),
    ("Match 2", 2, False, 8, 24.4),
    ("Match 1 + Millionaire Ball", 1, True, 8, 15.6),
]

# [T5] Take 5: 5 of 1-39, $1, pari-mutuel. 50% of sales is the prize pool:
# 20% of the pool to match 5, 30% to match 4, 50% to match 3; match 2 = free Quick Pick.
TAKE5_TIERS = [
    ("Match 5", 5, None, None, 575_757, 57_575.70),
    ("Match 4", 4, None, None, 3_386, 508.02),
    ("Match 3", 3, None, None, 102, 25.66),
    ("Match 2 (free Quick Pick)", 2, None, None, 9.62, 1.00),
]

# [LOT] NY Lotto: 6 of 1-59 + bonus ball, $1 = two panels, pari-mutuel.
NYLOTTO_TIERS = [
    ("Jackpot (Match 6)", 6, None, None, 45_057_474),
    ("Second (5 + Bonus)", 5, True, None, 7_509_579),
    ("Third (Match 5)", 5, False, None, 144_415),
    ("Fourth (Match 4)", 4, None, None, 2_180),
    ("Fifth (Match 3)", 3, None, 1, 96),
]

PICK10_PRIZES = {10: 500_000, 9: 6_000, 8: 300, 7: 40, 6: 10, 0: 4}  # [P10] per $1
PICK10_PUBLISHED_ODDS = {10: 8_911_711, 9: 163_381, 8: 7_384, 7: 621, 6: 87, 0: 22}  # [P10]

NUMBERS_PRIZES_PER_DOLLAR = {"straight": 500, "box_3way": 160, "box_6way": 80}  # [NUM]
WIN4_PRIZES_PER_DOLLAR = {  # [W4] 9 NYCRR 5010.2(c)
    "straight": 5_000, "box_4way": 1_200, "box_6way": 800, "box_12way": 400, "box_24way": 200,
}


def _expected_mm_multiplier() -> float:
    total = sum(MEGAMILLIONS_MULTIPLIER_ODDS.values())
    return sum(m * p for m, p in MEGAMILLIONS_MULTIPLIER_ODDS.items()) / total


def computed_odds(game: str) -> list[dict[str, Any]]:
    """Exact odds per tier from combinatorics, next to the published odds."""
    rows: list[dict[str, Any]] = []
    if game == "powerball":
        for label, w, b, prize, published in POWERBALL_TIERS:
            hit, miss = _bonus_split(69, 5, w, 26)
            p = hit if b else miss
            rows.append({"tier": label, "probability": p, "published_one_in": published, "prize": prize})
    elif game == "megamillions":
        mult = _expected_mm_multiplier()
        for label, w, b, base, published in MEGAMILLIONS_TIERS_BASE:
            hit, miss = _bonus_split(70, 5, w, 24)
            p = hit if b else miss
            prize = None if base is None else base * mult
            rows.append({"tier": label, "probability": p, "published_one_in": published, "prize": prize})
    elif game == "millionaireforlife":
        for label, w, b, prize, published in MILLIONAIRE_FOR_LIFE_TIERS:
            hit, miss = _bonus_split(58, 5, w, 5)
            p = hit if b else miss
            rows.append({"tier": label, "probability": p, "published_one_in": published, "prize": prize})
    elif game == "take5":
        for label, w, _b, _prize, published, avg in TAKE5_TIERS:
            rows.append({
                "tier": label, "probability": hypergeom_pmf(39, 5, 5, w),
                "published_one_in": published, "prize": avg, "pari_mutuel": True,
            })
    elif game == "nylotto":
        for label, w, b, prize, published in NYLOTTO_TIERS:
            p_white = hypergeom_pmf(59, 6, 6, w)
            if b is None:
                p = p_white
            else:
                # Bonus is a 7th ball drawn from the remaining 53; it can only match
                # one of the 6 - w panel numbers that missed the six winners.
                p_bonus = (6 - w) / 53
                p = p_white * (p_bonus if b else 1 - p_bonus)
            rows.append({"tier": label, "probability": p, "published_one_in": published,
                         "prize": prize, "pari_mutuel": prize is None})
    elif game == "pick10":
        for hits, prize in PICK10_PRIZES.items():
            rows.append({
                "tier": f"Match {hits}", "probability": hypergeom_pmf(80, 20, 10, hits),
                "published_one_in": PICK10_PUBLISHED_ODDS[hits], "prize": prize,
            })
    elif game in ("pick3", "numbers"):
        rows.append({"tier": "Straight", "probability": 1 / 1000, "published_one_in": 1000,
                     "prize": NUMBERS_PRIZES_PER_DOLLAR["straight"]})
    elif game == "win4":
        rows.append({"tier": "Straight", "probability": 1 / 10000, "published_one_in": 10000,
                     "prize": WIN4_PRIZES_PER_DOLLAR["straight"]})
    for row in rows:
        p = row["probability"]
        row["one_in"] = round(1 / p, 2) if p > 0 else None
    return rows


GAME_PLAY = {
    # price per play, numbers on one play, pari-mutuel status, status
    "powerball": {"price": 2.0, "play_size": 5, "pari_mutuel": "jackpot only", "source": "[PB]"},
    "megamillions": {"price": 5.0, "play_size": 5, "pari_mutuel": "jackpot only", "source": "[MM]"},
    "millionaireforlife": {"price": 5.0, "play_size": 5, "pari_mutuel": "top prize (second may become)",
                           "source": "[MFL]"},
    "take5": {"price": 1.0, "play_size": 5, "pari_mutuel": "all tiers", "source": "[T5]"},
    "nylotto": {"price": 0.5, "play_size": 6, "pari_mutuel": "all tiers except 3-match $1", "source": "[LOT]"},
    "pick10": {"price": 1.0, "play_size": 10, "pari_mutuel": "only if >10 top winners ($5M cap)",
               "source": "[P10]"},
    "pick3": {"price": 1.0, "play_size": 3, "pari_mutuel": "no (fixed)", "source": "[NUM]"},
    "numbers": {"price": 1.0, "play_size": 3, "pari_mutuel": "no (fixed)", "source": "[NUM]"},
    "win4": {"price": 1.0, "play_size": 4, "pari_mutuel": "no (fixed)", "source": "[W4]"},
    "cash4life": {"price": 2.0, "play_size": 5, "pari_mutuel": None, "source": "[C4L]",
                  "retired": "Final draw 2026-02-21; replaced by Millionaire for Life."},
    "quickdraw": {"price": 1.0, "play_size": None, "pari_mutuel": None, "source": None,
                  "not_modeled": "Spot-dependent prize table is not encoded."},
}

# Games where a shared top prize is split between winners (split risk matters).
PARI_MUTUEL_GAMES = {"take5", "nylotto", "powerball", "megamillions", "millionaireforlife"}


def _box_ways(digits: list[int]) -> int:
    from collections import Counter
    from math import factorial

    counts = Counter(digits)
    ways = factorial(len(digits))
    for c in counts.values():
        ways //= factorial(c)
    return ways


def tier_for(
    game: str,
    ticket: list[int],
    actual: list[int],
    bonus_hit: bool | None = None,
    nylotto_bonus_ball: int | None = None,
) -> dict[str, Any]:
    """Prize tier a ticket won against one draw, from the published tables."""
    play = GAME_PLAY.get(game) or {}
    play_size = play.get("play_size")
    hits = len(set(ticket) & set(actual))
    if play.get("retired"):
        return {"tier": "n/a", "wins": False, "prize": None, "note": play["retired"]}
    if game in ("pick3", "numbers", "win4"):
        straight = list(ticket) == list(actual)
        box = sorted(ticket) == sorted(actual)
        ways = _box_ways(list(ticket))
        table = NUMBERS_PRIZES_PER_DOLLAR if game != "win4" else WIN4_PRIZES_PER_DOLLAR
        if straight:
            return {"tier": "Straight", "wins": True, "prize": table["straight"],
                    "note": f"${table['straight']:,} on a $1 straight play."}
        if box and ways > 1:
            key = f"box_{ways}way"
            prize = table.get(key)
            return {"tier": f"Box ({ways}-way)", "wins": False, "prize": 0, "box_prize_if_boxed": prize,
                    "note": f"Box match: a $1 box play pays ${prize:,}; a straight play pays $0."}
        return {"tier": "No prize", "wins": False, "prize": 0}
    if play_size and len(ticket) != play_size:
        return {"tier": "n/a", "wins": False, "prize": None,
                "note": f"Suggestion has {len(ticket)} numbers; one {game} play has {play_size}."}
    if game == "powerball":
        tiers = POWERBALL_TIERS
    elif game == "megamillions":
        tiers = MEGAMILLIONS_TIERS_BASE
    elif game == "millionaireforlife":
        tiers = MILLIONAIRE_FOR_LIFE_TIERS
    else:
        tiers = None
    if tiers is not None:
        for label, w, b, prize, _odds in tiers:
            if hits == w and bool(bonus_hit) == bool(b):
                value = prize
                if game == "megamillions" and prize is not None:
                    value = prize * 2  # minimum (2x) prize on a $5 play
                return {"tier": label, "wins": True, "prize": value,
                        "note": "Jackpot is pari-mutuel." if prize is None else None}
        return {"tier": "No prize", "wins": False, "prize": 0}
    if game == "take5":
        for label, w, _b, _p, _odds, avg in TAKE5_TIERS:
            if hits == w:
                return {"tier": label, "wins": True, "prize": avg, "estimated": True,
                        "note": "Pari-mutuel: published average, actual amount varies by draw."}
        return {"tier": "No prize", "wins": False, "prize": 0}
    if game == "nylotto":
        if hits == 6:
            return {"tier": "Jackpot (Match 6)", "wins": True, "prize": None, "note": "Pari-mutuel."}
        if hits == 5:
            second = nylotto_bonus_ball is not None and nylotto_bonus_ball in set(ticket)
            label = "Second (5 + Bonus)" if second else "Third (Match 5)"
            return {"tier": label, "wins": True, "prize": None, "note": "Pari-mutuel."}
        if hits == 4:
            return {"tier": "Fourth (Match 4)", "wins": True, "prize": None, "note": "Pari-mutuel."}
        if hits == 3:
            return {"tier": "Fifth (Match 3)", "wins": True, "prize": 1}
        return {"tier": "No prize", "wins": False, "prize": 0}
    if game == "pick10":
        if hits in PICK10_PRIZES:
            return {"tier": f"Match {hits}", "wins": True, "prize": PICK10_PRIZES[hits]}
        return {"tier": "No prize", "wins": False, "prize": 0}
    return {"tier": "n/a", "wins": False, "prize": None, "note": play.get("not_modeled") or "Not modeled."}


def any_prize_probability(game: str) -> float | None:
    """Probability one play wins any prize (exact, from ``computed_odds``)."""
    rows = computed_odds(game)
    if not rows:
        return None
    return float(sum(r["probability"] for r in rows))
