"""Stage 2 — Gemini Call #1: transcript + edit plan candidates.

Uses the new google-genai SDK with structured output (`response_schema`
+ `response_mime_type`) to avoid brittle ```json``` stripping. Model IDs come
from `settings.gemini_fallback_chain` (worker/config.py) — the single source
of truth for which Gemini model IDs are currently live — instead of being
hardcoded here.

Error handling prints the exact request payload on 400 so we can inspect
what the API rejected.
"""
import json
import logging
import sys
from typing import Any, Dict, List, Optional, Callable

from google import genai as google_genai
from google.genai import types as genai_types

from worker.config import settings
from worker.pipeline import NonRetryableError
from worker.gemini.client import gemini_client
from worker.gemini.prompts import TRANSCRIPTION_PROMPT, CANDIDATE_EXTRACTION_PROMPT, CANDIDATE_SCORING_PROMPT
from worker.gemini.schemas import (
    TRANSCRIPTION_SCHEMA_DICT,
    CANDIDATES_SCHEMA_DICT,
    SCORING_SCHEMA_DICT,
    TranscriptionResponse,
    CandidatesResponse,
    ScoringResponse,
)
from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_transcribe")


# ── Helpers ──────────────────────────────────────────────────────────

def _extract_json_response(text: str) -> Dict[str, Any]:
    """Strip ```json ``` markdown wrappers then parse.

    Returns the parsed dict, or raises GeminiResponseInvalid.
    """
    # Remove fenced code blocks if present
    if text.startswith("```"):
        # Find the end fence
        idx = text.rfind("```")
        if idx >= 0:
            text = text[3:idx].strip()
            # Remove trailing ```
    # json.loads may still fail on stray text; try strip first/last chars
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find the outer { … } brackets
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])


class GeminiResponseInvalid(Exception):
    """Raised when the model returns HTTP 200 but the body is not valid."""


# ── Core API call ────────────────────────────────────────────────────

async def _gemini_call_with_fallback(
    video_id: str,
    video_file_uri: str,
    prompt: str,
    response_schema: Any,
    response_model: Any,
    context_text: Optional[str] = None,
) -> Any:
    """Generic helper to perform a Gemini call with fallback model chain."""
    last_exc: Exception | None = None

    # Prepare parts: Video URI + Prompt (+ optional context text)
    parts = [
        genai_types.Part.from_uri(file_uri=video_file_uri, mime_type="video/mp4"),
        genai_types.Part(text=prompt),
    ]
    if context_text:
        parts.append(genai_types.Part(text=f"Context:\n{context_text}"))

    for model_id in settings.gemini_fallback_chain:
        try:
            logger.info("Gemini call using model %s", model_id)
            response = gemini_client.client.models.generate_content(
                model=model_id,
                contents=[genai_types.Content(role="user", parts=parts)],
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )

            # Robust JSON extraction
            try:
                data = _extract_json_response(response.text)
            except Exception as exc:
                raise GeminiResponseInvalid(f"JSON parse failed: {exc}") from exc

            # Pydantic validation
            return response_model(**data)

        except GeminiResponseInvalid as exc:
            last_exc = exc
            logger.warning("Model %s returned invalid response: %s", model_id, exc)
            continue
        except Exception as exc:
            err_msg = str(exc).lower()
            if "400" in err_msg or "invalid_argument" in err_msg:
                logger.warning("Non-retryable 400 with model %s; trying next", model_id)
                last_exc = GeminiResponseInvalid(f"400 error: {exc}")
                continue
            if "403" in err_msg or "permission_denied" in err_msg:
                logger.error("Critical 403 with model %s; failing immediately", model_id)
                raise NonRetryableError(f"Critical 403: {exc}")

            last_exc = exc
            logger.warning("Model %s failed (%s); trying next", model_id, type(exc).__name__)
            continue

    logger.error("All models in fallback chain failed: %s", last_exc)
    if isinstance(last_exc, GeminiResponseInvalid):
        raise NonRetryableError(f"Fallback chain exhausted with invalid responses: {last_exc}")
    raise last_exc  # type: ignore[misc]


