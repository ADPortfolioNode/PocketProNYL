"""Expected value per dollar for each game, from the published prize tables.

Sources are cited in ``services/game_odds.py``. EV here is before taxes and
ignores jackpot splitting (an upper bound for jackpot games); pari-mutuel games
use their published prize-pool share of sales.
"""

from __future__ import annotations

from services.game_odds import (
    GAME_PLAY,
    PICKS_CANNOT_CHANGE_ODDS,
    computed_odds,
    hypergeom_pmf,
)

JACKPOT_ODDS = {"powerball": 1 / 292_201_338, "megamillions": 1 / 290_472_336}


def _fixed_tier_ev(game: str) -> float:
    return sum(r["probability"] * r["prize"] for r in computed_odds(game) if r.get("prize") is not None)


def game_ev(game: str, jackpot_cash: float | None = None) -> dict:
    play = GAME_PLAY.get(game) or {}
    price = float(play.get("price") or 1.0)
    row = {"game": game, "price_per_play": price, "pari_mutuel": play.get("pari_mutuel"),
           "source": play.get("source")}
    if play.get("retired"):
        return {**row, "ranked": False, "note": play["retired"]}
    if play.get("not_modeled"):
        return {**row, "ranked": False, "note": play["not_modeled"]}
    note = None
    if game == "take5":
        # [T5] 50% of sales goes to prizes; match 2 pays a free play worth the EV itself.
        p_free = hypergeom_pmf(39, 5, 5, 2)
        ev_per_dollar = 0.50 / (1 - p_free)
        note = "Pari-mutuel: 50% of sales is paid out; match 2 returns a free play."
    elif game == "nylotto":
        # [LOT] up to 40% of sales is prize money; 75% of that (30% of sales) is the jackpot.
        if jackpot_cash:
            ev_per_dollar = 0.10 + 2 * float(jackpot_cash) / 45_057_474
            note = "Lower tiers ~10% of sales plus the jackpot you entered (2 panels per $1)."
        else:
            ev_per_dollar = 0.40
            note = "Long-run average: up to 40% of sales is paid as prizes."
    elif game in ("powerball", "megamillions"):
        fixed = _fixed_tier_ev(game)
        jackpot = float(jackpot_cash or 0.0) * JACKPOT_ODDS[game]
        ev_per_dollar = (fixed + jackpot) / price
        row["fixed_tiers_ev_per_play"] = round(fixed, 4)
        row["break_even_jackpot_cash"] = round((price - fixed) / JACKPOT_ODDS[game], -6)
        note = ("Jackpot included at the cash value you entered; before taxes and ignoring splits."
                if jackpot_cash else "Fixed tiers only; enter a jackpot cash value to include it.")
        if game == "megamillions":
            note += " Non-jackpot prizes use the expected built-in multiplier."
    else:
        ev_per_dollar = _fixed_tier_ev(game) / price
        if game == "millionaireforlife":
            note = "Top two tiers at their published cash values; top prize is pari-mutuel."
        elif game in ("pick3", "numbers", "win4"):
            note = "$1 straight play (box plays have the same EV per dollar)."
    return {**row, "ranked": True, "ev_per_dollar": round(ev_per_dollar, 4),
            "expected_loss_per_dollar": round(1 - ev_per_dollar, 4), "jackpot_cash_used": jackpot_cash,
            "note": note}


def ev_ranking(jackpots: dict[str, float] | None = None) -> dict:
    jackpots = jackpots or {}
    rows = [game_ev(game, jackpots.get(game)) for game in GAME_PLAY]
    ranked = sorted([r for r in rows if r.get("ranked")], key=lambda r: -r["ev_per_dollar"])
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    return {
        "status": "ok",
        "ranking": ranked,
        "not_ranked": [r for r in rows if not r.get("ranked")],
        "disclaimer": PICKS_CANNOT_CHANGE_ODDS,
        "note": "Every game returns less than it costs on average (EV per $1 below 1.00) unless a jackpot is "
                "very large, and even then taxes and shared jackpots usually erase it.",
    }
