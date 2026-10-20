"""Anti-overfitting guarantees for tuning, training, and reported accuracy.

(a) On uniformly random draws the tuner must not report significant lift and
    challengers must rarely be accepted.
(b) Holdout draws are never passed to selection (round retries, champion /
    challenger, trial choice, weight learning).
(c) Existing API responses keep every old key.
"""

import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import prediction.engine as engine_module
from prediction.config.loader import game_rules_from_config
from prediction.core.draw_loader import draws_from_lists
from prediction.core.types import PredictionTicket, WeightState
from prediction.engine import LotteryPredictionEngine
from prediction.metrics.evaluator import (
    HONEST_METRIC_KEYS,
    champion_challenger_decision,
    paired_permutation_p_value,
    random_hits_p_value,
    resolve_holdout_size,
    walk_forward_backtest,
)
from prediction.state.weight_store import WeightStore
from services.game_tuner import GameTuner
from services.tuning_assistant import TuningAssistant, result_score

# Keys the backtest returned on main before the anti-overfit change.
LEGACY_BACKTEST_KEYS = {
    "status", "split", "evaluated_draws", "mean_partial_hits", "accuracy", "accuracy_percent",
    "held_accuracy_percent", "this_run_accuracy_percent", "next_run_target_percent",
    "rolling_accuracy", "rolling_accuracy_percent", "highest_rolling_accuracy_percent",
    "rolling_window", "overall_accuracy", "overall_accuracy_percent", "target_accuracy",
    "target_accuracy_percent", "target_reached", "verification_rounds", "max_verification_rounds",
    "min_verification_rounds", "improvement_patience", "stopped_reason", "suggestion_history",
    "highest_suggestion", "highest_accuracy_percent", "best_round_accuracy_percent",
    "round_metrics", "regression_count", "promotion_count", "exact_match_rate",
    "partial_hit_rate_at_1", "partial_hit_rate_at_2", "sum_mae", "random_baseline",
    "lift_vs_random", "note",
}
LEGACY_SPLIT_KEYS = {"training_draws", "test_draws", "training_fraction", "test_fraction", "max_test_draws"}
LEGACY_TUNE_RESULT_KEYS = {
    "status", "game", "training_draws", "test_draws", "evaluated_draws", "accuracy", "accuracy_percent",
    "rolling_accuracy_percent", "highest_rolling_accuracy_percent", "highest_accuracy_percent",
    "best_round_accuracy_percent", "target_reached", "verification_rounds", "stopped_reason",
    "regression_count", "promotion_count", "delta_percent", "suggestion_history_count",
    "suggestion_history", "training_seconds", "learning_rate", "rolling_window", "assistant_trial",
    "message",
}
LEGACY_TUNING_METRIC_KEYS = {
    "accuracy_percent", "held_accuracy_percent", "this_run_accuracy_percent", "next_run_target_percent",
    "highest_accuracy_percent", "best_round_accuracy_percent", "rolling_accuracy_percent",
    "highest_rolling_accuracy_percent", "previous_run_accuracy_percent", "delta_percent",
    "target_accuracy_percent", "random_baseline", "lift_vs_random", "regression_count",
    "promotion_count", "verification_round", "evaluated_draws", "stopped_reason", "target_reached",
    "suggestion_history_count", "suggestion_history", "round_metrics", "run_history", "weights",
    "best_weights", "live",
}
NEW_FIELDS = {
    "holdout_accuracy_percent", "validation_accuracy_percent", "best_round_accuracy_percent",
    "overfit_gap_percent", "holdout_lift_vs_random", "holdout_p_value", "trials_tested",
    "overfit_warning",
}
SMALL_TRIALS = (
    {"learning_rate": 0.05, "rolling_window": 40, "rounds": 2, "patience": 1, "label": "baseline"},
    {"learning_rate": 0.10, "rolling_window": 28, "rounds": 2, "patience": 1, "label": "aggressive"},
)


def _random_history(game: str, n: int, seed: int):
    rules = game_rules_from_config(game)
    rng = random.Random(seed)
    pool = list(range(rules.primary_min, rules.primary_max + 1))
    rows = [sorted(rng.sample(pool, rules.primary_count)) for _ in range(n)]
    return draws_from_lists(rows, game)


# ---------------------------------------------------------------- helpers ---

def test_resolve_holdout_size_auto_and_disabled():
    assert resolve_holdout_size(400, min_draws=50) == 60          # capped at 60
    assert resolve_holdout_size(200, min_draws=50) == 30          # ~15%
    assert resolve_holdout_size(200, min_draws=50, holdout_draws=0) == 0
    assert resolve_holdout_size(60, min_draws=50) == 0            # too little room


