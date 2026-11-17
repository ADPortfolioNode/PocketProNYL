"""Pattern search, regularization, and the Quick Draw ingest cap."""

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

from prediction.chain import evaluate as chain_eval  # noqa: E402
from prediction.chain.chained import ChainVariant  # noqa: E402
from prediction.chain.features import FeatureBuilder  # noqa: E402
from prediction.chain.posterior import flat_prior  # noqa: E402
from prediction.chain.stacking import C_GRID, tune_C_walk_forward  # noqa: E402
from prediction.config.loader import game_rules_from_config  # noqa: E402
from prediction.core.types import Draw  # noqa: E402
from prediction.engine import LotteryPredictionEngine  # noqa: E402
from prediction.metrics.evaluator import three_way_split  # noqa: E402
from prediction.patterns.search import SIGNAL_NAMES, PatternMatrix, run_pattern_search  # noqa: E402

SMALL = [ChainVariant(votes=3, use_subtargets=False, window=30)]


def make_history(n, game="take5", seed=1, bias=False):
    """Fair draws, or (bias=True) the five lowest balls drawn ~5x as often."""
    rules = game_rules_from_config(game)
    rng = random.Random(seed)
    pool = list(range(rules.primary_min, rules.primary_max + 1))
    weights = [5 if (bias and x <= rules.primary_min + 4) else 1 for x in pool]
    draws = []
    for i in range(n):
        if rules.primary_unique:
            picked = set()
            while len(picked) < rules.primary_count:
                picked.add(rng.choices(pool, weights)[0])
            primary = sorted(picked)
        else:
            primary = [rng.choice(pool) for _ in range(rules.primary_count)]
        bonus = [rng.randint(rules.bonus_min, rules.bonus_max)] if rules.bonus_count else []
        draws.append(Draw(primary=primary, bonus=bonus, date=date(2024, 1, 1) + timedelta(days=i),
                          draw_id=f"{game}-{i}"))
    return draws


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PREDICTION_STATE_DIR", str(tmp_path / "prediction"))
    return LotteryPredictionEngine(str(tmp_path / "prediction"))


# ------------------------------------------------------------ pattern search

def _by_name(report):
    return {row["signal"]: row for row in report["signals"]}


def test_fair_draws_pass_nothing_on_holdout():
    for game in ("take5", "powerball", "win4"):
        report = run_pattern_search(game, make_history(400, game=game, seed=3))
        assert report["status"] == "ok"
        assert report["passed_holdout"] == []
        rows = _by_name(report)
        assert set(rows) == set(SIGNAL_NAMES)
        for row in rows.values():
            if row["status"] == "not_applicable":
                continue
            assert {"lift", "p_value", "chance_hits"} <= set(row["validation"])
            assert row["q_value"] >= row["validation"]["p_value"] - 1e-9  # BH never lowers p
            assert row["label"] in {"not significant", "passed validation, failed holdout"}


def test_planted_bias_is_found_and_confirmed_on_holdout():
    report = run_pattern_search("take5", make_history(500, seed=5, bias=True))
    rows = _by_name(report)
    hot = rows["hot_w200"]
    assert hot["passed_validation"] and hot["passed_holdout"]
    assert hot["label"] == "passed holdout"
    assert hot["validation"]["lift"] > 1.5 and hot["q_value"] <= 0.05
    assert hot["holdout"]["lift"] > 1.0
    assert "hot_w200" in report["feature_signals"]
    assert rows["cold_w50"]["status"] == "not_significant"


def test_holdout_is_only_scored_for_validation_survivors():
    report = run_pattern_search("take5", make_history(400, seed=9))
    for row in report["signals"]:
        if row.get("status") == "not_applicable":
            continue
        assert ("holdout" in row) == bool(row["passed_validation"])


