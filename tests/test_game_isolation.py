"""Game isolation ("game bleed") tests.

Every game's draw history, training state, weights, tuning status and
suggestions must belong to that game only.
"""

import json
import random
import sys
import tempfile
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from config import DATASET_ENDPOINTS, GAME_CONFIGS, GAME_PREDICTION_FORMATS  # noqa: E402
from utils.game_validation import (  # noqa: E402
    dataset_id_from_url,
    foreign_fields_for,
    validate_game_record,
)

GAMES = [
    "take5", "pick3", "win4", "nylotto", "powerball", "megamillions",
    "cash4life", "pick10", "quickdraw",
]

# ---------------------------------------------------------------- fixtures ---

DATASET_OF = {
    "take5": "dg63-4siq",
    "pick3": "n4w8-wxte",
    "daily": "hsys-3def",  # Daily Numbers / Win 4 (also used by pick3, numbers)
    "powerball": "d6yy-54nr",
    "megamillions": "5xaw-6ayf",
    "pick10": "bycu-cw7c",
    "cash4life": "kwxv-fwze",
    "quickdraw": "7sqk-ycpk",
    "nylotto": "6nbc-h7bj",
    "millionaireforlife": "a4w9-a3tp",
}


def _board(rng, count, low, high, unique=True):
    if unique:
        return sorted(rng.sample(range(low, high + 1), count))
    return [rng.randint(low, high) for _ in range(count)]


def _fmt(numbers, width=2):
    return " ".join(str(n).zfill(width) for n in numbers)


def make_source_rows(dataset_id, count, start, seed=0):
    """Rows shaped exactly like the NY Open Data dataset ``dataset_id``."""
    rng = random.Random(f"{dataset_id}-{seed}")
    rows = []
    for index in range(count):
        day = (start + timedelta(days=index)).isoformat() + "T00:00:00.000"
        if dataset_id == "dg63-4siq":
            row = {
                "draw_date": day,
                "midday_winning_numbers": _fmt(_board(rng, 5, 1, 39)),
                "evening_winning_numbers": _fmt(_board(rng, 5, 1, 39)),
            }
        elif dataset_id == "n4w8-wxte":
            row = {
                "draw_date": day,
                "midday_daily": "".join(str(d) for d in _board(rng, 3, 0, 9, False)),
                "evening_daily": "".join(str(d) for d in _board(rng, 3, 0, 9, False)),
            }
        elif dataset_id == "hsys-3def":
            row = {
                "draw_date": day,
                "midday_daily": "".join(str(d) for d in _board(rng, 3, 0, 9, False)),
                "evening_daily": "".join(str(d) for d in _board(rng, 3, 0, 9, False)),
                "midday_win_4": "".join(str(d) for d in _board(rng, 4, 0, 9, False)),
                "evening_win_4": "".join(str(d) for d in _board(rng, 4, 0, 9, False)),
            }
        elif dataset_id == "d6yy-54nr":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 5, 1, 69) + [rng.randint(1, 26)]),
                   "multiplier": "2"}
        elif dataset_id == "5xaw-6ayf":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 5, 1, 70)),
                   "mega_ball": str(rng.randint(1, 25)), "multiplier": "3"}
        elif dataset_id == "bycu-cw7c":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 20, 1, 80))}
        elif dataset_id == "kwxv-fwze":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 5, 1, 60)),
                   "cash_ball": str(rng.randint(1, 4)).zfill(2)}
        elif dataset_id == "7sqk-ycpk":
            row = {"draw_date": day, "draw_number": str(3000000 + index), "draw_time": "04:04",
                   "winning_numbers": _fmt(_board(rng, 20, 1, 80)), "extra_multiplier": "01",
                   "money_dots_winning_number": "34", "money_dots_prize_amount": "$0"}
        elif dataset_id == "6nbc-h7bj":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 6, 1, 59)),
                   "bonus": str(rng.randint(1, 59))}
        elif dataset_id == "a4w9-a3tp":
            row = {"draw_date": day, "winning_numbers": _fmt(_board(rng, 5, 1, 58)),
                   "mill_ball": str(rng.randint(1, 5))}
        else:
            raise KeyError(dataset_id)
        rows.append(row)
    return rows


