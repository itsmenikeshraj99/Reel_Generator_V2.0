from typing import List

from dotenv import load_dotenv
from pydantic import ConfigDict, field_validator
from pydantic_settings import BaseSettings

load_dotenv()

# Models that we know exist in the current Gemini API. Keep this small — validate at startup.
#
# NOTE (Sept 2026): gemini-2.0-flash, gemini-1.5-flash, gemini-2.5-flash, and
# gemini-2.5-flash-lite are all confirmed dead/blocked for new API keys (a
# live 404 from Google: "no longer available to new users"). Kept in sync
# with worker/worker/config.py's _PRIMARY_GEMINI_MODELS — update both lists
# together when Google's lineup changes.
_ALLOWED_GEMINI_MODELS: List[str] = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]


class Settings(BaseSettings):
    # Supabase (backend uses the service_role key; never expose this to the browser)
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_JWT_SECRET: str = ""  # used by services/auth.py if you choose HS256 verify
    STORAGE_BUCKET: str = "reels-videos"

    # Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.5-flash"

    # App
    MAX_VIDEO_SIZE_MB: int = 500
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    # Worker handshake
    WORKER_URL: str = "http://127.0.0.1:8001/process"
    WORKER_SHARED_SECRET: str = ""

    model_config = ConfigDict(env_file=".env", extra="ignore")

    @field_validator("GEMINI_MODEL")
    @classmethod
    def _validate_gemini_model(cls, v: str) -> str:
        if v not in _ALLOWED_GEMINI_MODELS:
            raise ValueError(
                f"GEMINI_MODEL={v!r} is not a known model. "
                f"Allowed: {_ALLOWED_GEMINI_MODELS}"
            )
        return v

    @property
    def frontend_origins(self) -> List[str]:
        """CORS allow-list. Comma-separated FRONTEND_ORIGIN supports multiple dev/preview URLs."""
        return [o.strip() for o in self.FRONTEND_ORIGIN.split(",") if o.strip()]


settings = Settings()