def test_random_hits_p_value_is_calibrated_for_random_tickets():
    rules = game_rules_from_config("take5")
    draws = _random_history("take5", 40, seed=3)
    rng = random.Random(5)
    pool = list(range(1, 40))
    p_values = []
    for _ in range(40):
        total = sum(len(set(rng.sample(pool, 5)) & set(d.primary)) for d in draws)
        p_values.append(random_hits_p_value(total, draws, rules, replicates=500))
    # Under the null, p < 0.05 should be rare (~5%).
    assert sum(1 for p in p_values if p < 0.05) <= 6
    # A perfect predictor is wildly significant.
    perfect = sum(len(d.primary) for d in draws)
    assert random_hits_p_value(perfect, draws, rules, replicates=500) < 0.01


def test_champion_challenger_requires_beating_noise_with_bonferroni():
    rng = random.Random(1)
    champion = [rng.randint(0, 2) for _ in range(80)]
    noisy = [max(0, h + rng.choice([-1, 0, 0, 1])) for h in champion]
    assert champion_challenger_decision(noisy, champion, comparisons=10)["accepted"] is False
    assert champion_challenger_decision(champion, champion)["accepted"] is False
    strong = [h + 1 for h in champion]
    decision = champion_challenger_decision(strong, champion, comparisons=10)
    assert decision["accepted"] is True
    assert decision["alpha_adjusted"] == 0.005
    assert paired_permutation_p_value(strong, champion) < 0.005


# ------------------------------------------- (a) no lift on random draws ---

def test_retries_on_random_data_inflate_validation_but_holdout_stays_honest():
    """Max-of-retries is optimistic; the headline (holdout) shows no lift."""
    rules = game_rules_from_config("take5")
    lifts, p_values, gaps = [], [], []
    for seed in range(20):
        history = _random_history("take5", 260, seed=100 + seed)
        state = {"round_rng": random.Random(seed)}

        def predict_fn(train_hist):
            return PredictionTicket(game="take5", primary=state["round_rng"].sample(range(1, 40), 5))

        result = walk_forward_backtest(
            history, rules, predict_fn, min_draws=50, target_accuracy=0.99,
            verification_rounds=6, min_verification_rounds=6, improvement_patience=6,
            holdout_draws=None, holdout_p_value_replicates=600,
        )
        assert result["accuracy_source"] == "holdout"
        assert result["accuracy_percent"] == result["holdout_accuracy_percent"]
        assert result["best_round_accuracy_percent"] >= result["validation_accuracy_percent"] - 1e-9
        lifts.append(result["holdout_lift_vs_random"])
        p_values.append(result["holdout_p_value"])
        gaps.append(result["overfit_gap_percent"])

    assert sum(1 for p in p_values if p >= 0.05) >= 17
    assert abs(sum(lifts) / len(lifts) - 1.0) < 0.12
    # The legacy number (validation floor = max over 6 retries) is inflated.
    assert sum(gaps) / len(gaps) > 0.5


def test_tuner_on_random_draws_reports_no_significant_lift(monkeypatch):
    seeds = range(6)
    p_values, lifts, accepted, warnings = [], [], 0, 0
    for seed in seeds:
        history = _random_history("take5", 200, seed=seed)
        monkeypatch.setattr(engine_module, "from_chroma", lambda game, limit=500, h=history: h[-limit:])
        with tempfile.TemporaryDirectory() as tmp:
            tuner = GameTuner(state_dir=tmp)
            result = tuner.optimize_game("take5", trials=SMALL_TRIALS)
            assert result["accuracy_source"] == "holdout"
            p_values.append(result["holdout_p_value"])
            lifts.append(result["holdout_lift_vs_random"])
            warnings += 1 if result["overfit_warning"] else 0
            assistant = result["assistant"]
            assert assistant["significance_mode"] == "champion_challenger"
            accepted += sum(1 for trial in assistant["trials"] if trial["kept"])
            if not any(trial["kept"] for trial in assistant["trials"]):
                # Nothing passed: the stored weights must be untouched (still initial).
                state = WeightStore(tmp).load("take5")
                assert not state.best_weights
                assert not state.history
    assert sum(1 for p in p_values if p >= 0.05) >= 5
    assert all(0.4 < lift < 1.7 for lift in lifts)
    assert abs(sum(lifts) / len(lifts) - 1.0) < 0.3
    # Challengers rarely accepted: at most one acceptance over 12 trials.
    assert accepted <= 1
    assert warnings >= 5


# ------------------------------- (b) holdout never reaches selection ---