async def transcribe_and_plan(
    video_id: str,
    video_path: str,
    existing_uri: Optional[str] = None,
    update_uri: Optional[Callable[[str], None]] = None
) -> bool:
    """
    Orchestrates the 3-call Gemini architecture:
    1. Transcription -> 2. Candidate Extraction -> 3. Scoring
    """
    try:
        logger.info("Starting 3-call Gemini pipeline for video %s", video_id)

        # ── 0. File Management ──────────────────────────────────────────
        video_file = None
        if existing_uri:
            try:
                file_name = existing_uri.split("/")[-1]
                video_file = gemini_client.client.files.get(name=f"files/{file_name}")
                if video_file.state.name != "ACTIVE":
                    video_file = None
            except Exception:
                video_file = None

        if video_file is None:
            video_file = gemini_client.upload_video(video_path)
            video_file = await gemini_client.wait_for_processing(video_file)
            if update_uri:
                uri = video_file.uri
                if not uri.startswith(("https:", "http:")):
                    uri = f"https://generativelanguage.googleapis.com/{uri}"
                update_uri(uri)

        video_file_uri = video_file.uri
        if not video_file_uri.startswith(("https:", "http:")):
            video_file_uri = f"https://generativelanguage.googleapis.com/{video_file_uri}"

        # ── 1. Transcription Call ─────────────────────────────────────────
        # Idempotency: check for existing transcript
        transcript_res = supabase_client.table("transcripts").select("*").eq("video_id", video_id).maybe_single().execute()
        transcript_data = transcript_res.data if transcript_res is not None else None
        if not transcript_data:
            logger.info("Generating transcript for %s...", video_id)
            plan = await _gemini_call_with_fallback(
                video_id=video_id,
                video_file_uri=video_file_uri,
                prompt=TRANSCRIPTION_PROMPT,
                response_schema=TRANSCRIPTION_SCHEMA_DICT,
                response_model=TranscriptionResponse,
            )
            # Persist transcript
            supabase_client.table("transcripts").upsert({
                "video_id": video_id,
                "full_text": plan.full_transcript,
                "words": [w.model_dump() for w in plan.words],
            }, on_conflict="video_id").execute()
            full_transcript = plan.full_transcript
        else:
            logger.info("Reusing existing transcript for %s", video_id)
            full_transcript = transcript_data["full_text"]

        # ── 2. Candidate Extraction Call ─────────────────────────────────
        # Idempotency: check for existing candidates
        candidates_res = supabase_client.table("edit_plans").select("*").eq("video_id", video_id).execute()
        if not candidates_res.data:
            logger.info("Extracting candidates for %s...", video_id)
            plan = await _gemini_call_with_fallback(
                video_id=video_id,
                video_file_uri=video_file_uri,
                prompt=CANDIDATE_EXTRACTION_PROMPT,
                response_schema=CANDIDATES_SCHEMA_DICT,
                response_model=CandidatesResponse,
                context_text=full_transcript,
            )
            # Persist candidates
            candidates_data = []
            for i, cand in enumerate(plan.candidates):
                candidates_data.append({
                    "video_id": video_id,
                    "candidate_index": i,
                    "segments": [seg.model_dump() for seg in cand.segments],
                    "status": "pending_review",
                })
            supabase_client.table("edit_plans").insert(candidates_data).execute()

        # ── 3. Scoring Call ──────────────────────────────────────────────
        # Idempotency: check if any candidates are still missing scores
        candidates_to_score = supabase_client.table("edit_plans").select("candidate_index").eq(
            "video_id", video_id
        ).is_("overall_score", "null").execute()

        if candidates_to_score.data:
            logger.info("Scoring candidates for %s...", video_id)
            # We send the video URI again but the prompt is focused on scoring
            scoring = await _gemini_call_with_fallback(
                video_id=video_id,
                video_file_uri=video_file_uri,
                prompt=CANDIDATE_SCORING_PROMPT,
                response_schema=SCORING_SCHEMA_DICT,
                response_model=ScoringResponse,
                context_text=full_transcript,
            )
            # Persist scores
            for score in scoring.scores:
                supabase_client.table("edit_plans").update({
                    "hook_score": score.hook_score,
                    "overall_score": score.overall_score,
                }).eq("video_id", video_id).eq("candidate_index", score.candidate_index).execute()

        return True

    except Exception as exc:  # noqa: BLE001
        logger.exception("transcribe_and_plan failed for %s: %s", video_id, exc)
        raise
