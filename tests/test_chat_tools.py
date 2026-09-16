"""Concierge command routing for training and tuning."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from utils.chat_tools import (
    _render_tool_response,
    detect_chat_tool,
    is_all_games_scope,
    resolve_tool_game,
)


def test_detect_chat_tool_trains_all_games_command():
    assert detect_chat_tool("train all games") == "train_models"
    assert detect_chat_tool("Train all games") == "train_models"
    assert detect_chat_tool("retrain pick 3") == "train_models"
    assert detect_chat_tool("How do I train Pick 3 without a gateway timeout?") is None
    assert detect_chat_tool("Tune all games for the next suggestion pass") == "optimize_suggestions"


def test_resolve_tool_game_all_and_specific():
    assert resolve_tool_game("train all games", selected="__all_games__") == "all"
    assert resolve_tool_game("train all games", selected="take5") == "all"
    assert resolve_tool_game("train pick 3", selected="take5") == "pick3"
    assert resolve_tool_game("train take 5") == "take5"
    assert is_all_games_scope("all_games")
    assert is_all_games_scope("__all_games__")
    assert not is_all_games_scope("take5")


def test_train_models_response_does_not_mention_api_keys():
    text = _render_tool_response("train_models", {
        "status": "started",
        "game": "all",
        "started": ["take5", "pick3"],
        "already_running": [],
    })
    assert "Training started" in text
    assert "api key" not in text.lower()
    assert "take5" in text


def test_enqueue_training_games_starts_all_scope(monkeypatch):
    import time
    from routes import training as training_mod
    from state.train_jobs import set_job

    ran = []

    def fake_worker(game_key, request):
        ran.append(game_key)
        set_job(game_key, {"status": "completed", "game": game_key})

    monkeypatch.setattr(training_mod, "_training_worker", fake_worker)
    monkeypatch.setattr(training_mod, "GAME_CONFIGS", {"take5": {}, "pick3": {}})

    result = training_mod.enqueue_training_games(["all_games"])
    assert result["status"] == "started"
    assert result["game"] == "all"
    assert result["started"] == ["take5", "pick3"]
    for _ in range(40):
        if len(ran) == 2:
            break
        time.sleep(0.05)
    assert ran == ["take5", "pick3"]
