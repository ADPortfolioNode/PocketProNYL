"""Anti-popular-number filter for pari-mutuel games.

In a pari-mutuel tier the prize pool is split between everyone who wins it,
so a ticket many other people also play (birthdays, sequences, patterns)
wins less when it wins. Avoiding those choices can only reduce the chance of
SHARING a prize. It does not change the chance of winning: every combination
is equally likely to be drawn.

Heuristics follow published studies of player number choice (players
over-select 1-31 and especially 1-12, arithmetic sequences / consecutive runs,
and numbers sharing a last digit), e.g. Henze & Riedwyl, "How to Win More:
Strategies for Increasing a Lottery Win" (1998), and Cook & Clotfelter,
"The Peculiar Scale Economies of Lotto", American Economic Review 83(3), 1993.
"""

from __future__ import annotations

from collections import Counter

from prediction.core.types import GameRules
from services.game_odds import PARI_MUTUEL_GAMES, PICKS_CANNOT_CHANGE_ODDS


def _longest_run(numbers: list[int]) -> int:
    values = sorted(set(numbers))
    best = run = 1 if values else 0
    for a, b in zip(values, values[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


def _is_arithmetic(numbers: list[int]) -> bool:
    values = sorted(set(numbers))
    if len(values) < 3:
        return False
    step = values[1] - values[0]
    return all(b - a == step for a, b in zip(values, values[1:]))


def popularity_score(numbers: list[int], rules: GameRules) -> dict:
    """0 (unremarkable) .. 100 (very commonly played) with the reasons."""
    k = max(len(numbers), 1)
    flags: list[str] = []
    score = 0.0
    birthday = sum(1 for n in numbers if n <= 31)
    expected_birthday = k * min(31, rules.primary_max) / max(rules.primary_max - rules.primary_min + 1, 1)
    if rules.primary_max > 31 and birthday == k:
        score += 35
        flags.append("all numbers are 31 or lower (birthday range)")
    elif rules.primary_max > 31 and birthday > expected_birthday + 1:
        score += 15
        flags.append("leans on the 1-31 birthday range")
    if sum(1 for n in numbers if n <= 12) >= max(3, k - 1):
        score += 10
        flags.append("mostly 1-12 (month numbers)")
    run = _longest_run(numbers)
    if run >= 3:
        score += 10 * (run - 2)
        flags.append(f"{run} consecutive numbers")
    if _is_arithmetic(numbers):
        score += 25
        flags.append("evenly spaced sequence")
    last_digits = Counter(n % 10 for n in numbers)
    if last_digits and last_digits.most_common(1)[0][1] >= 3:
        score += 10
        flags.append("three or more share a last digit")
    if all(n % 5 == 0 for n in numbers) or all(n % 7 == 0 for n in numbers):
        score += 15
        flags.append("all multiples of 5 or 7")
    return {"score": int(min(100, round(score))), "flags": flags}


def anti_popular_adjust(game: str, numbers: list[int], rules: GameRules,
                        ranking: list[int] | None = None, threshold: int = 20) -> dict:
    """Swap the most 'popular' numbers for the best-ranked alternatives above 31.

    ``ranking`` is the model's ordered preference over the whole universe (best
    first); without it, alternatives are taken in descending order. Fixed-prize
    games are returned unchanged: there is no split to avoid.
    """
    before = popularity_score(numbers, rules)
    base = {"game": game, "original": list(numbers), "original_popularity": before,
            "disclaimer": PICKS_CANNOT_CHANGE_ODDS}
    if game not in PARI_MUTUEL_GAMES:
        return {**base, "applied": False, "numbers": list(numbers), "popularity": before,
                "note": "Fixed prizes in this game: winners never split, so popularity does not matter."}
    if not rules.primary_unique or before["score"] < threshold:
        return {**base, "applied": False, "numbers": list(numbers), "popularity": before,
                "note": "Already unremarkable; left as is."}
    universe = list(range(rules.primary_min, rules.primary_max + 1))
    order = [n for n in (ranking or sorted(universe, reverse=True)) if rules.primary_min <= n <= rules.primary_max]
    current = list(numbers)
    for _ in range(len(current)):
        score = popularity_score(current, rules)["score"]
        if score < threshold:
            break
        # Replace the lowest-preference number in the birthday range (or the run).
        rank = {n: i for i, n in enumerate(order)}
        candidates = sorted([n for n in current if n <= 31] or current, key=lambda n: -rank.get(n, len(order)))
        out = candidates[0]
        replacement = next((n for n in order if n > 31 and n not in current), None)
        if replacement is None:
            break
        trial = [replacement if n == out else n for n in current]
        if popularity_score(trial, rules)["score"] >= score:
            break
        current = trial
    after = popularity_score(current, rules)
    return {**base, "applied": current != list(numbers), "numbers": sorted(current), "popularity": after,
            "note": "Lowers the chance of sharing a pari-mutuel prize; the chance of winning is unchanged."}
