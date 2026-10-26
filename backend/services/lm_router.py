import asyncio
import time
from typing import Any, Dict, Optional

from services.chatgpt_client import chatgpt_client
from services.gemini_client import LM_UNAVAILABLE_PREFIX, gemini_client


class LMRouter:
    def __init__(self, providers: Dict[str, Any], audit_prompt: str = "Reply with exactly OK."):
        self.providers = providers
        self.audit_prompt = audit_prompt
        self._audit_lock = asyncio.Lock()
        self._audited = False
        self._ordered_available: list[str] = []
        self._audit_results: Dict[str, Dict[str, Any]] = {}

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
            response_text = await client.generate_text(self.audit_prompt)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

            if self._is_unavailable_response(response_text):
                return {
                    "provider": provider_name,
                    "available": False,
                    "latency_ms": elapsed_ms,
                    "reason": response_text,
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
                "reason": str(exc),
            }

    async def audit_connections(self, force: bool = False) -> Dict[str, Any]:
        async with self._audit_lock:
            if self._audited and not force:
                return self.snapshot()

            names = list(self.providers.keys())
            probes = [self._probe_provider(name, self.providers[name]) for name in names]
            results = await asyncio.gather(*probes)

            self._audit_results = {item["provider"]: item for item in results}
            available = [item for item in results if item.get("available")]
            available.sort(key=lambda item: item.get("latency_ms") if item.get("latency_ms") is not None else float("inf"))
            self._ordered_available = [item["provider"] for item in available]
            self._audited = True

            return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "audited": self._audited,
            "ordered_available": list(self._ordered_available),
            "providers": self._audit_results,
        }

    def _build_chain(self, preferred_provider: Optional[str]) -> list[str]:
        provider = self._normalize_provider(preferred_provider)

        if provider != "auto":
            if provider in self.providers:
                return [provider]
            return []

        available_first = list(self._ordered_available)
        remaining = [name for name in self.providers.keys() if name not in available_first]
        return available_first + remaining

    async def generate_with_provider(self, prompt: str, preferred_provider: Optional[str] = "auto") -> Dict[str, str]:
        await self.audit_connections()

        chain = self._build_chain(preferred_provider)
        if not chain:
            return {
                "provider": "fallback",
                "response": f"{LM_UNAVAILABLE_PREFIX}:api_error:No valid LM provider configured",
            }

        first_unavailable = None
        for provider_name in chain:
            client = self.providers.get(provider_name)
            if client is None:
                continue

            response_text = await client.generate_text(prompt)
            if self._is_unavailable_response(response_text):
                first_unavailable = first_unavailable or response_text
                continue

            return {
                "provider": provider_name,
                "response": response_text,
            }

        return {
            "provider": "fallback",
            "response": first_unavailable or f"{LM_UNAVAILABLE_PREFIX}:api_error:All configured LM providers are unavailable",
        }

    async def generate_text(self, prompt: str, preferred_provider: Optional[str] = "auto") -> str:
        result = await self.generate_with_provider(prompt, preferred_provider=preferred_provider)
        return result.get("response", "")


lm_router = LMRouter(
    providers={
        "gemini": gemini_client,
        "chatgpt": chatgpt_client,
    }
)