def test_chance_baseline_matches_hypergeometric_and_positional():
    take5 = run_pattern_search("take5", make_history(300, seed=2))
    base = _by_name(take5)["hot_w50"]["validation"]["chance_hits"]
    assert base == pytest.approx(5 * 5 / 39, abs=1e-3)
    win4 = run_pattern_search("win4", make_history(300, game="win4", seed=2))
    assert _by_name(win4)["positional"]["validation"]["chance_hits"] == pytest.approx(0.4, abs=1e-3)
    assert _by_name(win4)["bonus_frequency"]["status"] == "not_applicable"


def test_signals_never_see_the_future():
    history = make_history(200, seed=4)
    rules = game_rules_from_config("take5")
    # Different future numbers, same scheduled dates (a draw's weekday is known in advance).
    altered = history[:150] + [Draw(primary=d.primary, bonus=d.bonus, date=history[150 + j].date)
                               for j, d in enumerate(make_history(50, seed=99))]
    a, b = PatternMatrix(history, rules), PatternMatrix(altered, rules)
    for name in SIGNAL_NAMES:
        if name in ("sum_band", "odd_even", "high_low", "bonus_frequency"):
            continue
        assert np.allclose(a.scores(name, 150), b.scores(name, 150)), name


def test_split_matches_chain_evaluation_and_insufficient_data():
    from prediction.config.loader import load_game_config

    cfg = load_game_config("take5").metrics
    test_start, holdout_start, holdout = three_way_split(400, cfg, max_test_draws=600)
    report = run_pattern_search("take5", make_history(400, seed=1))
    assert report["split"]["holdout_start"] == holdout_start == 400 - holdout
    assert holdout_start == three_way_split(400, cfg)[1]  # same untouched holdout as the chain evaluation
    assert report["split"]["validation_start"] == max(test_start, 20)
    assert run_pattern_search("take5", make_history(40))["status"] == "insufficient_data"


def test_games_are_isolated():
    """A Quick Draw matrix never reads Take 5 rules or numbers outside its own universe."""
    qd = make_history(300, game="quickdraw", seed=1)
    pm = PatternMatrix(qd, game_rules_from_config("quickdraw"))
    assert pm.size == 80 and pm.k == 20
    report = run_pattern_search("quickdraw", qd)
    assert report["game"] == "quickdraw"
    assert _by_name(report)["hot_w50"]["validation"]["chance_hits"] == pytest.approx(20 * 20 / 80, abs=1e-3)


# ------------------------------------------------------------ API

