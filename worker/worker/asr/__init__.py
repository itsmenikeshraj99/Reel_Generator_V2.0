"""Speech-to-text adapter: pick a provider, with a Gemini safety net.

Not wired into the pipeline yet (planned for the smart-pipeline step).
"""
import logging
from typing import Any, Dict, Optional, Tuple

from worker.asr.base import ASRError, ASRProvider, Segment, TranscriptResult, Word

__all__ = [
    "ASRError", "ASRProvider", "Segment", "TranscriptResult", "Word",
    "get_asr_provider", "transcribe_with_fallback",
]

logger = logging.getLogger("asr")


def get_asr_provider(name: Optional[str] = None) -> ASRProvider:
    """Build the provider named by `ASR_PROVIDER` (or `name`)."""
    from worker.config import settings

    provider = (name or settings.ASR_PROVIDER).lower()
    if provider == "groq":
        if not settings.GROQ_API_KEY:
            raise ASRError("ASR_PROVIDER is groq but GROQ_API_KEY is not set")
        from worker.asr.groq_provider import GroqProvider
        return GroqProvider(api_key=settings.GROQ_API_KEY, model=settings.GROQ_ASR_MODEL)
    if provider == "gemini":
        from worker.asr.gemini_provider import GeminiProvider
        return GeminiProvider()
    raise ASRError(f"Unknown ASR provider: {provider!r}")


async def transcribe_with_fallback(
    video_path: str,
    primary: Optional[ASRProvider] = None,
    fallback: Optional[ASRProvider] = None,
) -> Tuple[TranscriptResult, Dict[str, Any]]:
    """Transcribe with the configured provider; on any failure use Gemini.

    Returns (result, meta). `meta` records which provider produced the
    text so it can be stored in `reels.meta` later.
    """
    meta: Dict[str, Any] = {"provider": None, "fallback_used": False, "primary_error": None}
    primary_name = "primary"
    try:
        primary = primary or get_asr_provider()
        primary_name = primary.name
        result = await primary.transcribe(video_path)
        meta["provider"] = primary.name
        return result, meta
    except Exception as exc:  # noqa: BLE001
        meta["primary_error"] = str(exc)[:300]
        if primary_name == "gemini":
            raise
        logger.warning("ASR provider %s failed (%s); falling back to Gemini", primary_name, exc)

    fallback = fallback or get_asr_provider("gemini")
    result = await fallback.transcribe(video_path)
    meta["provider"] = fallback.name
    meta["fallback_used"] = True
    return result, meta
