"""Exact per-strategy p-values (services/graded_reweight.py).

The old binomial tail treated every picked number as an independent
coin flip. Real tickets draw without replacement, so the exact test is
the convolution of per-entry hypergeometrics. These tests pin the exact
values, including one case where the binomial tail would have missed a
real edge.
"""

import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.graded_reweight import _exact_upper_tail, strategy_credit  # noqa: E402


def _entry(date, session, numbers, actual, contrib):
    return {
        "status": "graded",
        "actual_draw_date": date,
        "actual_draw_session": session,
        "numbers": numbers,
        "actual_numbers": actual,
        "strategy_contributions": {"frequency": contrib},
        "model": "ensemble",
    }


def test_single_perfect_ticket_exact_value():
    # take5: drum 39, 5 winners, 5 picks, all 5 hit.
    # Exact p = 1 / C(39,5) = 1/575757. Binomial would say (5/39)^5,
    # about 20x larger — the old test would have hidden this edge.
    graded = [_entry("2026-10-21", "evening", [1, 2, 3, 4, 5],
                     [1, 2, 3, 4, 5], [1, 2, 3, 4, 5])]
    credit = strategy_credit(graded, "take5")
    row = credit["frequency"]
    assert row["hits"] == 5
    assert row["p_value"] == 0.000002
    assert row["p_value_method"].startswith("exact convolution")
    assert _exact_upper_tail([(39, 5, 5)], 5) == 1 / math.comb(39, 5)


def test_two_entry_convolution_beats_binomial():
    graded = [
        _entry("2026-10-21", "evening", [1, 2, 3, 4, 5],
               [1, 2, 3, 30, 31], [1, 2, 3, 4, 5]),
        _entry("2026-10-22", "midday", [1, 2, 3, 4, 5],
               [1, 2, 3, 30, 31], [1, 2, 3, 4, 5]),
    ]
    credit = strategy_credit(graded, "take5")
    row = credit["frequency"]
    assert row["hits"] == 6
    # Independent recomputation of the convolution in the test.
    def pmf(u, w, k):
        return [math.comb(w, x) * math.comb(u - w, k - x) / math.comb(u, k)
                for x in range(min(k, w) + 1)]
    dist = [1.0]
    for _ in range(2):
        p = pmf(39, 5, 5)
        new = [0.0] * (len(dist) + len(p) - 1)
        for i, a in enumerate(dist):
            for j, b in enumerate(p):
                new[i + j] += a * b
        dist = new
    expected = sum(dist[6:])
    assert abs(row["p_value"] - round(expected, 6)) < 1e-6
    # And the exact value is well below the old binomial tail.
    p_bin = sum(math.comb(10, k) * (5 / 39) ** k * (34 / 39) ** (10 - k)
                for k in range(6, 11))
    assert row["p_value"] < p_bin
