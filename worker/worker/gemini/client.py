"""Gemini SDK wrapper.

Uses the new `google-genai` SDK with structured output (`response_schema` + `response_mime_type`)
to avoid the brittle ```json``` stripping we had before. Includes max-wait timeout and tenacity retry.

NOTE: `response_schema` is passed as a **flat dict** (raw JSON Schema), NOT a Pydantic class.
The google-genai SDK's `types.Schema.model_validate` is strict: it rejects
`exclusiveMinimum`, `$ref`, `$defs` — which Pydantic v2 emits by default. Hand-written
flat dicts work. We then validate the parsed dict against the Pydantic models in `schemas.py`
to keep Python-side guarantees (e.g. end_time > start_time).
"""
import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types as genai_types
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from worker.config import settings

logger = logging.getLogger("gemini_client")

# Exceptions that are safe to retry on
_RETRYABLE_EXC: tuple = (
    ConnectionError,
    TimeoutError,
)


class GeminiProcessingTimeout(Exception):
    pass


class GeminiResponseInvalid(Exception):
    """Raised when the model returns HTTP 200 but the body is not parseable
    (truncated, malformed JSON, missing required fields, etc.). Treated as
    a retryable condition by the fallback chain."""


class GeminiClient:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_id = settings.GEMINI_MODEL

    def upload_video(self, file_path: str):
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
                raise GeminiProcessingTimeout(
                    f"Gemini did not finish processing within {max_wait_seconds}s"
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            video_file = self.client.files.get(name=video_file.name)

        if "FAILED" in str(getattr(video_file, "state", "")):
            raise Exception("Gemini failed to process the video.")
        return video_file

    def generate_content(
        self,
        contents: List[Any],
        prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
        validator: Optional[Callable[[Any], None]] = None,
    ) -> str:
        """Generate content with multi-model fallback.

        If `response_schema` is provided, enforce JSON output. The schema must
        be a flat JSON-Schema dict (see schemas.py for examples).

        If `validator` is provided, it is called with the parsed JSON object
        and may raise `GeminiResponseInvalid` if the response is unacceptable
        (e.g. wrong shape, missing fields). This causes the fallback chain
        to try the next model.

        Behavior: tries the configured `GEMINI_MODEL` first; on any retryable
        failure (5xx, 429, network, 404 NOT_FOUND, malformed JSON) it walks
        the fallback chain defined by `settings.gemini_fallback_chain`. If
        the first model returns a parseable, valid answer, we return
        immediately — we never retry the same model twice within one call.
        """
        config: Dict[str, Any] = {}
        if response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_schema

        # The SDK accepts `contents` as a list of parts. We pass the file refs first,
        # then a Content object for the prompt. This is the canonical pattern for the new SDK.
        user_content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=prompt)],
        )
        full_contents = [*contents, user_content]

        @retry(
            retry=retry_if_exception_type(_RETRYABLE_EXC),
            stop=stop_after_attempt(settings.MAX_RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        def _call_with_retry(model_id: str) -> str:
            response = self.client.models.generate_content(
                model=model_id,
                contents=full_contents,
                config=config or None,
            )
            return response.text

        def _validate_response(text: str) -> str:
            """If a schema was requested, parse and validate. Raises
            GeminiResponseInvalid on any failure so the chain moves on."""
            if response_schema is None:
                return text
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GeminiResponseInvalid(
                    f"Malformed JSON from Gemini (will try next model): {exc}"
                ) from exc
            if validator is not None:
                try:
                    validator(data)
                except Exception as exc:  # noqa: BLE001
                    raise GeminiResponseInvalid(
                        f"Validator rejected Gemini response (will try next model): {exc}"
                    ) from exc
            return text

        # Multi-model fallback: try primary, then walk the chain on any failure
        # (transport error, malformed JSON, validator rejection).
        chain = settings.gemini_fallback_chain
        last_exc: Optional[Exception] = None
        for model_id in chain:
            try:
                logger.info("Gemini call using model %s", model_id)
                text = _call_with_retry(model_id)
                text = _validate_response(text)
                return text
            except GeminiResponseInvalid as exc:
                last_exc = exc
                logger.warning(
                    "Gemini model %s returned invalid response (%s); trying next",
                    model_id, exc,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Gemini model %s failed (%s); will try next in fallback chain if available",
                    model_id,
                    type(exc).__name__,
                )
                continue

        # All models exhausted
        logger.error("All Gemini models in fallback chain failed: %s", last_exc)
        raise last_exc  # type: ignore[misc]


gemini_client = GeminiClient()

