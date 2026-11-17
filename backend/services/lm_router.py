import asyncio
import time
from typing import Any, Dict, Optional

from config import settings
from services.chatgpt_client import chatgpt_client
from services.gemini_client import LM_UNAVAILABLE_PREFIX, gemini_client, grok_client
from services.groq_client import groq_client
from services.llm_common import (
    join_chain_errors,
    parse_sentinel,
    strip_api_keys,
)
from services.ollama_client import ollama_client

# Fixed provider chain order for "auto" mode. This deliberately replaces
# the previous latency-ordered audit order: the chain must be
# predictable (Gemini -> Groq -> Ollama -> Grok -> ChatGPT), with the
# retrieved-text rung after it (wired by routes/chat.py and
# services/rag_service.py, which own the retrieved documents).
# Existing providers are kept -- Grok and ChatGPT are rungs after
# Ollama, not dropped -- and retrieved text remains the last rung.
CHAIN_ORDER = ["gemini", "groq", "ollama", "grok", "chatgpt"]

# Model shown per provider on /api/llm/status (never keys).
_PROVIDER_MODELS = {
    "gemini": lambda: settings.GEMINI_MODEL,
    "groq": lambda: settings.GROQ_MODEL,
    "ollama": lambda: settings.OLLAMA_MODEL,
    "grok": lambda: settings.GROK_MODEL,
    "chatgpt": lambda: settings.CHATGPT_MODEL,
}


