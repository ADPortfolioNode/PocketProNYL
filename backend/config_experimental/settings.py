# File Location: /app/config/settings.py
import os

class Settings:
    PROJECT_NAME: str = "PocketProNYGames"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "chroma")
    CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))

settings = Settings()