# Different start dates per dataset so record ids never collide by accident.
ROWS_PER_DATASET = 4
SOURCE_ROWS = {
    dataset_id: make_source_rows(dataset_id, ROWS_PER_DATASET, date(2020, 1, 1) + timedelta(days=40 * i))
    for i, dataset_id in enumerate(sorted(set(DATASET_OF.values())))
}


def own_dataset_ids(game):
    return {dataset_id_from_url(url) for url in DATASET_ENDPOINTS[game]}


def expected_record_count(game):
    total = 0
    for dataset_id in own_dataset_ids(game):
        rows = len(SOURCE_ROWS[dataset_id])
        # win4/numbers expand each source row into midday + evening records.
        total += rows * 2 if GAME_CONFIGS[game].get("expansion") else rows
    return total


# ---------------------------------------------------------------- fakes -----

class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.rows = OrderedDict()

    def count(self):
        return len(self.rows)

    def upsert(self, documents=None, metadatas=None, ids=None):
        for index, row_id in enumerate(ids or []):
            self.rows[row_id] = dict(metadatas[index])

    add = upsert

    def delete(self, ids=None):
        for row_id in ids or []:
            self.rows.pop(row_id, None)

    def get(self, ids=None, where=None, limit=None, offset=None, include=None):
        items = list(self.rows.items())
        if ids is not None:
            items = [(k, v) for k, v in items if k in set(ids)]
        start = int(offset or 0)
        items = items[start:start + limit] if limit is not None else items[start:]
        return {"ids": [k for k, _ in items], "metadatas": [dict(v) for _, v in items], "documents": []}


class FakeStore:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, **_kwargs):
        return self.collections.setdefault(name, FakeCollection(name))

    def get_collection(self, name, **_kwargs):
        if name not in self.collections:
            raise ValueError(f"Collection {name} does not exist")
        return self.collections[name]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PREDICTION_STATE_DIR", str(tmp_path / "prediction"))
    fake = FakeStore()

    from routes.chroma_repository import chroma_repository
    from services.chroma_client import chroma_client

    monkeypatch.setattr(chroma_repository, "get_or_create_collection", fake.get_or_create_collection)
    monkeypatch.setattr(
        chroma_repository, "get_collection",
        lambda name: fake.collections.get(name),
    )
    monkeypatch.setattr(
        chroma_repository, "count_documents",
        lambda name: fake.collections[name].count() if name in fake.collections else 0,
    )

    def _get_documents(name, ids=None, where=None, limit=None, offset=None, include=None):
        if name not in fake.collections:
            return {"ids": [], "metadatas": []}
        return fake.collections[name].get(ids=ids, where=where, limit=limit, offset=offset, include=include)

    monkeypatch.setattr(chroma_repository, "get_documents", _get_documents)
    monkeypatch.setattr(chroma_client, "_client", fake)
    return fake


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)[:200]

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def fake_requests_get(calls):
    """Serve every dataset and a catalog that lists ALL lottery datasets."""

    def _get(url, params=None, timeout=None, **_kwargs):
        calls.append(url)
        if "api/catalog" in url:
            return FakeResponse({"results": [
                {"resource": {"id": dataset_id, "name": f"Lottery {dataset_id} Winning Numbers New York"}}
                for dataset_id in SOURCE_ROWS
            ]})
        dataset_id = dataset_id_from_url(url)
        rows = SOURCE_ROWS.get(dataset_id, [])
        if "count" in url:
            return FakeResponse([{"count": str(len(rows))}])
        offset = 0
        if "%24offset=" in url:
            offset = int(url.split("%24offset=")[1].split("&")[0])
        return FakeResponse(rows if offset == 0 else [])

    return _get


# ---------------------------------------------------------------- ingest ----