def test_pattern_report_endpoint(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import prediction.core.draw_loader as loader
    from routes import patterns
    from services import pattern_scheduler

    monkeypatch.setenv("PREDICTION_STATE_DIR", str(tmp_path / "prediction"))
    monkeypatch.setenv("PATTERN_AUTO_PROMOTE", "0")
    calls = []

    def fake_from_chroma(game, limit=500):
        calls.append((game, limit))
        return make_history(300, game=game, seed=7)

    monkeypatch.setattr(loader, "from_chroma", fake_from_chroma)
    sched = pattern_scheduler.PatternScheduler(
        runner=lambda g, trigger, limit: pattern_scheduler.run_scan(g, trigger=trigger, count_fn=lambda _g: 300),
        count_fn=lambda g: 300)
    pattern_scheduler.set_scheduler(sched)
    try:
        app = FastAPI()
        app.include_router(patterns.router)
        client = TestClient(app)
        first = client.get("/api/pattern_report", params={"game": "take5"}).json()
        assert first["scan_status"] == "running" and first["cached"] is False  # first view starts a scan
        assert sched.wait_idle("take5", 120)
        done = client.get("/api/pattern_report", params={"game": "take5"}).json()
        assert done["status"] == "ok" and done["game"] == "take5" and done["cached"] is True
        assert done["scan_status"] == "fresh" and done["last_run_at"] and done["draw_count"] == 300
        for row in done["signals"]:
            assert row["label"] in {"passed holdout", "not significant", "passed validation, failed holdout",
                                    "not applicable"}
        assert client.get("/api/pattern_report", params={"game": "nope"}).status_code == 400
        assert calls[0] == ("take5", 1000)
    finally:
        pattern_scheduler.set_scheduler(None)


# ------------------------------------------------------------ ensemble + regularization

def test_pattern_features_are_appended_and_leak_free(engine):
    history = make_history(160, seed=11)
    rules = game_rules_from_config("take5")
    base = flat_prior("take5", rules)
    plain = FeatureBuilder(engine, "take5", history, base)
    pat = FeatureBuilder(engine, "take5", history, base, pattern_signals=("hot_w50", "gap_overdue"))
    assert pat.feature_names[-2:] == ["pattern:hot_w50", "pattern:gap_overdue"]
    assert pat.features_at(120).shape[1] == plain.features_at(120).shape[1] + 2
    altered = FeatureBuilder(engine, "take5", history[:120] + make_history(40, seed=77), base,
                             pattern_signals=("hot_w50", "gap_overdue"))
    assert np.allclose(pat.features_at(120), altered.features_at(120))


def test_variant_roundtrip_with_patterns():
    v = ChainVariant(votes=3, use_subtargets=False, window=30, patterns=("hot_w50",))
    assert v.name.endswith("_pat1")
    assert ChainVariant.from_dict(v.to_dict()) == v
    assert ChainVariant.from_dict({"votes": 3}).patterns == ()


def test_C_is_tuned_walk_forward_on_train_prefix_only(engine):
    history = make_history(200, seed=12)
    rules = game_rules_from_config("take5")
    builder = FeatureBuilder(engine, "take5", history, flat_prior("take5", rules))
    result = tune_C_walk_forward(builder, list(range(30, 110)))
    assert result["status"] == "ok" and result["method"] == "walk_forward_expanding_window"
    assert result["C"] in C_GRID and len(result["grid"]) == len(C_GRID)
    assert max(builder._features) < 110  # never touched validation/holdout positions


def test_evaluation_adds_pattern_challengers_but_promotes_only_on_holdout(engine):
    history = make_history(260, seed=5, bias=True)
    report = chain_eval.evaluate_chain(engine, "take5", history, variants=SMALL, commit=False,
                                       min_history=30, max_test_draws=40)
    assert report["status"] == "ok"
    assert report["regularization"]["meta_learner_C"]["status"] in {"ok", "insufficient_data"}
    feats = report["pattern_search"]["feature_signals"]
    names = [v["name"] for v in report["variants_tested"]]
    if feats:
        assert any(n.endswith(f"_pat{len(feats)}") for n in names)
    assert report["promoted"] == bool(report["validation"]["decision"]["accepted"]
                                      and report["holdout"]["chained_beats_incumbent"])


def test_fair_draws_keep_incumbent_with_pattern_search(engine):
    report = chain_eval.evaluate_chain(engine, "take5", make_history(220, seed=21), variants=SMALL,
                                       commit=False, min_history=30, max_test_draws=40)
    assert report["promoted"] is False


def test_random_forest_is_regularized(monkeypatch):
    from services.trainer import TrainerService

    trainer = TrainerService()
    assert trainer.min_samples_leaf >= 5 and trainer.max_features == "sqrt"
    rng = np.random.default_rng(0)
    X, y = rng.random((80, 4)), rng.random(80)
    m1, *_ = trainer._fit_and_score_model(X[:60], y[:60], X[60:], y[60:], y, attempt=1)
    m9, *_ = trainer._fit_and_score_model(X[:60], y[:60], X[60:], y[60:], y, attempt=9)
    assert m1.max_depth == m9.max_depth  # depth no longer grows with retries
    assert m9.min_samples_leaf == trainer.min_samples_leaf
    monkeypatch.setenv("TRAIN_MAX_FEATURES", "0.5")
    monkeypatch.setenv("TRAIN_MIN_SAMPLES_LEAF", "8")
    tuned = TrainerService()
    assert tuned.max_features == 0.5 and tuned.min_samples_leaf == 8


def test_feedforward_uses_l2_and_time_ordered_early_stopping():
    from prediction.nn.feedforward import FeedforwardNN

    rules = game_rules_from_config("take5")
    nn = FeedforwardNN(lookback=5, hidden_layers=[8], max_iter=60, patience=3)
    nn.fit(make_history(120, seed=2), rules)
    assert nn.is_fitted and nn._model.alpha == pytest.approx(1e-3)
    assert nn.best_epoch is not None and 1 <= nn.best_epoch <= 60
    scores = nn.predict_scores(make_history(10, seed=3), rules)
    assert len(scores) == 39 and abs(sum(scores) - 1.0) < 1e-6


# ------------------------------------------------------------ Quick Draw ingest cap

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _quickdraw_server(calls, total=100_000):
    """rows.json dumps everything (ignores $limit); /resource honours $limit/$offset."""
    from urllib.parse import parse_qs, urlparse

    def row(i):
        return {"draw_date": "2026-10-22T00:00:00.000", "draw_number": str(3_000_000 + i), "draw_time": "12:00",
                "winning_numbers": " ".join(f"{n:02d}" for n in range(1, 21)), "extra_multiplier": "01"}

    def get(url, params=None, timeout=None, **_kw):
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("rows.json"):
            raise AssertionError("full rows.json dump requested")
        q = parse_qs(parsed.query)
        limit, offset = int(q["$limit"][0]), int(q["$offset"][0])
        return _Resp([row(i) for i in range(offset, min(total, offset + limit))])

    return get


@pytest.mark.parametrize("installed", [False, True], ids=["service_fetch", "capped_fetch"])
def test_quickdraw_ingest_respects_row_cap(monkeypatch, installed):
    import services.socrata_fetch as socrata
    from config import DATASET_ENDPOINTS
    from services.ingest import IngestService

    calls = []
    monkeypatch.setattr(socrata.requests, "get", _quickdraw_server(calls))
    monkeypatch.setattr(socrata.time, "sleep", lambda *_: None)
    monkeypatch.setenv("INGEST_MAX_ROWS_PER_GAME", "1200")
    service = IngestService()
    service.batch_size = 500
    if installed:
        socrata.install_capped_fetch(service)
    rows, _cols = service._fetch_all_rows_from_api("quickdraw", DATASET_ENDPOINTS["quickdraw"])
    assert len(rows) == 1200
    assert len(calls) == 3  # 500 + 500 + 200: never more than the cap requested
    assert all("/resource/7sqk-ycpk.json" in url for url in calls)
    assert "%24limit=200" in calls[-1] and "%3Aid+DESC" in calls[0]
    assert all(r["source_dataset"] == "7sqk-ycpk" for r in rows)


def test_per_game_cap_override_and_dump_truncation(monkeypatch):
    import services.socrata_fetch as socrata

    monkeypatch.setenv("INGEST_MAX_ROWS_PER_GAME", "80000")
    monkeypatch.setenv("INGEST_MAX_ROWS_QUICKDRAW", "300")
    assert socrata.max_rows_for("quickdraw") == 300 and socrata.max_rows_for("take5") == 80000
    assert socrata.pageable_endpoint(
        "https://data.ny.gov/api/views/7sqk-ycpk/rows.json?accessType=DOWNLOAD"
    ) == "https://data.ny.gov/resource/7sqk-ycpk.json"
    # An endpoint that ignores $limit is still truncated to the cap and not paged further.
    calls = []

    def dump(url, params=None, timeout=None, **_kw):
        calls.append(url)
        return _Resp([{"draw_number": str(i)} for i in range(5000)])

    monkeypatch.setattr(socrata.requests, "get", dump)
    monkeypatch.setattr(socrata.time, "sleep", lambda *_: None)

    class Svc:
        batch_size = 500

    rows, _ = socrata.fetch_rows_capped(Svc(), "quickdraw", ["https://example.org/data.json"])
    assert len(rows) == 300 and len(calls) == 1