def test_walk_forward_never_passes_holdout_to_selection():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i * 3) % 10, (i * 7) % 10] for i in range(200)], "pick3")
    holdout_ids = {id(d) for d in history[-30:]}
    phase = {"name": "selection"}
    selection_predict_lengths, holdout_predict_lengths, updates = [], [], []

    def predict_fn(train_hist):
        if phase["name"] == "selection":
            assert not any(id(d) in holdout_ids for d in train_hist)
            selection_predict_lengths.append(len(train_hist))
        else:
            holdout_predict_lengths.append(len(train_hist))
        return PredictionTicket(game="pick3", primary=list(train_hist[-1].primary))

    def update_fn(actual, train_hist, rolling):
        assert phase["name"] == "selection", "weights must be frozen on the holdout"
        assert id(actual) not in holdout_ids
        assert not any(id(d) in holdout_ids for d in train_hist)
        updates.append(actual)
        return {}

    def before_holdout(summary):
        assert summary["holdout_start"] == 170
        assert all(index < 170 for index in summary["validation_draw_indices"])
        phase["name"] = "holdout"
        return {}

    result = walk_forward_backtest(
        history, rules, predict_fn, min_draws=50, verification_rounds=3, min_verification_rounds=3,
        improvement_patience=3, update_fn=update_fn, holdout_draws=30, before_holdout_fn=before_holdout,
    )
    assert result["split"]["holdout_start"] == 170
    assert max(selection_predict_lengths) < 170
    # Holdout scored exactly once per draw, walk-forward.
    assert holdout_predict_lengths == list(range(170, 200))
    assert result["holdout_evaluated_draws"] == 30
    assert updates


def test_engine_selection_and_learning_never_see_holdout(monkeypatch):
    history = _random_history("take5", 220, seed=9)
    with tempfile.TemporaryDirectory() as tmp:
        engine = LotteryPredictionEngine(state_dir=tmp)
        calls = []
        original_predict = LotteryPredictionEngine.predict
        original_update = LotteryPredictionEngine.update_weights

        def spy_predict(self, game, history=None, **kwargs):
            calls.append(("predict", len(history), bool(kwargs.get("weights_override"))))
            return original_predict(self, game, history=history, **kwargs)

        def spy_update(self, game, actual, history=None, **kwargs):
            calls.append(("update", next(i for i, d in enumerate(history_ref) if d is actual), False))
            return original_update(self, game, actual, history=history, **kwargs)

        history_ref = history
        monkeypatch.setattr(LotteryPredictionEngine, "predict", spy_predict)
        monkeypatch.setattr(LotteryPredictionEngine, "update_weights", spy_update)
        result = engine.backtest(
            "take5", history=history, verification_rounds=3, min_verification_rounds=2,
            improvement_patience=2,
        )
        holdout_start = result["split"]["holdout_start"]
        assert holdout_start == 220 - 33
        holdout_calls = [c for c in calls if c[0] == "predict" and c[1] >= holdout_start]
        # Holdout scored once per draw; nothing else ever reached those draws.
        assert len(holdout_calls) == result["holdout_evaluated_draws"] == 33
        first_holdout = calls.index(holdout_calls[0])
        assert all(c[1] < holdout_start for c in calls[:first_holdout])
        assert all(c[0] == "predict" and not c[2] for c in calls[first_holdout:])
        assert all(c[1] < holdout_start for c in calls if c[0] == "update")
        # The champion re-score used only validation draws.
        champion_calls = [c for c in calls if c[2]]
        assert champion_calls and all(c[1] < holdout_start for c in champion_calls)


def test_assistant_selects_on_validation_not_holdout():
    assert result_score({
        "validation_accuracy_percent": 10.0,
        "held_accuracy_percent": 50.0,
        "accuracy_percent": 50.0,
        "holdout_accuracy_percent": 50.0,
    }) == 10.0
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        store.save(WeightState(game="take5", weights={"frequency": 1.0}, best_weights={"frequency": 1.0}))
        plan = {
            # Great holdout but no significant validation gain -> must be rejected.
            "baseline": {"validation": 12.0, "holdout": 40.0, "accepted": False},
            # Significant validation gain, worse holdout -> chosen on validation alone.
            "aggressive": {"validation": 15.0, "holdout": 11.0, "accepted": True},
        }
        seen = []

        def run_trial(settings):
            seen.append(settings)
            entry = plan[settings["label"]]
            return {
                "status": "ok",
                "game": "take5",
                "validation_accuracy_percent": entry["validation"],
                "holdout_accuracy_percent": entry["holdout"],
                "accuracy_percent": entry["holdout"],
                "held_accuracy_percent": entry["holdout"],
                "trials_tested": 2,
                "champion_challenger": {"accepted": entry["accepted"], "p_value": 0.5},
            }

        result = TuningAssistant(store).optimize_game("take5", run_trial=run_trial, trials=SMALL_TRIALS)
        assistant = result["assistant"]
        assert [t["kept"] for t in assistant["trials"]] == [False, True]
        assert assistant["best_settings"]["label"] == "aggressive"
        assert assistant["best_score"] == 15.0
        assert result["holdout_accuracy_percent"] == 11.0
        assert result["trials_tested"] == 4
        assert all(s["bonferroni_comparisons"] == 4 for s in seen)