def test_configured_games_never_merge_catalog_datasets(monkeypatch):
    from services.ingest import IngestService

    service = IngestService()
    service.enable_catalog_fallback = True
    other_endpoints = [url for game, urls in DATASET_ENDPOINTS.items() for url in urls]
    monkeypatch.setattr(service, "_fetch_catalog_endpoints", lambda game: list(other_endpoints))
    for game in GAMES:
        assert service._resolve_game_endpoints(game) == DATASET_ENDPOINTS[game]


@pytest.mark.parametrize("capped", [False, True], ids=["service_fetch", "capped_fetch"])
@pytest.mark.parametrize("game", GAMES)
def test_ingest_stores_only_own_game_rows(game, capped, store, monkeypatch):
    import services.ingest as ingest_module
    import services.socrata_fetch as socrata_module

    monkeypatch.setenv("PREDICTION_ENGINE", "legacy")
    calls = []
    monkeypatch.setattr(ingest_module.requests, "get", fake_requests_get(calls))
    monkeypatch.setattr(socrata_module.requests, "get", fake_requests_get(calls))

    service = ingest_module.IngestService()
    if capped:
        # Production path: main.py installs the capped Socrata fetch at startup.
        socrata_module.install_capped_fetch(service)
    service.enable_catalog_fallback = True  # the old default that caused the bleed
    result = service.fetch_and_sync(game, force=True)

    fetched = {dataset_id_from_url(url) for url in calls if "api/catalog" not in url}
    assert fetched == own_dataset_ids(game), f"{game} fetched {fetched}"

    stored = store.collections[game].rows
    assert len(stored) == expected_record_count(game)
    assert result["total"] == len(stored)
    for meta in stored.values():
        ok, reason = validate_game_record(game, meta, strict=True)
        assert ok, (game, reason, meta)
        assert meta["source_dataset"] in own_dataset_ids(game)
        assert not (set(meta) & foreign_fields_for(game))


@pytest.mark.parametrize("game", GAMES)
def test_ingest_rejects_other_games_rows_from_polluted_cache(game, store, monkeypatch, tmp_path):
    """Old caches (e.g. take5.json with Cash 4 Life + NY Lotto rows) are filtered."""
    import services.ingest as ingest_module

    monkeypatch.setenv("PREDICTION_ENGINE", "legacy")
    cache_dir = tmp_path / "ingestion_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_rows = [row for dataset_id in sorted(SOURCE_ROWS) for row in SOURCE_ROWS[dataset_id]]
    (cache_dir / f"{game}.json").write_text(json.dumps({"rows": all_rows, "columns": []}))

    result = ingest_module.IngestService().fetch_and_sync(game, force=False)
    stored = store.collections[game].rows
    assert len(stored) == expected_record_count(game)
    assert result["rejected"] > 0
    for meta in stored.values():
        assert validate_game_record(game, meta, strict=True)[0]


def test_take5_rejects_cash4life_and_nylotto_rows():
    c4l = SOURCE_ROWS["kwxv-fwze"][0]
    lotto = SOURCE_ROWS["6nbc-h7bj"][0]
    # A Cash 4 Life board can fit 1..39 by chance; it is still rejected by provenance.
    c4l_low = {**c4l, "winning_numbers": "01 05 09 22 38"}
    for row in (c4l, lotto, c4l_low):
        assert not validate_game_record("take5", row, strict=True)[0]
        assert not validate_game_record("take5", row, strict=False)[0]
    assert not validate_game_record(
        "take5", {**SOURCE_ROWS["dg63-4siq"][0], "source_dataset": "kwxv-fwze"}
    )[0]
    assert validate_game_record("take5", {**SOURCE_ROWS["dg63-4siq"][0], "source_dataset": "dg63-4siq"})[0]


