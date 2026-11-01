"""Concierge /api/chat RAG trace: RAG ON must retrieve even with no
game selected (all-games scope), and the response must carry a trace
describing the real retrieval outcome for the process display."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from routes.chat import ChatRequest, chat as chat_endpoint


class FakeRag:
    def __init__(self, docs=None, fail=False):
        self.docs = docs if docs is not None else [
            {"game": "take5", "content": "Take 5 draw 2026-01-01: 1 2 3 4 5",
             "distance": 0.1, "metadata": {}},
            {"game": "pick3", "content": "Pick 3 draw 2026-01-01: 1 2 3",
             "distance": 0.2, "metadata": {}},
        ]
        self.fail = fail
        self.calls = []

    def retrieve_context(self, query, game=None, use_all_games=False, top_k=None):
        self.calls.append({"query": query, "game": game,
                           "use_all_games": use_all_games, "top_k": top_k})
        if self.fail:
            raise RuntimeError("chroma down")
        return list(self.docs)


class FakeRouter:
    async def generate_with_provider(self, prompt, preferred_provider=None):
        return {"provider": "gemini", "response": "grounded answer", "error": None}


def _run(request, rag):
    import routes.chat as chat_module
    old_rag, old_router = chat_module.rag_service, chat_module.lm_router
    chat_module.rag_service, chat_module.lm_router = rag, FakeRouter()
    try:
        return asyncio.run(chat_endpoint(request))
    finally:
        chat_module.rag_service, chat_module.lm_router = old_rag, old_router


def test_all_games_scope_retrieves_and_traces():
    rag = FakeRag()
    result = _run(ChatRequest(text="What's hot lately?", game=None, use_rag=True), rag)
    assert result.tool_name is None
    assert rag.calls and rag.calls[0]["use_all_games"] is True
    assert result.rag_trace["retrieval"] == "done"
    assert result.rag_trace["scope"] == "all games"
    assert result.rag_trace["sources_count"] == 2
    assert result.context_used is True
    assert result.rag_trace["provider"] == "gemini"
    assert result.rag_trace["fallback"] is False


def test_single_game_scope_retrieves_that_game():
    rag = FakeRag()
    result = _run(ChatRequest(text="What's hot in Take 5 lately?",
                              game="take5", use_rag=True), rag)
    assert result.tool_name is None
    assert rag.calls and rag.calls[0]["game"] == "take5"
    assert rag.calls[0]["use_all_games"] is False
    assert result.rag_trace["scope"] == "take5"
    assert result.rag_trace["retrieval"] == "done"


def test_rag_off_skips_retrieval():
    rag = FakeRag()
    result = _run(ChatRequest(text="What's hot lately?", game=None, use_rag=False), rag)
    assert rag.calls == []
    assert result.rag_trace["retrieval"] == "skipped"
    assert result.context_used is False


def test_retrieval_failure_is_traced_not_silent():
    rag = FakeRag(fail=True)
    result = _run(ChatRequest(text="What's hot lately?", game=None, use_rag=True), rag)
    assert result.rag_trace["retrieval"] == "failed"
    assert result.rag_trace["sources_count"] == 0
    assert result.response == "grounded answer"
