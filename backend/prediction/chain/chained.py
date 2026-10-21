"""The chained ensemble: posterior -> stacked meta-learner -> bootstrap vote -> sub-targets.

1. Beta-Binomial posterior of each number (carried across runs, leak-free).
2. Features: every existing strategy/agent score + posterior + gap (``features.py``).
3. Stacking meta-learner (logistic regression) fit only on earlier draws.
4. Odd-K bootstrap majority vote over meta-learners (``stacking.py``).
5. Sub-target chain: odd/even count and sum band constrain the final ticket.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from prediction.chain.features import FeatureBuilder
from prediction.chain.posterior import BetaPosterior, usable_carried
from prediction.chain.stacking import DEFAULT_C, bootstrap_vote, majority_ranking
from prediction.chain.subtargets import constrained_ticket, predict_classes
from prediction.core.types import Draw


@dataclass(frozen=True)
class ChainVariant:
    votes: int = 5
    use_subtargets: bool = True
    window: int = 120
    C: float = DEFAULT_C

    @property
    def name(self) -> str:
        return f"chain_k{self.votes}_{'sub' if self.use_subtargets else 'nosub'}_w{self.window}"

    def to_dict(self) -> dict:
        return {**asdict(self), "name": self.name}

    @classmethod
    def from_dict(cls, data: dict) -> "ChainVariant":
        return cls(votes=int(data.get("votes", 5)), use_subtargets=bool(data.get("use_subtargets", True)),
                   window=int(data.get("window", 120)), C=float(data.get("C", DEFAULT_C)))


DEFAULT_VARIANTS = (
    ChainVariant(votes=3, use_subtargets=False),
    ChainVariant(votes=5, use_subtargets=False),
    ChainVariant(votes=3, use_subtargets=True),
    ChainVariant(votes=5, use_subtargets=True),
)


class ChainedPredictor:
    """Builds chained-ensemble tickets for any index of one game's history."""

    def __init__(self, engine, game: str, history: list[Draw], base_posterior: BetaPosterior,
                 seed: int = 13, min_history: int | None = None):
        kwargs = {} if min_history is None else {"min_history": min_history}
        self.builder = FeatureBuilder(engine, game, history, base_posterior, **kwargs)
        self.rules = self.builder.rules
        self.seed = int(seed)
        self._votes: dict[tuple, dict] = {}

    def vote_at(self, j: int, variant: ChainVariant) -> dict:
        key = (j, variant.votes, variant.window, variant.C)
        if key not in self._votes:
            self._votes[key] = bootstrap_vote(self.builder, j, votes=variant.votes, window=variant.window,
                                              seed=self.seed, C=variant.C)
        return self._votes[key]

    def ticket_at(self, j: int, variant: ChainVariant) -> dict:
        vote = self.vote_at(j, variant)
        lo = self.rules.primary_min
        ranked = [lo + u for u in majority_ranking(vote)]
        k = self.rules.primary_count
        majority = int((variant.votes + 1) // 2)
        info = {
            "learners": vote.get("learners"),
            "training_draws": vote.get("positions"),
            "majority_numbers": [lo + u for u in range(len(vote["votes"])) if vote["votes"][u] >= majority],
        }
        if variant.use_subtargets:
            classes = predict_classes(self.builder.history[:j], self.rules, window=variant.window)
            scores = {lo + u: float(p) for u, p in enumerate(vote["mean_prob"])}
            ticket, mode = constrained_ticket(ranked, scores, self.rules, classes)
            info.update({"subtargets": {k_: v for k_, v in classes.items() if k_ != "sum_edges"},
                         "subtarget_mode": mode})
        else:
            ticket = ranked[:k]
        return {"primary": [int(n) for n in ticket[:k]], **info}


def live_chained_ticket(engine, game: str, history: list[Draw], variant: ChainVariant,
                        carried: BetaPosterior | None) -> dict:
    """Ticket for the next (unseen) draw using all of ``history``."""
    from prediction.config.loader import game_rules_from_config

    rules = game_rules_from_config(game)
    base, _ = usable_carried(carried, history, len(history), game, rules)
    predictor = ChainedPredictor(engine, game, history, base)
    return predictor.ticket_at(len(history), variant)
