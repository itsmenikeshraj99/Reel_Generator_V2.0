"""Gemini SDK wrapper for the reels-generator worker.

Uses the new `google-genai` SDK with structured output (`response_schema`
+ `response_mime_type`) to avoid brittle ```json``` stripping.

Only uses confirmed-free-tier model IDs. Falls back from gemini-2.0-flash
to gemini-1.5-flash on any retryable failure.

All `generate_content` calls expect `contents` as a list where each element
is either a `Part` (media reference) or a `Content` object — never nested
`Content(role="user")` which the Gemini API strictly rejects.
"""
import json
import logging
import asyncio
from typing import Any, List, Optional

from google import genai as google_genai
from google.genai import types as genai_types

from worker.config import settings


logger = logging.getLogger("gemini_client")


class GeminiResponseInvalid(Exception):
    """Raised when the model returns HTTP 200 but the body is not valid."""


# ── Model IDs ──────────────────────────────────────────────────────
# Confirmed to exist on free tier as of Sept 2026.
# Never use gemini-3.1-pro-preview, gemini-3.5-flash, etc. — they return 404.
GEMINI_PRIMARY = "gemini-2.0-flash"
GEMINI_FALLBACK = "gemini-1.5-flash"


# ── Public API ─────────────────────────────────────────────────────

class GeminiClient:
    """Wrapper around the google-genai SDK client."""

    def __init__(self) -> None:
        self.client = google_genai.Client(api_key=settings.GEMINI_API_KEY)

    # ── Video management ───────────────────────────────────────────

    def upload_video(self, file_path: str):
        """Upload a video file to Gemini Files API.

        Returns the File object (has .uri, .mime_type, .state attributes).
        """
        logger.info("Uploading %s to Gemini…", file_path)
        try:
            return self.client.files.upload(file=file_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Gemini upload failed: %s", exc)
            raise

    async def wait_for_processing(self, video_file, max_wait_seconds: int = 600):
        """Poll until Gemini marks the file ACTIVE, or until max_wait_seconds elapses."""
        elapsed = 0
        poll_interval = 2
        while True:
            state = str(getattr(video_file, "state", ""))
            if "PROCESSING" not in state:
                break
            if elapsed >= max_wait_seconds:
                raise RuntimeError(
                    f"Gemini did not finish processing within {max_wait_seconds}s"
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            video_file = self.client.files.get(name=video_file.name)

        if "FAILED" in state:
            raise RuntimeError("Gemini failed to process the video.")
        return video_file

    # ── Content generation ─────────────────────────────────────────

    def generate_content(
        self,
        contents: List[Any],
        prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate content with multi-model fallback.

        Args:
            contents: List of Part/Content objects. Must NOT contain
                      nested Content(role="user") — the SDK adds one
                      automatically below.
            prompt: Text prompt to append.
            response_schema: Flat JSON-Schema dict for structured output.

        Returns:
            The model's text response.

        Raises:
            GeminiResponseInvalid: If the model returns a parseable 200
                                   but the body is not valid JSON/schema.
        """
        config: Dict[str, Any] = {}
        if response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_schema

        last_exc: Optional[Exception] = None

        for model_id in [GEMINI_PRIMARY, GEMINI_FALLBACK]:
            try:
                logger.info("Gemini call using model %s", model_id)

                response = self.client.models.generate_content(
                    model=model_id,
                    contents=contents,          # passed as-is; SDK appends prompt
                    config=config or None,
                )

                raw_text = response.text

                # Parse the JSON response
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError:
                    # Strip ```json ``` markdown if the model returned text
                    if raw_text.startswith("```"):
                        idx = raw_text.rfind("```")
                        if idx >= 0:
                            raw_text = raw_text[3:idx].strip()
                            data = json.loads(raw_text)
                        else:
                            raise
                    else:
                        raise

                return raw_text

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Gemini model %s failed (%s); trying next in fallback chain",
                    model_id,
                    type(exc).__name__,
                )
                continue

        # All models exhausted
        logger.error("All Gemini models in fallback chain failed: %s", last_exc)
        raise last_exc  # type: ignore[misc]


# Singleton instance imported by the worker pipeline (worker/pipeline.py:25)
gemini_client = GeminiClient()