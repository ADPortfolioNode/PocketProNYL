import asyncio

import requests

from config import settings
from services.gemini_client import LM_UNAVAILABLE_PREFIX
from services.llm_common import USER_AGENT, sanitize_error


class OllamaClient:
    """Local Ollama client via its OpenAI-compatible endpoint.

    This rung is active ONLY when OLLAMA_ENABLED is set (truthy). When it
    is off, the client reports not-configured and the chain moves on --
    Ollama must never block or slow down a deployment that does not run
    it. Default base is http://host.docker.internal:11434/v1 so a
    containerised backend can reach Ollama on the Docker host.
    """

    def __init__(self):
        self.enabled = bool(settings.OLLAMA_ENABLED)
        self.api_base = settings.OLLAMA_API_BASE.rstrip("/")
        self.model_name = settings.OLLAMA_MODEL
        self.last_error = None

    def is_available(self) -> bool:
        return bool(self.enabled)

    def _post(self, prompt: str) -> str:
        if not self.enabled:
            raise RuntimeError(
                "ollama: not configured (OLLAMA_ENABLED is not set)")
        response = requests.post(
            f"{self.api_base}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json() or {}
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("No choices returned from Ollama API")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError("Empty response content from Ollama API")
        return content

    async def generate_text(self, prompt: str) -> str:
        if not self.enabled:
            detail = "ollama: not configured (OLLAMA_ENABLED is not set)"
            self.last_error = detail
            return f"{LM_UNAVAILABLE_PREFIX}:not_configured:{detail}"
        try:
            loop = asyncio.get_running_loop()
            response_text = await loop.run_in_executor(None, self._post, prompt)
            self.last_error = None
            return response_text
        except Exception as e:
            print(f"Error generating text with Ollama: {e}")
            detail = sanitize_error(str(e), [])
            self.last_error = detail
            return f"{LM_UNAVAILABLE_PREFIX}:api_error:{detail}"


ollama_client = OllamaClient()
