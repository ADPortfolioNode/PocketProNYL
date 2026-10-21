"""Chained ensemble: leak-free features, odd-K vote, posteriors, stats, champion gate."""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.chain import champion_store  # noqa: E402
from prediction.chain import evaluate as chain_eval  # noqa: E402
from prediction.chain.chained import ChainedPredictor, ChainVariant  # noqa: E402
from prediction.chain.features import FeatureBuilder  # noqa: E402
from prediction.chain.posterior import BetaPosterior, flat_prior, update, usable_carried  # noqa: E402
from prediction.chain.stacking import bootstrap_vote  # noqa: E402
from prediction.chain.stats import (  # noqa: E402
    benjamini_hochberg,
    chi_square_sf,
    chi_square_uniformity,
    number_frequency_tests,
)
from prediction.chain.subtargets import constrained_ticket, odd_count, odd_count_chance  # noqa: E402
from prediction.config.loader import game_rules_from_config  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from prediction.engine import LotteryPredictionEngine  # noqa: E402

SMALL = [ChainVariant(votes=3, use_subtargets=False, window=30), ChainVariant(votes=3, use_subtargets=True, window=30)]


def make_history(n, seed=1, game="take5", forced=None):
    rules = game_rules_from_config(game)
    rng = random.Random(seed)
    start = date(2025, 1, 1)
    draws = []
    for i in range(n):
        pool = list(range(rules.primary_min, rules.primary_max + 1))
        if rules.primary_unique:
            nums = rng.sample(pool, rules.primary_count)
            if forced is not None and forced not in nums:
                nums[0] = forced
        else:
            nums = [rng.choice(pool) for _ in range(rules.primary_count)]
        draws.append(Draw(primary=nums, date=start + timedelta(days=i), draw_id=f"{game}-{i}"))
    return draws


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PREDICTION_STATE_DIR", str(tmp_path / "prediction"))
    return LotteryPredictionEngine(str(tmp_path / "prediction"))


def test_features_never_see_the_future(engine):
    rules = game_rules_from_config("take5")
    history = make_history(60)
    altered = history[:40] + make_history(20, seed=99)
    a = FeatureBuilder(engine, "take5", history, flat_prior("take5", rules), min_history=10)
    b = FeatureBuilder(engine, "take5", altered, flat_prior("take5", rules), min_history=10)
    np.testing.assert_allclose(a.features_at(40), b.features_at(40))
    assert a.features_at(40).shape == (39, len(a.feature_names))
    assert set(np.flatnonzero(a.labels_at(40)) + 1) == set(history[40].primary)


def test_bootstrap_vote_requires_odd_k_and_is_deterministic(engine):
    rules = game_rules_from_config("take5")
    builder = FeatureBuilder(engine, "take5", make_history(60), flat_prior("take5", rules), min_history=10)
    with pytest.raises(ValueError):
        bootstrap_vote(builder, 50, votes=4, window=30, seed=1)
    v1 = bootstrap_vote(builder, 50, votes=3, window=30, seed=1)
    v2 = bootstrap_vote(builder, 50, votes=3, window=30, seed=1)
    np.testing.assert_allclose(v1["mean_prob"], v2["mean_prob"])
    assert v1["votes"].sum() == 3 * 5 and v1["learners"] == 3


def test_ticket_is_valid_and_follows_subtargets(engine):
    rules = game_rules_from_config("take5")
    predictor = ChainedPredictor(engine, "take5", make_history(70), flat_prior("take5", rules), min_history=10)
    for variant in SMALL:
        out = predictor.ticket_at(60, variant)
        assert len(out["primary"]) == 5 and len(set(out["primary"])) == 5
        assert all(1 <= n <= 39 for n in out["primary"])
    sub = predictor.ticket_at(60, SMALL[1])
    if sub["subtarget_mode"] in ("odd_and_sum", "odd_only"):
        assert odd_count(sub["primary"]) == sub["subtargets"]["odd_count"]


def test_constrained_ticket_relaxes():
    rules = game_rules_from_config("take5")
    ranked = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
    scores = {n: 1.0 / n for n in ranked}
    ticket, mode = constrained_ticket(ranked, scores, rules, {"odd_count": 3, "sum_band": None, "sum_edges": None})
    assert mode == "unconstrained" and ticket == ranked[:5]
    assert sum(odd_count_chance(rules)) == pytest.approx(1.0)


def test_posterior_is_carried_without_leakage():
    rules = game_rules_from_config("take5")
    history = make_history(50)
    post = update(flat_prior("take5", rules), history[:30], rules)
    assert post.draws_counted == 30 and post.as_of[0] == history[29].draw_date.isoformat()
    again = update(post, history[:40], rules)
    assert again.draws_counted == 40  # only the 10 newer draws were added
    restored = BetaPosterior.from_dict(again.to_dict())
    assert restored.means() == pytest.approx(again.means())
    # Newer than the train prefix -> ignored for this run.
    base, status = usable_carried(again, history, 35, "take5", rules)
    assert status == "carried_posterior_newer_than_train_prefix_ignored" and base.draws_counted == 0
    base, status = usable_carried(post, history, 35, "take5", rules)
    assert status == "carried" and base.draws_counted == 30
    _, status = usable_carried(post, history, 35, "pick3", game_rules_from_config("pick3"))
    assert status == "carried_posterior_wrong_game_ignored"