def test_weights_restored_when_challenger_not_significant():
    history = _random_history("take5", 200, seed=21)
    with tempfile.TemporaryDirectory() as tmp:
        store = WeightStore(tmp)
        start = WeightState(
            game="take5",
            weights={"frequency": 0.5, "delta": 0.5},
            best_weights={"frequency": 0.5, "delta": 0.5},
            best_validation_accuracy=0.11,
        )
        store.save(start)
        before = store.snapshot("take5")
        result = LotteryPredictionEngine(state_dir=tmp).backtest("take5", history=history, verification_rounds=2)
        decision = result["champion_challenger"]
        if not decision["accepted"]:
            after = store.snapshot("take5")
            assert after["weights"] == before["weights"]
            assert after["best_weights"] == before["best_weights"]
            assert after["best_validation_accuracy"] == before["best_validation_accuracy"]
            assert result["weights_committed"] is False


# --------------------------------------------- (c) old keys still there ---

def test_backtest_keeps_legacy_keys_and_adds_honest_fields():
    history = _random_history("take5", 200, seed=4)
    with tempfile.TemporaryDirectory() as tmp:
        result = LotteryPredictionEngine(state_dir=tmp).backtest("take5", history=history)
    assert LEGACY_BACKTEST_KEYS <= set(result)
    assert LEGACY_SPLIT_KEYS <= set(result["split"])
    assert NEW_FIELDS <= set(result)
    assert result["best_round_accuracy_is_optimistic"] is True
    assert isinstance(result["overfit_warning"], bool)
    assert result["overfit_gap_percent"] == round(
        result["validation_accuracy_percent"] - result["holdout_accuracy_percent"], 2
    )


def test_legacy_two_way_split_is_flagged_not_silent():
    rules = game_rules_from_config("pick3")
    history = draws_from_lists([[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(80)], "pick3")
    result = walk_forward_backtest(
        history, rules, lambda h: PredictionTicket(game="pick3", primary=h[-1].primary), min_draws=30,
    )
    assert LEGACY_BACKTEST_KEYS <= set(result)
    assert result["accuracy_source"] == "validation"
    assert result["holdout_accuracy_percent"] is None
    assert result["overfit_warning"] is True
    assert "no_holdout" in result["overfit_warning_reasons"]


def test_tuning_status_api_keeps_old_keys(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import services.game_tuner as game_tuner_module
    from routes import chat as chat_routes

    history = _random_history("take5", 200, seed=5)
    monkeypatch.setattr(engine_module, "from_chroma", lambda game, limit=500: history[-limit:])
    with tempfile.TemporaryDirectory() as tmp:
        tuner = GameTuner(state_dir=tmp)
        tune_result = tuner.tune_game("take5", settings=dict(SMALL_TRIALS[0]))
        assert LEGACY_TUNE_RESULT_KEYS <= set(tune_result)
        assert NEW_FIELDS <= set(tune_result)
        monkeypatch.setattr(game_tuner_module, "game_tuner", tuner)
        app = FastAPI()
        app.include_router(chat_routes.router)
        response = TestClient(app).get("/api/tuning_status")
    assert response.status_code == 200
    body = response.json()
    metrics = body["metrics_by_game"]["take5"]
    assert LEGACY_TUNING_METRIC_KEYS <= set(metrics)
    assert NEW_FIELDS <= set(metrics)
    assert set(HONEST_METRIC_KEYS) <= set(metrics)
    assert body["latest_result"]["accuracy_percent"] == metrics["holdout_accuracy_percent"]


def test_post_train_weight_optimization_keeps_old_keys(monkeypatch):
    import services.game_tuner as game_tuner_module
    from routes import training as training_routes

    history = _random_history("take5", 200, seed=6)
    monkeypatch.setattr(engine_module, "from_chroma", lambda game, limit=500: history[-limit:])
    monkeypatch.setattr(training_routes, "set_job", lambda *args, **kwargs: None)
    with tempfile.TemporaryDirectory() as tmp:
        class TmpTuner(GameTuner):
            def __init__(self):
                super().__init__(state_dir=tmp)

        monkeypatch.setattr(game_tuner_module, "GameTuner", TmpTuner)
        monkeypatch.setattr("services.tuning_assistant.POST_TRAIN_TRIALS", SMALL_TRIALS)
        out = training_routes._post_train_optimize("take5", {"status": "success", "accuracy": 0.1})
    opt = out["weight_optimization"]
    legacy = {"status", "held_accuracy_percent", "best_settings", "best_score", "previous_score",
              "trials", "kept_existing", "message"}
    assert legacy <= set(opt)
    assert NEW_FIELDS <= set(opt)
    assert "Holdout" in out["message"]
