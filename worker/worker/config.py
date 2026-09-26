import os
from typing import List

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

# Models we accept in GEMINI_MODEL (primary). Everything in the fallback chain
# is also valid as a primary.
#
# NOTE (updated Sept 2026, from a live failure log): gemini-2.5-flash and
# gemini-2.5-flash-lite now return 404 "no longer available to new users"
# on fresh API keys — Google has moved new projects to the 3.x generation.
# gemini-2.0-flash / gemini-1.5-flash were retired earlier in 2026 and
# must never be used. Confirmed-GA 3.x models as of Sept 2026:
_PRIMARY_GEMINI_MODELS: List[str] = [
    "gemini-3.5-flash",           # GA since May 2026 — current flagship flash
    "gemini-3.1-flash-lite",      # GA, stable low-latency option
    "gemini-3.5-flash-lite",      # GA since Jul 2026
    "gemini-3.6-flash",           # GA since Jul 2026 — cheaper/more efficient than 3.5
]

# Fallback chain used by the worker when the primary fails. Order matters —
# first = best fit, last = most expensive fallback.
#
# Do NOT add gemini-2.0-flash, gemini-1.5-flash, gemini-2.5-flash, or
# gemini-2.5-flash-lite back here — all four are confirmed dead/blocked
# for new API keys as of Sept 2026 (see note above).
DEFAULT_FALLBACK_CHAIN: List[str] = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]


class WorkerSettings(BaseSettings):
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_JWT_SECRET: str = ""
    STORAGE_BUCKET: str = "reels-videos"

    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-3.5-flash"
    # Comma-separated list of fallback models. Empty = use the built-in chain.
    GEMINI_FALLBACK_MODELS: str = ""

    WORKER_SHARED_SECRET: str = ""
    WORKER_HOST: str = "127.0.0.1"
    WORKER_PORT: int = 8001

    MAX_RETRY_ATTEMPTS: int = 3
    # Cap on how many times the reviewer can ask the planner to regenerate
    # before we fall back to the best hook_score candidate. Set to 0 to
    # disable the revise loop entirely (single-pass review).
    MAX_REVISE_ATTEMPTS: int = 2

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("GEMINI_MODEL")
    @classmethod
    def _validate_gemini_model(cls, v: str) -> str:
        if v not in _PRIMARY_GEMINI_MODELS:
            raise ValueError(
                f"GEMINI_MODEL={v!r} is not a known model. "
                f"Allowed: {_PRIMARY_GEMINI_MODELS}"
            )
        return v

    @field_validator("WORKER_HOST")
    @classmethod
    def _block_public_bind_unless_explicit(cls, v: str) -> str:
        if v == "0.0.0.0" and not os.getenv("WORKER_SHARED_SECRET"):
            raise ValueError(
                "Refusing to bind 0.0.0.0 without WORKER_SHARED_SECRET. "
                "Set WORKER_SHARED_SECRET in the worker env, or bind to 127.0.0.1."
            )
        return v

    @property
    def gemini_fallback_chain(self) -> List[str]:
        """Ordered list of Gemini models to try on failure. The primary
        GEMINI_MODEL is implicitly the first entry; we never retry the same
        model twice. Set GEMINI_FALLBACK_MODELS to override the chain."""
        raw = self.GEMINI_FALLBACK_MODELS.strip()
        chain = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_FALLBACK_CHAIN)
        # Primary first (if not already in chain), then the configured chain
        # without the primary (avoid duplicates).
        if self.GEMINI_MODEL not in chain:
            chain = [self.GEMINI_MODEL] + chain
        else:
            chain = [self.GEMINI_MODEL] + [m for m in chain if m != self.GEMINI_MODEL]
        return chain


def _ensure_ffmpeg_on_path() -> None:
    """Prepend common ffmpeg install locations to PATH so child processes
    (ffmpeg, ffprobe) can find the binaries regardless of how the worker was
    launched."""
    import shutil
    from pathlib import Path

    candidates = [
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.0.1-full_build" / "bin",
        Path("C:/Program Files/ffmpeg/bin"),
        Path("C:/ffmpeg/bin"),
        Path("C:/ProgramData/chocolatey/bin"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        Path("/usr/bin"),
    ]
    extra: list[str] = []
    for c in candidates:
        try:
            if not c.exists():
                continue
            ffmpeg_bin = c / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            ffprobe_bin = c / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if ffmpeg_bin.exists() and ffprobe_bin.exists():
                extra.append(str(c))
        except OSError:
            continue

    if extra:
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(extra + [current_path]) if current_path else os.pathsep.join(extra)

    if shutil.which("ffprobe") is None:
        import logging
        logging.getLogger("worker_config").warning(
            "ffprobe not found on PATH. Install ffmpeg "
            "(e.g. 'winget install Gyan.FFmpeg') and ensure worker is restarted."
        )


_ensure_ffmpeg_on_path()


settings = WorkerSettings()