def test_stats_helpers():
    assert chi_square_sf(0.0, 4) == pytest.approx(1.0)
    assert chi_square_sf(9.488, 4) == pytest.approx(0.05, abs=1e-3)
    assert chi_square_sf(30.144, 19) == pytest.approx(0.05, abs=1e-3)
    adjusted, rejected = benjamini_hochberg([0.001, 0.02, 0.03, 0.5], q=0.05)
    assert adjusted == pytest.approx([0.004, 0.04, 0.04, 0.5])
    assert rejected == [True, True, True, False]
    rules = game_rules_from_config("take5")
    fair = make_history(400, seed=3)
    assert chi_square_uniformity(fair, rules)["uniform_at_0_05"] is True
    rigged = make_history(400, seed=3, forced=7)
    assert chi_square_uniformity(rigged, rules)["uniform_at_0_05"] is False
    flagged = number_frequency_tests(rigged, rules)["significant_after_fdr"]
    assert 7 in [row["number"] for row in flagged]


def test_evaluation_keeps_incumbent_on_fair_draws(engine):
    history = make_history(110, seed=5)
    report = chain_eval.evaluate_chain(engine, "take5", history, variants=SMALL, holdout_draws=12,
                                       max_test_draws=15, min_history=20)
    assert report["status"] == "ok"
    assert report["split"]["holdout_draws"] == 12 and report["split"]["validation_draws"] == 15
    assert report["validation"]["decision"]["comparisons"] == len(SMALL)
    assert report["validation"]["decision"]["alpha_adjusted"] == pytest.approx(0.05 / len(SMALL))
    assert report["holdout"]["chained"]["draws"] == 12 and report["holdout"]["incumbent"]["draws"] == 12
    assert report["champion"] == ("chained_ensemble" if report["promoted"] else "incumbent")
    stored = champion_store.load("take5")
    assert stored["champion"] == report["champion"] and stored["posterior"]["draws_counted"] > 0
    assert champion_store.current_champion("pick3") == ("incumbent", None)

    # Incumbent tickets equal engine.predict with the committed weights.
    config = engine._load_config("take5")
    weights = engine._resolve_weights("take5", config)
    direct = engine.predict("take5", history=history[:100], use_nn=False, weights_override=weights).primary
    predictor = ChainedPredictor(engine, "take5", history, flat_prior("take5", game_rules_from_config("take5")))
    from prediction.core.ensemble import build_ticket

    rebuilt = build_ticket(game="take5", outputs=predictor.builder.outputs_at(100), weights=weights,
                           rules=game_rules_from_config("take5")).primary
    assert rebuilt == direct

    second = chain_eval.evaluate_chain(engine, "take5", history, variants=SMALL, holdout_draws=12,
                                       max_test_draws=15, min_history=20)
    assert second["posterior"]["status"] == "carried"


def test_promotion_needs_validation_and_holdout(engine, monkeypatch):
    history = make_history(100, seed=8)
    kwargs = dict(variants=SMALL[:1], holdout_draws=10, max_test_draws=12, min_history=20)
    holdout_start = 90

    def oracle_until(limit):
        def ticket_at(self, j, variant):
            if j < limit:
                return {"primary": list(self.builder.history[j].primary)}  # perfect ticket
            drawn = set(self.builder.history[j].primary)
            return {"primary": [n for n in range(1, 40) if n not in drawn][:5]}  # zero hits
        return ticket_at

    # Wins on validation, loses on the untouched holdout -> incumbent stays.
    monkeypatch.setattr(ChainedPredictor, "ticket_at", oracle_until(holdout_start))
    report = chain_eval.evaluate_chain(engine, "take5", history, **kwargs)
    assert report["validation"]["decision"]["accepted"] is True
    assert report["holdout"]["chained_beats_incumbent"] is False
    assert report["promoted"] is False and report["champion"] == "incumbent"
    assert champion_store.current_champion("take5")[0] == "incumbent"

    # Wins on both -> promoted, and the champion record names the variant.
    monkeypatch.setattr(ChainedPredictor, "ticket_at", oracle_until(10 ** 6))
    report = chain_eval.evaluate_chain(engine, "take5", history, **kwargs)
    assert report["promoted"] is True and report["holdout"]["paired_permutation_p_value"] <= 0.05
    champion, variant = champion_store.current_champion("take5")
    assert champion == "chained_ensemble" and variant["votes"] == 3

    # Dry run never writes.
    champion_store.save("take5", {"champion": "incumbent"})
    chain_eval.evaluate_chain(engine, "take5", history, commit=False, **kwargs)
    assert champion_store.current_champion("take5")[0] == "incumbent"


def test_adapter_serves_chained_ticket_only_when_promoted(engine, monkeypatch):
    from services.prediction_adapter import PredictionAdapter

    history = make_history(80, seed=2)
    adapter = PredictionAdapter(str(Path(engine.weight_store.base_dir)))
    monkeypatch.setattr("services.prediction_adapter.from_chroma", lambda game, limit=100: history)
    out = adapter.predict_next_draw("take5")
    assert out["model_strategy"] == "ensemble" and out["model_champion"] == "incumbent"

    variant = ChainVariant(votes=3, use_subtargets=False, window=30).to_dict()
    champion_store.save("take5", {"champion": "chained_ensemble", "champion_variant": variant})
    out = adapter.predict_next_draw("take5")
    assert out["model_strategy"] == "chained_ensemble" and len(out["predicted_main_numbers"]) == 5
    # Another game is untouched by take5's champion.
    monkeypatch.setattr("services.prediction_adapter.from_chroma",
                        lambda game, limit=100: make_history(80, seed=2, game="pick3"))
    assert adapter.predict_next_draw("pick3")["model_champion"] == "incumbent"
