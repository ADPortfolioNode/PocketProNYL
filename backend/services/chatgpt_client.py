import asyncio
from openai import OpenAI

from config import settings
from services.gemini_client import LM_UNAVAILABLE_PREFIX
from services.llm_common import sanitize_error


class ChatGPTClient:
    """
    Client for interacting with OpenAI Chat Completions API.
    """

    def __init__(self):
        self.api_key = settings.CHAT_GPT_API_KEY or settings.OPENAI_API_KEY
        # Previously hardcoded to gpt-4o-mini; now overridable via
        # CHATGPT_MODEL in .env (default stays gpt-4o-mini).
        self.model_name = settings.CHATGPT_MODEL
        self.client = None
        self.init_error = None
        self.last_error = None

    def _known_keys(self):
        return [k for k in (self.api_key,) if k]

    def is_available(self) -> bool:
        # Attempt to get client to check if it can be initialized without error
        return self._get_client() is not None

    def _get_client(self):
        if self.client is not None:
            return self.client
        if not self.api_key:
            return None
        try:
            self.client = OpenAI(api_key=self.api_key)
            self.init_error = None
            return self.client
        except Exception as e:
            self.init_error = str(e)
            print(f"Error initializing OpenAI client: {e}")
            self.client = None # Ensure client is reset if init fails
            return None # Return None on failure

    async def generate_text(self, prompt: str) -> str:
        client = self._get_client()
        if not client:
            detail = sanitize_error(
                self.init_error or "chatgpt: not configured "
                "(no CHAT_GPT_API_KEY/OPENAI_API_KEY loaded)",
                self._known_keys())
            self.last_error = detail
            kind = ("not_configured" if not self.api_key else "api_error")
            return f"{LM_UNAVAILABLE_PREFIX}:{kind}:{detail}"

        try:
            # The 'create' method is synchronous.
            # We need to run it in an executor to avoid blocking the event loop.
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
            )
            text = (response.choices[0].message.content or "").strip()
            self.last_error = None
            return text
        except Exception as e:
            print(f"Error generating text with ChatGPT: {e}")
            detail = sanitize_error(str(e), self._known_keys())
            self.last_error = detail
            if "429" in detail or "rate_limit" in detail.lower() or "quota" in detail.lower():
                return f"{LM_UNAVAILABLE_PREFIX}:rate_limited:{detail}"
            return f"{LM_UNAVAILABLE_PREFIX}:api_error:{detail}"


chatgpt_client = ChatGPTClient()
