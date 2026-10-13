"""Groq Whisper speech-to-text (OpenAI-compatible transcription endpoint)."""
import asyncio
import logging
import os
from typing import Any, Dict, Optional

import httpx

from worker.asr.audio import extract_audio, remove_quietly
from worker.asr.base import ASRError, Segment, TranscriptResult, Word

logger = logging.getLogger("asr_groq")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_MAX_RETRY_AFTER_S = 30.0


def parse_response(data: Dict[str, Any]) -> TranscriptResult:
    """Convert Groq's `verbose_json` body into a TranscriptResult."""
    full_text = (data.get("text") or "").strip()

    words = []
    for w in data.get("words") or []:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        start, end = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if end < start:
            start, end = end, start
        words.append(Word(text=text, start=start, end=end))

    segments = []
    for s in data.get("segments") or []:
        text = (s.get("text") or "").strip()
        if text:
            segments.append(Segment(text=text, start=float(s.get("start", 0.0)),
                                    end=float(s.get("end", 0.0))))

    if full_text and not words:
        raise ASRError("Groq returned text but no word timestamps")

    duration = float(data.get("duration") or 0.0) or (words[-1].end if words else 0.0)
    return TranscriptResult(
        provider="groq",
        full_text=full_text,
        words=words,
        segments=segments,
        language=str(data.get("language") or ""),
        duration=duration,
    )


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_attempts: int = 3,
        backoff_s: float = 2.0,
        timeout_s: float = 300.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._timeout_s = timeout_s
        self._transport = transport

    async def transcribe(self, video_path: str) -> TranscriptResult:
        audio_path = await extract_audio(video_path)
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
        finally:
            remove_quietly(audio_path)
        data = await self._post_with_retry(os.path.basename(audio_path), audio_bytes)
        return parse_response(data)

    async def _post_with_retry(self, filename: str, audio_bytes: bytes) -> Dict[str, Any]:
        last_error = "unknown error"
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            for attempt in range(1, self._max_attempts + 1):
                retry_after: Optional[float] = None
                try:
                    resp = await client.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        files={"file": (filename, audio_bytes, "audio/mpeg")},
                        data={
                            "model": self._model,
                            "response_format": "verbose_json",
                            "timestamp_granularities[]": ["word", "segment"],
                        },
                    )
                except httpx.TransportError as exc:
                    last_error = f"network error ({type(exc).__name__})"
                else:
                    if resp.status_code == 200:
                        try:
                            return resp.json()
                        except ValueError as exc:
                            raise ASRError("Groq returned a non-JSON body") from exc
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if resp.status_code not in _RETRY_STATUSES:
                        raise ASRError(f"Groq request rejected ({last_error})")
                    try:
                        retry_after = min(float(resp.headers.get("retry-after", "")), _MAX_RETRY_AFTER_S)
                    except ValueError:
                        retry_after = None

                if attempt < self._max_attempts:
                    wait = retry_after if retry_after is not None else self._backoff_s * 2 ** (attempt - 1)
                    logger.warning("Groq attempt %d/%d failed (%s); retrying in %.1fs",
                                   attempt, self._max_attempts, last_error, wait)
                    await asyncio.sleep(wait)
        raise ASRError(f"Groq failed after {self._max_attempts} attempts ({last_error})")
