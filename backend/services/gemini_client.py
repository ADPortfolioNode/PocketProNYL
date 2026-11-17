import asyncio
import google.generativeai as genai
import requests
from config import settings
from services.llm_common import USER_AGENT, sanitize_error

LM_UNAVAILABLE_PREFIX = "__LM_UNAVAILABLE__"


def _openai_chat_post(api_base, api_key, model_name, prompt, timeout=30):
    """One OpenAI-compatible chat-completions POST with a real User-Agent.

    Shared by the xAI Grok rung here (and mirrored in groq_client.py /
    ollama_client.py, which cannot import from this module without a
    circular import through gemini_client). Previously no User-Agent was
    set at all; Cloudflare in front of Groq rejects default bot-like
    clients with 403 error code 1010, so every hand-rolled call sends
    PocketPro/1.0.
    """
    response = requests.post(
        f"{api_base.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        json={
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json() or {}
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("No choices returned from Grok API")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        raise RuntimeError("Empty response content from Grok API")
    return content


class GrokClient:
    """xAI Grok client (api.x.ai). This is xAI Grok, NOT Groq.

    GROK_* settings belong to xAI and must never be merged with the new
    GROQ_* (Groq) settings in groq_client.py. The router registers this
    as the separate "grok" rung; GeminiClient's legacy composite path
    still uses _generate_with_grok below for direct callers.
    """

    def __init__(self):
        self.api_key = settings.GROK_API_KEY
        self.api_base = settings.GROK_API_BASE.rstrip("/")
        self.model_name = settings.GROK_MODEL
        self.last_error = None

    def _known_keys(self):
        return [k for k in (self.api_key, settings.GEMINI_API_KEY) if k]

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _post(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("Grok API key not configured")
        return _openai_chat_post(self.api_base, self.api_key,
                                 self.model_name, prompt)

    async def generate_text(self, prompt: str) -> str:
        if not self.api_key:
            detail = "grok: not configured (no GROK_API_KEY loaded)"
            self.last_error = detail
            return f"{LM_UNAVAILABLE_PREFIX}:not_configured:{detail}"
        try:
            loop = asyncio.get_running_loop()
            response_text = await loop.run_in_executor(None, self._post, prompt)
            self.last_error = None
            return response_text
        except Exception as e:
            print(f"Error generating text with Grok: {e}")
            detail = sanitize_error(str(e), self._known_keys())
            self.last_error = detail
            kind = "rate_limited" if (
                "429" in detail or "RATE_LIMIT_EXCEEDED" in detail
                or "ResourceExhausted" in detail
            ) else "api_error"
            return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"


grok_client = GrokClient()


class GeminiClient:
    """
    Client for interacting with Google's Gemini API.

    generate_text() keeps its legacy composite behaviour (Gemini, then
    xAI Grok) for existing direct callers such as RAGService's default
    path. The router does NOT use that path: it calls
    generate_text(prompt, gemini_only=True) for the "gemini" rung and the
    separate GrokClient for the "grok" rung, so the reported provider is
    the provider that actually answered.
    """

    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self.grok_api_key = settings.GROK_API_KEY
        self.grok_api_base = settings.GROK_API_BASE.rstrip("/")
        self.grok_model = settings.GROK_MODEL
        self._gemini_model = None
        self.last_error = None

    def _get_gemini_model(self):
        if self._gemini_model is None and self.api_key:
            genai.configure(api_key=self.api_key)
            self._gemini_model = genai.GenerativeModel(self.model_name)
        return self._gemini_model

    def _known_keys(self):
        return [k for k in (self.api_key, self.grok_api_key) if k]

    def is_available(self) -> bool:
        # Check if Gemini model can be initialized or Grok is available
        return bool((self.api_key and self._get_gemini_model()) or self.grok_api_key)

    def _generate_with_grok(self, prompt: str) -> str:
        if not self.grok_api_key:
            raise RuntimeError("Grok API key not configured")
        return _openai_chat_post(self.grok_api_base, self.grok_api_key,
                                 self.grok_model, prompt)

    async def _generate_gemini_only(self, prompt: str) -> str:
        gemini_model = self._get_gemini_model()
        if not self.api_key or not gemini_model:
            detail = "gemini: not configured (no GEMINI_API_KEY loaded)"
            self.last_error = detail
            return f"{LM_UNAVAILABLE_PREFIX}:not_configured:{detail}"
        try:
            # Run synchronous SDK call in an executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: gemini_model.generate_content(prompt)
            )
            self.last_error = None
            return response.text
        except Exception as e:
            print(f"Error generating text with Gemini: {e}")
            detail = sanitize_error(str(e), self._known_keys())
            self.last_error = detail
            kind = "rate_limited" if (
                "429" in detail or "RATE_LIMIT_EXCEEDED" in detail
                or "ResourceExhausted" in detail
            ) else "api_error"
            return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"

    async def generate_text(self, prompt: str, gemini_only: bool = False) -> str:
        if gemini_only:
            return await self._generate_gemini_only(prompt)

        if not self.is_available():
            return f"{LM_UNAVAILABLE_PREFIX}:missing_key:No LM provider configured. Set GEMINI_API_KEY or GROK_API_KEY."

        gemini_result = await self._generate_gemini_only(prompt)
        if not gemini_result.startswith(LM_UNAVAILABLE_PREFIX):
            return gemini_result

        if self.grok_api_key:
            try:
                # _generate_with_grok uses synchronous requests, run in executor
                loop = asyncio.get_running_loop()
                response_text = await loop.run_in_executor(
                    None, self._generate_with_grok, prompt
                )
                self.last_error = None
                return response_text
            except Exception as e:
                print(f"Error generating text with Grok: {e}")
                detail = sanitize_error(str(e), self._known_keys())
                self.last_error = detail
                kind = "rate_limited" if (
                    "429" in detail or "RATE_LIMIT_EXCEEDED" in detail
                    or "ResourceExhausted" in detail
                ) else "api_error"
                return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"

        return gemini_result


gemini_client = GeminiClient()
