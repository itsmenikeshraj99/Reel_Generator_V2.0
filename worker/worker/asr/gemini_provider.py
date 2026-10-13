"""Fallback provider: the existing Gemini transcription call.

Kept only as a safety net. Gemini writes the whole word-timed transcript
in one answer, which can be cut off on long videos (observed at ~9 min).
"""
import asyncio
import logging

from worker.asr.base import ASRError, TranscriptResult, Word

logger = logging.getLogger("asr_gemini")


class GeminiProvider:
    name = "gemini"

    async def transcribe(self, video_path: str) -> TranscriptResult:
        # Imported here so merely importing worker.asr stays light and
        # avoids a circular import with worker.pipeline.
        from worker.gemini.client import gemini_client
        from worker.gemini.prompts import TRANSCRIPTION_PROMPT
        from worker.gemini.schemas import TRANSCRIPTION_SCHEMA_DICT, TranscriptionResponse
        from worker.stages.transcribe_plan import _gemini_call_with_fallback

        video_file = None
        try:
            video_file = await asyncio.to_thread(gemini_client.upload_video, video_path)
            video_file = await gemini_client.wait_for_processing(video_file)
            uri = video_file.uri
            if not uri.startswith(("https:", "http:")):
                uri = f"https://generativelanguage.googleapis.com/{uri}"
            plan = await _gemini_call_with_fallback(
                video_id="asr",
                video_file_uri=uri,
                prompt=TRANSCRIPTION_PROMPT,
                response_schema=TRANSCRIPTION_SCHEMA_DICT,
                response_model=TranscriptionResponse,
            )
        except Exception as exc:  # noqa: BLE001
            raise ASRError(f"Gemini transcription failed: {exc}") from exc
        finally:
            if video_file is not None and getattr(video_file, "name", None):
                try:
                    gemini_client.client.files.delete(name=video_file.name)
                except Exception:  # noqa: BLE001
                    pass

        words = [Word(text=w.text, start=w.start, end=w.end) for w in plan.words]
        return TranscriptResult(
            provider="gemini",
            full_text=plan.full_transcript,
            words=words,
            duration=words[-1].end if words else 0.0,
        )