def test_validation_number_rules():
    assert not validate_game_record("pick3", {"draw_date": "2026-01-01", "winning_numbers": "1,2,3,4"})[0]
    assert validate_game_record("win4", {"draw_date": "2026-01-01", "winning_numbers": "0,0,1,2"})[0]
    assert not validate_game_record("cash4life", {"draw_date": "2026-01-01", "winning_numbers": "1 2 3 4 5",
                                                  "cash_ball": "9"})[0]
    assert not validate_game_record("nylotto", {"draw_date": "2026-01-01", "winning_numbers": "1 2 3 4 5 60",
                                                "bonus": "3"})[0]
    assert not validate_game_record("take5", {"draw_date": "2026-01-01",
                                              "evening_winning_numbers": "1 1 2 3 4"})[0]
    # Genuine older Mega Millions matrix (5/75 + 1/15) is kept in storage.
    assert validate_game_record("megamillions", {"draw_date": "2014-01-01", "winning_numbers": "03 19 45 61 72",
                                                 "mega_ball": "14"})[0]


# ---------------------------------------------------------------- repair ----

def test_repair_removes_only_foreign_rows_and_is_idempotent(store):
    from services.data_repair import repair_game_collection
    from services.ingest import IngestService

    collection = store.get_or_create_collection("take5")
    service = IngestService()
    own = SOURCE_ROWS["dg63-4siq"]
    foreign = SOURCE_ROWS["kwxv-fwze"] + SOURCE_ROWS["6nbc-h7bj"]
    for index, row in enumerate(own + foreign):
        records = service._normalize_game_records("take5", service._normalize_draw_metadata(row), f"id-{index}")
        for record_id, meta in records:
            collection.upsert(ids=[record_id], metadatas=[meta], documents=[""])
    # A valid take5 draw imported in compact form (no foreign columns) must survive.
    collection.upsert(ids=["imported"], metadatas=[{"draw_date": "2019-05-05",
                                                    "winning_numbers": "2,9,17,30,39"}], documents=[""])
    before = collection.count()

    summary = repair_game_collection("take5", collection=collection)
    assert summary["removed"] == len(foreign)
    assert collection.count() == before - len(foreign) == len(own) + 1
    assert "imported" in collection.rows

    again = repair_game_collection("take5", collection=collection)
    assert again["removed"] == 0
    assert collection.count() == len(own) + 1


def test_repair_refuses_to_empty_a_collection(store):
    from services.data_repair import repair_game_collection

    collection = store.get_or_create_collection("pick3")
    for index, row in enumerate(SOURCE_ROWS["6nbc-h7bj"]):
        collection.upsert(ids=[f"x{index}"], metadatas=[row], documents=[""])
    summary = repair_game_collection("pick3", collection=collection)
    assert summary["status"] == "refused"
    assert collection.count() == len(SOURCE_ROWS["6nbc-h7bj"])
    dry = repair_game_collection("pick3", collection=collection, dry_run=True, allow_full_wipe=True)
    assert dry["would_remove"] == collection.count() and dry["removed"] == 0


# ---------------------------------------------------------------- loader ----

def test_history_loader_returns_newest_own_draws(store):
    from prediction.core.draw_loader import from_chroma

    collection = store.get_or_create_collection("take5")
    rows = make_source_rows("dg63-4siq", 30, date(2025, 1, 1), seed=7)
    for index, row in enumerate(rows):
        collection.upsert(ids=[f"t{index}"], metadatas=[{**row, "draw_date": row["draw_date"][:10]}], documents=[""])
    # Polluted rows inserted LAST (the old offset-based tail read picked these).
    for index, row in enumerate(SOURCE_ROWS["6nbc-h7bj"] + SOURCE_ROWS["kwxv-fwze"]):
        collection.upsert(ids=[f"bad{index}"], metadatas=[{**row, "draw_date": "2026-12-31"}], documents=[""])
    collection.upsert(ids=["dup"], metadatas=[{**rows[-1], "draw_date": rows[-1]["draw_date"][:10]}], documents=[""])

    draws = from_chroma("take5", limit=10)
    assert len(draws) == 10
    assert all(str(d.draw_id).startswith("t") or str(d.draw_id).startswith("dup") for d in draws)
    dates = [d.draw_date for d in draws]
    assert dates == sorted(dates)
    assert dates[-1] == date(2025, 1, 30)
    assert {d.metadata.get("draw_session") for d in draws} == {"midday", "evening"}
    assert all(len(d.primary) == 5 and all(1 <= n <= 39 for n in d.primary) for d in draws)


