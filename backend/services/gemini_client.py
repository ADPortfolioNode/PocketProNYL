import google.generativeai as genai
import requests
from config import settings

LM_UNAVAILABLE_PREFIX = "__LM_UNAVAILABLE__"

class GeminiClient:
    """
    Client for interacting with Google's Gemini API.
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

    def is_available(self) -> bool:
        # Check if Gemini model can be initialized or Grok is available
        return bool((self.api_key and self._get_gemini_model()) or self.grok_api_key)

    def _generate_with_grok(self, prompt: str) -> str:
        if not self.grok_api_key:
            raise RuntimeError("Grok API key not configured")

        response = requests.post(
            f"{self.grok_api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.grok_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.grok_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=30,
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

    async def generate_text(self, prompt: str) -> str:
        if not self.is_available():
            return f"{LM_UNAVAILABLE_PREFIX}:missing_key:No LM provider configured. Set GEMINI_API_KEY or GROK_API_KEY."

        gemini_model = self._get_gemini_model()
        gemini_error_text = "" # Initialize here to ensure it's always defined
        if self.api_key and gemini_model:
            try:
                response = gemini_model.generate_content(prompt)
                self.last_error = None
                return response.text
            except Exception as e:
                print(f"Error generating text with Gemini: {e}")
                self.last_error = str(e)
                gemini_error_text = str(e)

        if self.grok_api_key:
            try:
                response_text = self._generate_with_grok(prompt)
                self.last_error = None
                return response_text
            except Exception as e:
                print(f"Error generating text with Grok: {e}")
                self.last_error = str(e)
                error_text = str(e)
                if "429" in error_text or "RATE_LIMIT_EXCEEDED" in error_text or "ResourceExhausted" in error_text:
                    return f"{LM_UNAVAILABLE_PREFIX}:rate_limited:{error_text[:220]}"
                return f"{LM_UNAVAILABLE_PREFIX}:api_error:{error_text[:220]}"

        if "429" in gemini_error_text or "RATE_LIMIT_EXCEEDED" in gemini_error_text or "ResourceExhausted" in gemini_error_text:
            return f"{LM_UNAVAILABLE_PREFIX}:rate_limited:{gemini_error_text[:220]}"
        if gemini_error_text:
            return f"{LM_UNAVAILABLE_PREFIX}:api_error:{gemini_error_text[:220]}"
        return f"{LM_UNAVAILABLE_PREFIX}:api_error:All configured LM providers are unavailable"

gemini_client = GeminiClient()
