"""Graded reweighting gates (services/graded_reweight.py).

No network, no backend: graded entries are plain dicts, the weight store
is a temp dir. Proves the hold/promote gates and the capped blend.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from prediction.core.types import WeightState  # noqa: E402
from prediction.state.weight_store import WeightStore  # noqa: E402
from services.graded_reweight import (  # noqa: E402
    _binomial_upper_tail,
    apply_reweight,
    dedupe_graded,
    propose_reweight,
    strategy_credit,
)

CURRENT = {"frequency": 0.5, "hot_cold": 0.5}


def _graded(n, frequency_hits=True, model="ensemble"):
    out = []
    day = date(2026, 10, 1)
    for i in range(n):
        actual = [1, 2, 30, 31, 32] if frequency_hits else [9, 10, 30, 31, 32]
        out.append({
            "status": "graded",
            "actual_draw_date": (day + timedelta(days=i)).isoformat(),
            "actual_draw_session": None,
            "numbers": [1, 2, 3, 4, 5],
            "actual_numbers": actual,
            "model": model,
            "strategy_contributions": {"frequency": [1, 2], "hot_cold": [9, 10]},
        })
    return out


def test_binomial_tail_edges():
    assert _binomial_upper_tail(20, 0.1282, 0) == 1.0
    assert _binomial_upper_tail(20, 0.1282, 21) == 0.0


def test_dedupe_counts_repeated_serving_once():
    items = _graded(3)
    items.append(dict(items[0]))
    assert len(dedupe_graded(items)) == 3


def test_hold_below_min_graded():
    proposal = propose_reweight("take5", _graded(3), CURRENT)
    assert proposal["verdict"] == "hold"
    assert proposal["candidate_weights"] is None
    assert any("30" in r for r in proposal["reasons"])


def test_strategy_credit_math():
    credit = strategy_credit(_graded(30), "take5")
    assert credit["frequency"]["hits"] == 60
    assert credit["frequency"]["numbers"] == 60
    assert credit["frequency"]["lift"] > 5.0
    assert credit["frequency"]["p_value"] < 0.001
    assert credit["hot_cold"]["hits"] == 0
    assert credit["hot_cold"]["lift"] == 0.0


def test_promote_moves_toward_winning_strategy_capped():
    proposal = propose_reweight("take5", _graded(30), CURRENT)
    assert proposal["verdict"] == "promote"
    assert proposal["alpha"] == 0.125  # 0.25 * 30 / (30 + 30)
    candidate = proposal["candidate_weights"]
    assert candidate["frequency"] > CURRENT["frequency"]
    assert candidate["hot_cold"] < CURRENT["hot_cold"]
    assert candidate["hot_cold"] >= 0.01  # floor: no strategy ever dies
    assert abs(sum(candidate.values()) - 1.0) < 1e-5


def test_chained_tickets_earn_no_strategy_credit():
    proposal = propose_reweight("take5", _graded(30, model="chained_ensemble"), CURRENT)
    assert proposal["verdict"] == "hold"
    assert proposal["strategies"] == {}


def test_apply_promotes_and_records_previous_weights(tmp_path):
    store = WeightStore(base_dir=str(tmp_path))
    store.save(WeightState(game="take5", weights=dict(CURRENT)))
    result = apply_reweight("take5", _graded(30), store=store)
    assert result["applied"] is True
    state = store.load("take5")
    assert state.weights == result["candidate_weights"]
    last = state.history[-1]
    assert last["source"] == "graded_ledger"
    assert last["previous_weights"] == CURRENT


def test_apply_holds_and_leaves_weights_untouched(tmp_path):
    store = WeightStore(base_dir=str(tmp_path))
    store.save(WeightState(game="take5", weights=dict(CURRENT)))
    result = apply_reweight("take5", _graded(3), store=store)
    assert result["applied"] is False
    assert store.load("take5").weights == CURRENT