# ------------------------------------------------------- weights / tuning ---

def test_weight_store_is_per_game_and_copies_defaults():
    from prediction.state.weight_store import WeightStore

    with tempfile.TemporaryDirectory() as tmp:
        store_ = WeightStore(tmp)
        initial = {"frequency": 0.5, "delta": 0.5}
        state = store_.load("take5", initial_weights=initial)
        state.weights["frequency"] = 99.0
        assert initial == {"frequency": 0.5, "delta": 0.5}
        store_.save(state)
        assert store_.load("TAKE5 ").weights["frequency"] == 99.0
        assert store_.load("pick3", initial_weights=initial).weights == {"frequency": 0.5, "delta": 0.5}
        assert not (Path(tmp) / "pick3_weights.json").exists()
        with pytest.raises(ValueError):
            store_._path("../take5")


def _synthetic_history(game, count, seed=3):
    from prediction.config.loader import game_rules_from_config
    from prediction.core.types import Draw

    rules = game_rules_from_config(game)
    rng = random.Random(seed)
    draws = []
    for index in range(count):
        if rules.primary_unique:
            primary = sorted(rng.sample(range(rules.primary_min, rules.primary_max + 1), rules.primary_count))
        else:
            primary = [rng.randint(rules.primary_min, rules.primary_max) for _ in range(rules.primary_count)]
        bonus = [rng.randint(rules.bonus_min, rules.bonus_max)] if rules.bonus_count else []
        draws.append(Draw(primary=primary, bonus=bonus, draw_id=f"{game}-{index}",
                          date=date(2024, 1, 1) + timedelta(days=index)))
    return draws


def test_tuning_one_game_leaves_other_games_state_untouched(monkeypatch):
    import prediction.engine as engine_module
    from prediction.state.weight_store import WeightStore
    from services.game_tuner import GameTuner

    histories = {"take5": _synthetic_history("take5", 160), "pick3": _synthetic_history("pick3", 160)}
    monkeypatch.setattr(engine_module, "from_chroma", lambda game, limit=500: histories[game][-limit:])

    with tempfile.TemporaryDirectory() as tmp:
        store_ = WeightStore(tmp)
        pick3_state = store_.load("pick3", initial_weights={"frequency": 0.7, "delta": 0.3})
        store_.save(pick3_state)
        pick3_before = (Path(tmp) / "pick3_weights.json").read_text()

        tuner = GameTuner(state_dir=tmp)
        result = tuner.tune_game("take5", settings={"rounds": 1, "patience": 1, "rolling_window": 20})
        assert result["game"] == "take5"
        assert (Path(tmp) / "pick3_weights.json").read_text() == pick3_before

        status = tuner.status()
        assert set(status["metrics_by_game"]) == {"take5"}
        assert status["tuning_metrics_game"] == "take5"

        # A pick3 run that produces no metrics must not inherit take5's.
        from services import game_tuner as tuner_module

        class _NoopAssistant:
            def __init__(self, *_a, **_k):
                pass

            def optimize_game(self, game, run_trial=None, trials=None):
                return {"status": "error", "game": game}

        monkeypatch.setattr(tuner_module, "TuningAssistant", _NoopAssistant)
        tuner.optimize_game("pick3")
        pick3_metrics = tuner.status()["metrics_by_game"]["pick3"]
        assert not pick3_metrics.get("suggestion_history")
        assert not pick3_metrics.get("run_history")
        assert pick3_metrics["weights"] == {"frequency": 0.7, "delta": 0.3}


