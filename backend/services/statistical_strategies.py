"""Pure statistical lottery number suggestion strategies."""

from collections import Counter
from typing import Any, Dict, List, Union

from prediction.core.types import Draw, GameRules

RulesLike = Union[GameRules, Dict[str, Any]]


def _coerce_rules(rules: RulesLike, game: str = "") -> GameRules:
    """Accept GameRules or the dict returned by `_get_rules` / GAME_CONFIGS."""
    if isinstance(rules, GameRules):
        return rules
    if not isinstance(rules, dict):
        raise TypeError(f"Expected GameRules or dict, got {type(rules)!r}")
    return GameRules(
        game=str(rules.get("game") or game or ""),
        primary_count=int(rules.get("primary_count", 5)),
        primary_min=int(rules.get("primary_min", 1)),
        primary_max=int(rules.get("primary_max", 99)),
        primary_unique=bool(rules.get("primary_unique", True)),
        bonus_count=int(rules.get("bonus_count", 0) or 0),
        bonus_min=int(rules.get("bonus_min", 1)),
        bonus_max=int(rules.get("bonus_max", 99)),
    )


def score_frequency(history: List[Draw], rules: RulesLike, window: int = 100) -> Dict[int, float]:
    """Scores numbers based on how frequently they have appeared in the recent past.

    Args:
        history: List of past draw results.
        rules: The rules for the game.
        window: The number of recent draws to consider.

    Returns:
        A dictionary mapping each number to its frequency score.
    """
    _coerce_rules(rules)
    if not history:
        return {}

    recent_history = history[-window:]
    all_numbers = [num for draw in recent_history for num in draw.primary]

    counts = Counter(all_numbers)
    max_count = max(counts.values()) if counts else 1.0

    scores = {num: count / max_count for num, count in counts.items()}
    return scores


def score_overdue(history: List[Draw], rules: RulesLike) -> Dict[int, float]:
    """Scores numbers based on how long it has been since they were last drawn.

    Args:
        history: List of past draw results.
        rules: The rules for the game.

    Returns:
        A dictionary mapping each number to its overdue score.
    """
    rules = _coerce_rules(rules)
    if not history:
        return {}

    last_seen = {}
    for i, draw in enumerate(history):
        for num in draw.primary:
            last_seen[num] = i

    gaps = {
        num: len(history) - last_seen.get(num, 0)
        for num in range(rules.primary_min, rules.primary_max + 1)
    }
    max_gap = max(gaps.values()) if gaps else 1.0

    scores = {num: gap / max_gap for num, gap in gaps.items()}
    return scores


def score_hot_cold(
    history: List[Draw],
    rules: RulesLike,
    hot_window: int = 50,
    cold_window: int = 200,
    hot_weight: float = 0.7,
    cold_weight: float = 0.3,
) -> Dict[int, float]:
    """Scores numbers based on a combination of 'hotness' (recent frequency)
    and 'coldness' (inverse frequency over a longer period, implying 'due').

    Args:
        history: List of past draw results.
        rules: The rules for the game.
        hot_window: The number of most recent draws to consider for 'hot' numbers.
        cold_window: The number of draws to consider for 'cold' numbers (longer window).
        hot_weight: The weight to give to the 'hot' score component.
        cold_weight: The weight to give to the 'cold' score component.

    Returns:
        A dictionary mapping each number to its combined hot/cold score.
    """
    rules = _coerce_rules(rules)
    if not history:
        return {}

    # Calculate hotness (frequency in recent window)
    hot_freq_scores = score_frequency(history, rules, window=hot_window)

    # Calculate coldness (inverse frequency in a longer window)
    # Numbers that appear less often in the cold_window get a higher 'cold' score.
    cold_freq_counts = Counter(
        num for draw in history[-cold_window:] for num in draw.primary
    )

    # Get all possible numbers in the game
    all_possible_numbers = set(range(rules.primary_min, rules.primary_max + 1))

    # Normalize cold frequency: 1 - (freq / max_freq)
    max_cold_count = max(cold_freq_counts.values()) if cold_freq_counts else 1.0

    cold_scores = {
        num: (1.0 - (cold_freq_counts.get(num, 0) / max_cold_count))
        for num in all_possible_numbers
    }

    # Combine scores
    combined_scores = {
        num: (hot_freq_scores.get(num, 0.0) * hot_weight)
        + (cold_scores.get(num, 0.0) * cold_weight)
        for num in all_possible_numbers
    }

    return combined_scores


def score_hybrid(
    history: List[Draw], rules: RulesLike, window: int = 100, freq_weight: float = 0.6
) -> Dict[int, float]:
    """Combines frequency and overdue scores into a single hybrid score.

    Args:
        history: List of past draw results.
        rules: The rules for the game.
        window: The number of recent draws to consider for frequency scoring.
        freq_weight: The weight to give to the frequency score.

    Returns:
        A dictionary mapping each number to its hybrid score.
    """
    rules = _coerce_rules(rules)
    freq_scores = score_frequency(history, rules, window)
    overdue_scores = score_overdue(history, rules)

    # Normalize overdue scores to be on the same scale as frequency scores
    max_overdue = max(overdue_scores.values()) if overdue_scores else 1.0
    normalized_overdue = {k: v / max_overdue for k, v in overdue_scores.items()}

    hybrid_scores = {}
    all_numbers = set(freq_scores.keys()) | set(normalized_overdue.keys())

    for num in all_numbers:
        freq = freq_scores.get(num, 0.0)
        overdue = normalized_overdue.get(num, 0.0)
        hybrid_scores[num] = (freq * freq_weight) + (overdue * (1.0 - freq_weight))

    return hybrid_scores


def select_top_numbers(scores: Dict[int, float], rules: RulesLike) -> List[int]:
    """Selects the top N numbers based on their scores.

    Args:
        scores: A dictionary mapping each number to its score.
        rules: The rules for the game.

    Returns:
        A list of the top N numbers.
    """
    rules = _coerce_rules(rules)
    if not scores:
        return []

    sorted_numbers = sorted(scores.items(), key=lambda item: item[1], reverse=True)

    top_numbers = [int(num) for num, score in sorted_numbers[: rules.primary_count]]

    # Ensure we have enough numbers
    if len(top_numbers) < rules.primary_count:
        all_possible_numbers = set(range(rules.primary_min, rules.primary_max + 1))
        missing_numbers = list(all_possible_numbers - set(top_numbers))
        top_numbers.extend(missing_numbers[: rules.primary_count - len(top_numbers)])

    return sorted(int(n) for n in top_numbers)
