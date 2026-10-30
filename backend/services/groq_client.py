import asyncio

import requests

from config import settings
from services.gemini_client import LM_UNAVAILABLE_PREFIX
from services.llm_common import USER_AGENT, sanitize_error


class GroqClient:
    """Groq client via its OpenAI-compatible endpoint (stdlib-free: requests
    is already a dependency, so no new package is needed).

    This is Groq (api.groq.com, model openai/gpt-oss-20b). It is NOT xAI
    Grok (api.x.ai, GROK_* settings, kept separately in gemini_client.py);
    the two must never be merged.

    The request carries an explicit User-Agent: Cloudflare in front of
    Groq rejects the stdlib urllib default with 403 error code 1010.
    """

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.api_base = settings.GROQ_API_BASE.rstrip("/")
        self.model_name = settings.GROQ_MODEL
        self.last_error = None

    def _known_keys(self):
        return [k for k in (self.api_key, settings.GEMINI_API_KEY,
                            settings.GROK_API_KEY) if k]

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _post(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("groq: not configured (no GROQ_API_KEY loaded)")
        response = requests.post(
            f"{self.api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json() or {}
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("No choices returned from Groq API")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError("Empty response content from Groq API")
        return content

    async def generate_text(self, prompt: str) -> str:
        if not self.api_key:
            detail = "groq: not configured (no GROQ_API_KEY loaded)"
            self.last_error = detail
            return f"{LM_UNAVAILABLE_PREFIX}:not_configured:{detail}"
        try:
            loop = asyncio.get_running_loop()
            response_text = await loop.run_in_executor(None, self._post, prompt)
            self.last_error = None
            return response_text
        except Exception as e:
            print(f"Error generating text with Groq: {e}")
            detail = sanitize_error(str(e), self._known_keys())
            self.last_error = detail
            kind = "rate_limited" if (
                "429" in detail or "rate_limit" in detail.lower()
                or "ResourceExhausted" in detail
            ) else "api_error"
            return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"


groq_client = GroqClient()
