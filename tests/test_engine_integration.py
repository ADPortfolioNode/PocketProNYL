"""End-to-end engine tests with synthetic history."""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.draw_loader import draws_from_lists
from prediction.engine import LotteryPredictionEngine


def test_engine_predict_take5():
    with tempfile.TemporaryDirectory() as tmp:
        engine = LotteryPredictionEngine(state_dir=tmp)
        history = draws_from_lists(
            [[a, b, c, d, e] for a, b, c, d, e in [
                (1, 2, 3, 4, 5), (6, 7, 8, 9, 10), (11, 12, 13, 14, 15),
                (16, 17, 18, 19, 20), (21, 22, 23, 24, 25),
            ] * 10],
            "take5",
        )
        ticket = engine.predict("take5", history=history)
        assert ticket.game == "take5"
        assert len(ticket.primary) == 5
        assert all(1 <= n <= 39 for n in ticket.primary)


def test_engine_backtest_pick3():
    with tempfile.TemporaryDirectory() as tmp:
        engine = LotteryPredictionEngine(state_dir=tmp)
        history = draws_from_lists(
            [[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(60)],
            "pick3",
        )
        result = engine.backtest("pick3", history=history)
        assert result.get("status") in ("ok", "insufficient_data")
        if result.get("status") == "ok":
            assert "random_baseline" in result
            assert result["lift_vs_random"] < 10


def test_backtest_skips_nn_and_reuses_strategy_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        engine = LotteryPredictionEngine(state_dir=tmp)
        history = draws_from_lists(
            [[i % 10, (i + 1) % 10, (i + 2) % 10] for i in range(70)],
            "pick3",
        )
        nn_calls = {"n": 0}
        collect_calls = {"n": 0}
        original_get_nn = LotteryPredictionEngine._get_nn
        original_collect = LotteryPredictionEngine._collect_outputs

        def counting_get_nn(self, game, config):
            nn_calls["n"] += 1
            return None

        def counting_collect(self, hist, config):
            collect_calls["n"] += 1
            return original_collect(self, hist, config)

        LotteryPredictionEngine._get_nn = counting_get_nn
        LotteryPredictionEngine._collect_outputs = counting_collect
        try:
            result = engine.backtest(
                "pick3",
                history=history,
                verification_rounds=1,
                max_test_draws=6,
            )
            backtest_nn = nn_calls["n"]
            live = engine.predict("pick3", history=history, use_nn=True)
        finally:
            LotteryPredictionEngine._get_nn = original_get_nn
            LotteryPredictionEngine._collect_outputs = original_collect

        assert result["status"] == "ok"
        assert result["evaluated_draws"] == 6
        assert result["split"]["test_draws"] == 6
        # Backtest must not touch the NN; live predict still does.
        assert backtest_nn == 0
        assert nn_calls["n"] >= 1
        assert live.game == "pick3"
        # One collect per scored draw (predict), not a second collect in update_weights.
        assert collect_calls["n"] == result["evaluated_draws"] + 1