"""Provider-chain tests for the PocketProNYL LLM port (funkStudio PRs #16-#20).

All providers and servers here are fake; no real API key is used and no
real provider is contacted. Run from the repo root or the backend dir,
e.g. ``pytest tests/test_llm_chain.py -v``.
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# The full backend deps (google-generativeai, openai) may not be installed
# in a minimal test env; stub only the SDK surface the clients touch at
# import time. When the real packages are present they are used as-is.
try:
    import google.generativeai  # noqa: F401
except Exception:  # pragma: no cover - stub path
    google_mod = ModuleType("google")
    genai_mod = ModuleType("google.generativeai")
    genai_mod.configure = lambda **kwargs: None

    class _DummyModel:
        def __init__(self, *args, **kwargs):
            pass

        def generate_content(self, prompt):
            raise RuntimeError("stubbed Gemini SDK")

    genai_mod.GenerativeModel = _DummyModel
    google_mod.generativeai = genai_mod
    sys.modules.setdefault("google", google_mod)
    sys.modules.setdefault("google.generativeai", genai_mod)

try:
    import openai  # noqa: F401
except Exception:  # pragma: no cover - stub path
    openai_mod = ModuleType("openai")

    class _DummyOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    openai_mod.OpenAI = _DummyOpenAI
    sys.modules.setdefault("openai", openai_mod)

from services.gemini_client import LM_UNAVAILABLE_PREFIX
from services.lm_router import CHAIN_ORDER, LMRouter

FAKE_KEY = "sk-fake-test-key-12345"


class FakeClient:
    """Minimal provider double: answer, sentinel failure, or both."""

    def __init__(self, answer=None, sentinel=None):
        self.answer = answer
        self.sentinel = sentinel
        self.calls = []
        self.last_error = None

    def is_available(self):
        return True

    async def generate_text(self, prompt, **kwargs):
        self.calls.append(prompt)
        if self.sentinel is not None:
            self.last_error = self.sentinel
            return self.sentinel
        return self.answer


def _sentinel(kind, detail):
    return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"


def _router_with(gemini=None, groq=None, ollama=None, grok=None, chatgpt=None):
    return LMRouter(providers={
        "gemini": gemini or FakeClient(sentinel=_sentinel("api_error", "gemini down")),
        "groq": groq or FakeClient(sentinel=_sentinel("api_error", "groq down")),
        "ollama": ollama or FakeClient(
            sentinel=_sentinel("not_configured", "ollama: not configured (OLLAMA_ENABLED is not set)")),
        "grok": grok or FakeClient(sentinel=_sentinel("api_error", "grok down")),
        "chatgpt": chatgpt or FakeClient(sentinel=_sentinel("api_error", "chatgpt down")),
    })


def test_chain_order_is_fixed():
    assert CHAIN_ORDER == ["gemini", "groq", "ollama", "grok", "chatgpt"]


def test_gemini_fail_falls_through_to_groq_answer_with_mode_groq():
    gemini = FakeClient(sentinel=_sentinel(
        "rate_limited", "gemini 429 ResourceExhausted"))
    groq = FakeClient(answer="groq answer")
    router = _router_with(gemini=gemini, groq=groq)
    result = asyncio.run(router.generate_with_provider("hello"))
    assert result["provider"] == "groq"
    assert result["response"] == "groq answer"
    assert gemini.calls and groq.calls


def test_no_groq_key_note_is_not_truncated_away():
    # A single Gemini error alone is 301 chars: with a 300-char joined
    # cap the Groq note after it was cut off entirely (funkStudio #18).
    long_gemini_error = "x" * 301
    gemini = FakeClient(sentinel=_sentinel("rate_limited", long_gemini_error))
    groq = FakeClient(sentinel=_sentinel(
        "not_configured", "groq: not configured (no GROQ_API_KEY loaded)"))
    router = _router_with(gemini=gemini, groq=groq)
    result = asyncio.run(router.generate_with_provider("hello"))
    assert result["provider"] == "fallback"
    assert "groq: not configured" in (result["error"] or "")
    assert len(result["error"] or "") <= 1000
    assert len(result["error"] or "") > 300


def test_explicit_provider_means_that_provider_only():
    groq = FakeClient(answer="groq answer that must not be used")
    grok = FakeClient(sentinel=_sentinel("api_error", "grok down"))
    router = _router_with(groq=groq, grok=grok)
    result = asyncio.run(router.generate_with_provider("hello", preferred_provider="grok"))
    assert result["provider"] == "fallback"
    # The startup audit probes every provider once (pre-existing
    # behaviour), but the real prompt must never reach Groq here.
    assert "hello" not in groq.calls
    assert all(call == "Reply with exactly OK." for call in groq.calls)


def test_chain_error_strips_api_keys(monkeypatch):
    # Defence in depth: the router strips settings-known keys from any
    # rung detail (real clients already strip their own key first).
    from config import settings
    monkeypatch.setattr(settings, "GROQ_API_KEY", FAKE_KEY)
    gemini = FakeClient(sentinel=_sentinel(
        "api_error", f"request failed, key was {FAKE_KEY} in the message"))
    router = _router_with(gemini=gemini)
    result = asyncio.run(router.generate_with_provider("hello"))
    assert FAKE_KEY not in (result["error"] or "")
    assert "[key]" in (result["error"] or "")


def test_llm_common_strips_keys_directly():
    from services.llm_common import sanitize_error, strip_api_keys
    text = f"boom {FAKE_KEY} end"
    assert FAKE_KEY not in strip_api_keys(text, [FAKE_KEY])
    assert "[key]" in strip_api_keys(text, [FAKE_KEY])
    assert len(sanitize_error("y" * 500, [])) == 300


def test_groq_request_carries_real_user_agent_not_python_urllib():
    from services.groq_client import GroqClient

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            seen["user_agent"] = self.headers.get("User-Agent")
            body = json.dumps({"choices": [{"message": {"content": "fake groq reply"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = GroqClient.__new__(GroqClient)
        client.api_key = FAKE_KEY
        client.api_base = f"http://127.0.0.1:{server.server_port}"
        client.model_name = "openai/gpt-oss-20b"
        client.last_error = None
        answer = asyncio.run(client.generate_text("hello"))
    finally:
        server.shutdown()
        server.server_close()
    assert answer == "fake groq reply"
    assert seen["user_agent"] == "PocketPro/1.0"
    assert not (seen["user_agent"] or "").startswith("Python-urllib")


def test_all_fail_falls_back_to_retrieved_text():
    # Import rag_service with chroma stubbed: the real chroma client
    # pulls in chromadb and a live Chroma host, neither needed here.
    # The stub is restored afterwards so other tests are unaffected.
    if "services.rag_service" not in sys.modules:
        chroma_stub = ModuleType("services.chroma_client")
        chroma_stub.chroma_client = object()
        previous = sys.modules.get("services.chroma_client")
        sys.modules["services.chroma_client"] = chroma_stub
        try:
            import services.rag_service as rag_module
        finally:
            if previous is None:
                sys.modules.pop("services.chroma_client", None)
            else:
                sys.modules["services.chroma_client"] = previous
    else:
        import services.rag_service as rag_module

    service = rag_module.RAGService.__new__(rag_module.RAGService)
    service.top_k = 5
    docs = [{
        "game": "take5",
        "content": "take5 draw 1 2 3 4 5",
        "metadata": {"winning_numbers": "1 2 3 4 5", "draw_date": "2026-10-01"},
    }]
    service._retrieve_context = lambda query, game=None, use_all_games=False, top_k=None: docs

    class FailingRouter:
        async def generate_with_provider(self, prompt, preferred_provider="auto"):
            return {
                "provider": "fallback",
                "response": _sentinel("api_error", "all providers down"),
                "error": "gemini: down; groq: not configured",
            }

    result = asyncio.run(service.query_with_rag(
        "show latest take5 winning numbers", game="take5",
        lm_client=FailingRouter(), lm_provider="auto"))
    assert result["lm_provider"] == "retrieved_text"
    assert "local ChromaDB context only" in result["response"]
    assert "1 2 3 4 5" in result["response"]
