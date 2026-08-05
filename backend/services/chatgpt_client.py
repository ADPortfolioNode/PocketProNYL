from openai import OpenAI

from config import settings
from services.gemini_client import LM_UNAVAILABLE_PREFIX


class ChatGPTClient:
    """
    Client for interacting with OpenAI Chat Completions API.
    """

    def __init__(self):
        self.api_key = settings.CHAT_GPT_API_KEY or settings.OPENAI_API_KEY
        self.model_name = "gpt-4o-mini"
        self.client = None
        self.init_error = None

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
            error_text = self.init_error or "ChatGPT client initialization failed"
            return f"{LM_UNAVAILABLE_PREFIX}:api_error:{error_text[:220]}"

        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
            )
            text = (response.choices[0].message.content or "").strip()
            return text
        except Exception as e:
            print(f"Error generating text with ChatGPT: {e}")
            error_text = str(e)
            if "429" in error_text or "rate_limit" in error_text.lower() or "quota" in error_text.lower():
                return f"{LM_UNAVAILABLE_PREFIX}:rate_limited:{error_text[:220]}"
            return f"{LM_UNAVAILABLE_PREFIX}:api_error:{error_text[:220]}"


chatgpt_client = ChatGPTClient()
