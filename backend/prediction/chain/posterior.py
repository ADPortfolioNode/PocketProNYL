"""Beta-Binomial posterior of each number's inclusion rate, carried across runs.

Each number ``u`` has a Beta(alpha_u, beta_u) posterior over "probability u is in
a draw". The prior is centred on the exact chance rate. A run stores the
posterior *as of the end of its train prefix* (before its validation window),
and the next run starts from it, adding only draws newer than that point, so a
carried posterior can never contain a draw the new run is scoring. If a stored
posterior is newer than the new run's train prefix it is ignored for that run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from prediction.core.types import Draw, GameRules

PRIOR_STRENGTH = 20.0
MAX_STRENGTH = 2000.0
SESSION_RANK = {"midday": 0, "evening": 1}


def chance_inclusion_rate(rules: GameRules) -> float:
    size = rules.primary_max - rules.primary_min + 1
    if rules.primary_unique:
        return rules.primary_count / size
    return 1.0 - (1.0 - 1.0 / size) ** rules.primary_count


def draw_key(draw: Draw) -> tuple[str, int, str]:
    session = str((draw.metadata or {}).get("draw_session") or "").lower()
    day = draw.draw_date.isoformat() if draw.draw_date else date.min.isoformat()
    return day, SESSION_RANK.get(session, 9), str(draw.draw_id or "")


@dataclass
class BetaPosterior:
    game: str
    primary_min: int
    alpha: list[float]
    beta: list[float]
    as_of: list | None = None  # draw_key of the newest draw counted
    draws_counted: int = 0
    runs: int = 0
    history: list[dict] = field(default_factory=list)

    def means(self) -> list[float]:
        return [a / (a + b) for a, b in zip(self.alpha, self.beta)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "BetaPosterior | None":
        if not isinstance(data, dict) or not data.get("alpha"):
            return None
        try:
            return cls(
                game=str(data["game"]), primary_min=int(data["primary_min"]),
                alpha=[float(v) for v in data["alpha"]], beta=[float(v) for v in data["beta"]],
                as_of=list(data["as_of"]) if data.get("as_of") else None,
                draws_counted=int(data.get("draws_counted") or 0), runs=int(data.get("runs") or 0),
                history=list(data.get("history") or [])[-20:],
            )
        except (KeyError, TypeError, ValueError):
            return None


def flat_prior(game: str, rules: GameRules, strength: float = PRIOR_STRENGTH) -> BetaPosterior:
    size = rules.primary_max - rules.primary_min + 1
    p0 = chance_inclusion_rate(rules)
    return BetaPosterior(game=game, primary_min=rules.primary_min,
                         alpha=[p0 * strength] * size, beta=[(1 - p0) * strength] * size)


def _is_newer(draw: Draw, as_of: list | None) -> bool:
    if not as_of or draw.draw_date is None:
        return True
    key = draw_key(draw)
    return (key[0], key[1]) > (str(as_of[0]), int(as_of[1]))


def update(posterior: BetaPosterior, draws: list[Draw], rules: GameRules) -> BetaPosterior:
    """Return a new posterior with ``draws`` newer than ``as_of`` counted once."""
    alpha = list(posterior.alpha)
    beta = list(posterior.beta)
    as_of = posterior.as_of
    counted = posterior.draws_counted
    size = len(alpha)
    for draw in draws:
        if not _is_newer(draw, as_of):
            continue
        present = {int(n) - rules.primary_min for n in draw.primary}
        for offset in range(size):
            if offset in present:
                alpha[offset] += 1.0
            else:
                beta[offset] += 1.0
        as_of = list(draw_key(draw))
        counted += 1
    total = alpha[0] + beta[0] if size else 0
    if total > MAX_STRENGTH:
        scale = MAX_STRENGTH / total
        alpha = [a * scale for a in alpha]
        beta = [b * scale for b in beta]
    return BetaPosterior(game=posterior.game, primary_min=posterior.primary_min, alpha=alpha, beta=beta,
                         as_of=as_of, draws_counted=counted, runs=posterior.runs, history=list(posterior.history))


def usable_carried(carried: BetaPosterior | None, history: list[Draw], cutoff_index: int,
                   game: str, rules: GameRules) -> tuple[BetaPosterior, str]:
    """Carried posterior if it predates ``history[cutoff_index]``; else a flat prior."""
    size = rules.primary_max - rules.primary_min + 1
    if carried is None:
        return flat_prior(game, rules), "flat_prior"
    if carried.game != game or len(carried.alpha) != size or carried.primary_min != rules.primary_min:
        return flat_prior(game, rules), "carried_posterior_wrong_game_ignored"
    if carried.as_of and 0 < cutoff_index <= len(history):
        boundary = draw_key(history[cutoff_index - 1])
        if (str(carried.as_of[0]), int(carried.as_of[1])) > (boundary[0], boundary[1]):
            return flat_prior(game, rules), "carried_posterior_newer_than_train_prefix_ignored"
    return carried, "carried"
