"""Stage 2 — Gemini Call #1: transcript + edit plan candidates.

Uses the new google-genai SDK with structured output (`response_schema`
+ `response_mime_type`) to avoid brittle ```json``` stripping. Only uses
confirmed-free-tier models: gemini-2.0-flash primary, gemini-1.5-flash fallback.

Error handling prints the exact request payload on 400 so we can inspect
what the API rejected.
"""
import json
import logging
import sys
from typing import Any, Dict, List

from google import genai as google_genai
from google.genai import types as genai_types

from worker.gemini.client import gemini_client
from worker.gemini.prompts import TRANSCRIPTION_PLANNING_PROMPT
from worker.gemini.schemas import TRANSCRIPT_PLAN_SCHEMA_DICT, TranscriptPlanResponse
from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_transcribe")


# ── Model configuration ──────────────────────────────────────────────
# Confirmed free-tier models only. Do NOT use models that return 404/400.
GEMINI_PRIMARY = "gemini-2.0-flash"
GEMINI_FALLBACK = "gemini-1.5-flash"


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

async def transcribe_and_plan(video_id: str, video_path: str) -> bool:
    """Upload video to Gemini, generate transcript + plan candidates."""

    try:
        logger.info("Generating transcript and plan for video %s", video_id)

        # Upload + wait for ACTIVE
        video_file = gemini_client.upload_video(video_path)
        video_file = await gemini_client.wait_for_processing(video_file)

        # ── Build contents: bare Part referencing the uploaded file + prompt ──
        # The SDK expects `contents` as a list where each element is either:
        #   • a Part (for media references), or
        #   • a Content object.
        # We pass a single Part.from_uri() that references the file uploaded
        # by upload_video(). The SDK will pair it with the prompt parameter
        # automatically — no nested Content(role="user") objects.
        #
        # CRITICAL: video_file.uri from upload_video() is a relative path like
        # "files/ABC123". Part.from_uri() requires a canonical URL, so we
        # prepend the Google AI File API base if needed.
        video_file_uri = video_file.uri
        if not video_file_uri.startswith(("https:", "http:")):
            video_file_uri = f"https://generativelanguage.googleapis.com/{video_file_uri}"

        part_video = genai_types.Part.from_uri(
            file_uri=video_file_uri,
            mime_type=video_file.mime_type or "video/mp4",
        )

        # ── API call with fallback chain ─────────────────────────────
        last_exc: Exception | None = None

        for model_id in [GEMINI_PRIMARY, GEMINI_FALLBACK]:
            try:
                logger.info("Gemini call using model %s", model_id)

                response = gemini_client.client.models.generate_content(
                    model=model_id,
                    contents=[part_video],          # bare Part, no Content wrapper
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TRANSCRIPT_PLAN_SCHEMA_DICT,
                    ),
                )

                raw_text = response.text

                # ── Robust JSON extraction ─────────────────────────────
                try:
                    data = _extract_json_response(raw_text)
                except Exception as exc:
                    raise GeminiResponseInvalid(
                        f"Could not parse JSON from response: {exc}"
                    ) from exc

                # ── Validate with Pydantic model ───────────────────────
                plan = TranscriptPlanResponse(**data)

                # ── Persist transcript + word timestamps ───────────────
                words_payload = [w.model_dump() for w in plan.words]
                try:
                    supabase_client.table("transcripts").upsert({
                        "video_id": video_id,
                        "full_text": plan.full_transcript,
                        "words": words_payload,
                    }, on_conflict="video_id").execute()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Upsert with `words` failed (schema may not have the column yet): %s. "
                        "Falling back to legacy insert.",
                        exc,
                    )
                    supabase_client.table("transcripts").upsert({
                        "video_id": video_id,
                        "full_text": plan.full_transcript,
                    }, on_conflict="video_id").execute()

                # ── Persist candidates ─────────────────────────────────
                supabase_client.table("edit_plans").delete().eq(
                    "video_id", video_id
                ).eq("status", "pending_review").execute()

                candidates_data = []
                for i, cand in enumerate(plan.candidates):
                    candidates_data.append({
                        "video_id": video_id,
                        "candidate_index": i,
                        "segments": [seg.model_dump() for seg in cand.segments],
                        "status": "pending_review",
                        "hook_score": cand.hook_score,
                        "overall_score": cand.overall_score,
                    })
                if candidates_data:
                    supabase_client.table("edit_plans").insert(candidates_data).execute()

                logger.info(
                    "Created %d candidates for %s (%d word timestamps)",
                    len(plan.candidates), video_id, len(plan.words),
                )
                return True

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

    except Exception as exc:  # noqa: BLE001
        logger.exception("transcribe_and_plan failed for %s: %s", video_id, exc)
        raise