def test_tuning_chart_never_shows_another_games_metrics():
    from services.tuning_chart import chart_status_for_game

    status = {
        "tuning_metrics": {"suggestion_history": [{"accuracy_percent": 50}]},
        "tuning_metrics_game": "take5",
        "metrics_by_game": {"take5": {"suggestion_history": [{"accuracy_percent": 50}]}},
        "last_run": {"game": "take5", "delta_percent": 3},
    }
    view = chart_status_for_game(status, "pick3")
    assert view["tuning_metrics"] == {}
    assert view["last_run"] is None
    assert chart_status_for_game(status, "take5")["tuning_metrics"]["suggestion_history"]


def test_trainer_restores_shared_knobs_after_each_game(monkeypatch):
    from services.trainer import TrainerService

    trainer = TrainerService()
    baseline = trainer.data_limit, trainer.n_estimators, trainer.window_size

    def _fake_train_game(game, *args, **kwargs):
        trainer.data_limit = 2500
        trainer.n_estimators = 7
        trainer.window_size = 99
        return {"status": "success", "game": game}

    monkeypatch.setattr(trainer, "_train_game", _fake_train_game)
    assert trainer.train("take5")["game"] == "take5"
    assert (trainer.data_limit, trainer.n_estimators, trainer.window_size) == baseline


# ------------------------------------------------------------ suggestions ---

def _seed_collection(store, game, count=120):
    collection = store.get_or_create_collection(game)
    from services.ingest import IngestService

    service = IngestService()
    dataset_ids = sorted(own_dataset_ids(game))
    rows = make_source_rows(dataset_ids[-1], count, date(2023, 1, 1), seed=11)
    for index, row in enumerate(rows):
        meta = service._normalize_draw_metadata({**row, "source_dataset": dataset_ids[-1]})
        for record_id, record in service._normalize_game_records(game, meta, f"{game}-{index}"):
            collection.upsert(ids=[record_id], metadatas=[record], documents=[""])
    # Pollute every game's store with every other game's rows.
    for other_dataset, other_rows in SOURCE_ROWS.items():
        if other_dataset in own_dataset_ids(game):
            continue
        for index, row in enumerate(other_rows):
            collection.upsert(ids=[f"{other_dataset}-{index}"], metadatas=[row], documents=[""])


def _assert_numbers_fit(game, payload):
    fmt = GAME_PREDICTION_FORMATS[game]
    cfg = GAME_CONFIGS[game]
    if "predicted_main_numbers" not in payload:
        payload = (payload.get("prediction_session") or [{}])[0]
    main = payload["predicted_main_numbers"]
    assert len(main) == int(cfg["primary_count"]) or len(main) == int(fmt["main_count"])
    assert all(fmt["main_min"] <= n <= fmt["main_max"] for n in main), (game, main)
    if fmt.get("unique_main"):
        assert len(set(main)) == len(main)
    bonus = payload.get("predicted_bonus_numbers") or []
    assert len(bonus) == int(fmt.get("bonus_count") or 0)
    assert all(fmt["bonus_min"] <= n <= fmt["bonus_max"] for n in bonus), (game, bonus)


@pytest.mark.parametrize("game", GAMES)
def test_modular_predict_fits_each_games_rules(game, store, tmp_path):
    from services.prediction_adapter import PredictionAdapter

    _seed_collection(store, game)
    result = PredictionAdapter(state_dir=str(tmp_path / "state")).predict_next_draw(game)
    assert result["status"] == "success", result
    assert result["game"] == game
    _assert_numbers_fit(game, result)
    indexes = [item["draw_index"] for item in result["prediction_session"]]
    assert indexes == list(range(1, len(indexes) + 1))


def test_predict_all_maps_each_result_to_its_game(store):
    from services.predictor import PredictorService

    for game in GAMES:
        _seed_collection(store, game, count=60)
    results = PredictorService().predict_all_games(GAMES)
    assert [item["game"] for item in results] == GAMES
    for game, item in zip(GAMES, results):
        assert item["status"] == "success", item
        assert item["prediction"]["game"] == game
        _assert_numbers_fit(game, item["prediction"])