class LMRouter:
    def __init__(self, providers: Dict[str, Any], audit_prompt: str = "Reply with exactly OK."):
        self.providers = providers
        self.audit_prompt = audit_prompt
        self._audit_lock = asyncio.Lock()
        self._audited = False
        self._ordered_available: list[str] = []
        self._audit_results: Dict[str, Dict[str, Any]] = {}
        self.last_error: Optional[str] = None

    def _normalize_provider(self, value: Optional[str]) -> str:
        provider = (value or "auto").strip().lower()
        aliases = {
            "openai": "chatgpt",
            "chat_gpt": "chatgpt",
        }
        return aliases.get(provider, provider)

    def _is_configured(self, client: Any) -> bool:
        checker = getattr(client, "is_available", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return bool(getattr(client, "api_key", None))

    def _is_unavailable_response(self, text: Any) -> bool:
        return isinstance(text, str) and text.startswith(LM_UNAVAILABLE_PREFIX)

    async def _generate_for_provider(self, provider_name: str, client: Any,
                                     prompt: str) -> str:
        """Drive one rung, keeping provider attribution exact.

        The "gemini" rung must be Gemini-only: GeminiClient's plain
        generate_text() falls through to xAI Grok internally, which
        would report a Grok answer as "gemini". All other rungs use
        their client's generate_text() directly.
        """
        if provider_name == "gemini" and hasattr(client, "generate_text"):
            try:
                return await client.generate_text(prompt, gemini_only=True)
            except TypeError:
                return await client.generate_text(prompt)
        return await client.generate_text(prompt)

    def _known_keys(self):
        return [k for k in (
            settings.GEMINI_API_KEY,
            settings.GROK_API_KEY,
            settings.GROQ_API_KEY,
            settings.CHAT_GPT_API_KEY,
            settings.OPENAI_API_KEY,
        ) if k]

    def _sanitize(self, text):
        return strip_api_keys(text, self._known_keys()) if isinstance(text, str) else text

    async def _probe_provider(self, provider_name: str, client: Any) -> Dict[str, Any]:
        if not self._is_configured(client):
            return {
                "provider": provider_name,
                "available": False,
                "latency_ms": None,
                "reason": "not_configured",
            }

        started = time.perf_counter()
        try:
            response_text = await self._generate_for_provider(
                provider_name, client, self.audit_prompt)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

            if self._is_unavailable_response(response_text):
                return {
                    "provider": provider_name,
                    "available": False,
                    "latency_ms": elapsed_ms,
                    "reason": self._sanitize(response_text),
                }

            return {
                "provider": provider_name,
                "available": True,
                "latency_ms": elapsed_ms,
                "reason": None,
            }
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "provider": provider_name,
                "available": False,
                "latency_ms": elapsed_ms,
                "reason": self._sanitize(str(exc)),
            }

    async def audit_connections(self, force: bool = False) -> Dict[str, Any]:
        async with self._audit_lock:
            if self._audited and not force:
                return self.snapshot()

            names = list(self.providers.keys())
            probes = [self._probe_provider(name, self.providers[name]) for name in names]
            results = await asyncio.gather(*probes)

            self._audit_results = {item["provider"]: item for item in results}
            # Fixed chain order, not latency order. _ordered_available
            # keeps its name for the startup log in main.py, but it now
            # lists available providers in chain order.
            self._ordered_available = [
                name for name in CHAIN_ORDER
                if self._audit_results.get(name, {}).get("available")
            ]
            self._audited = True

            return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "audited": self._audited,
            "ordered_available": list(self._ordered_available),
            "providers": self._audit_results,
        }

    def status(self) -> Dict[str, Any]:
        """Public status surface for GET /api/llm/status.

        Lists every provider in chain order with configured/available
        flags, model, and last error -- never any key value. Keys are
        stripped again here as defence in depth.
        """
        providers = []
        for name in CHAIN_ORDER:
            client = self.providers.get(name)
            audit = self._audit_results.get(name, {})
            model = None
            try:
                model = _PROVIDER_MODELS[name]()
            except Exception:
                model = None
            providers.append({
                "provider": name,
                "configured": bool(self._is_configured(client)) if client is not None else False,
                "available": bool(audit.get("available")) if self._audited else None,
                "model": model,
                "last_error": self._sanitize(getattr(client, "last_error", None)),
                "latency_ms": audit.get("latency_ms"),
            })
        return {
            "chain_order": list(CHAIN_ORDER),
            "retrieved_text_last": True,
            "providers": providers,
            "last_error": self._sanitize(self.last_error),
        }

    def _build_chain(self, preferred_provider: Optional[str]) -> list[str]:
        provider = self._normalize_provider(preferred_provider)

        if provider != "auto":
            # Explicit provider = that provider only (failover disabled),
            # preserving the previous override semantics.
            if provider in self.providers:
                return [provider]
            return []

        # Fixed order, regardless of audit latency. Rungs that are not
        # configured are still attempted: their clients return a
        # not_configured sentinel, which is how the joined error names
        # e.g. "groq: not configured" instead of silently skipping it.
        return [name for name in CHAIN_ORDER if name in self.providers]

    async def generate_with_provider(self, prompt: str, preferred_provider: Optional[str] = "auto") -> Dict[str, str]:
        await self.audit_connections()

        chain = self._build_chain(preferred_provider)
        if not chain:
            error = "No valid LM provider configured"
            self.last_error = error
            return {
                "provider": "fallback",
                "response": f"{LM_UNAVAILABLE_PREFIX}:api_error:{error}",
                "error": error,
                "errors": {},
                "attempted": [],
            }

        errors: Dict[str, str] = {}
        attempted: list[str] = []
        for provider_name in chain:
            client = self.providers.get(provider_name)
            if client is None:
                continue
            attempted.append(provider_name)

            try:
                response_text = await self._generate_for_provider(
                    provider_name, client, prompt)
            except Exception as exc:
                response_text = (
                    f"{LM_UNAVAILABLE_PREFIX}:api_error:"
                    f"{self._sanitize(str(exc))}")

            if self._is_unavailable_response(response_text):
                _, detail = parse_sentinel(response_text, LM_UNAVAILABLE_PREFIX)
                # Per-rung detail is already key-stripped and capped at
                # 300 chars by the clients; prefix it with the rung name
                # so the joined error names the failing provider. A
                # not_configured sentinel already starts with the rung
                # name, so it is kept as-is.
                detail = self._sanitize(detail or response_text)
                if detail.startswith(f"{provider_name}:"):
                    errors[provider_name] = detail
                else:
                    errors[provider_name] = f"{provider_name}: {detail}"
                continue

            self.last_error = None
            return {
                "provider": provider_name,
                "response": response_text,
                "error": None,
                "errors": errors,
                "attempted": attempted,
            }

        # All rungs failed. Join per-rung notes with a 1000-char cap
        # (not 300): a single Gemini error alone is 301 chars, and a
        # 300-char joined cap previously cut the Groq note off entirely.
        joined = join_chain_errors(
            [errors[name] for name in chain if name in errors])
        if not joined:
            joined = "All configured LM providers are unavailable"
        self.last_error = joined
        first_unavailable = None
        for name in chain:
            if name in errors:
                first_unavailable = (
                    f"{LM_UNAVAILABLE_PREFIX}:api_error:{errors[name]}")
                break
        return {
            "provider": "fallback",
            "response": first_unavailable or f"{LM_UNAVAILABLE_PREFIX}:api_error:All configured LM providers are unavailable",
            "error": joined,
            "errors": errors,
            "attempted": attempted,
        }

    async def generate_text(self, prompt: str, preferred_provider: Optional[str] = "auto") -> str:
        result = await self.generate_with_provider(prompt, preferred_provider=preferred_provider)
        return result.get("response", "")


lm_router = LMRouter(
    providers={
        "gemini": gemini_client,
        "groq": groq_client,
        "ollama": ollama_client,
        "grok": grok_client,
        "chatgpt": chatgpt_client,
    }
)
