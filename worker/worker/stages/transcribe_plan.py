"""Stage 2 — Gemini Call #1: transcript + edit plan candidates."""
import json
import logging

from worker.gemini.client import gemini_client
from worker.gemini.prompts import TRANSCRIPTION_PLANNING_PROMPT
from worker.gemini.schemas import TRANSCRIPT_PLAN_SCHEMA_DICT, TranscriptPlanResponse
from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_transcribe")


async def transcribe_and_plan(video_id: str, video_path: str) -> bool:
    try:
        logger.info("Generating transcript and plan for video %s", video_id)

        # Upload + wait for ACTIVE
        video_file = gemini_client.upload_video(video_path)
        video_file = await gemini_client.wait_for_processing(video_file)

        # Generate with structured output. SDK gets the flat dict; Python
        # validates the result against the Pydantic model.
        response_text = gemini_client.generate_content(
            contents=[video_file],
            prompt=TRANSCRIPTION_PLANNING_PROMPT,
            response_schema=TRANSCRIPT_PLAN_SCHEMA_DICT,
        )

        data = json.loads(response_text)
        plan = TranscriptPlanResponse(**data)

        # Persist transcript + word timestamps. We use a single upsert so
        # re-running the stage is idempotent.
        words_payload = [w.model_dump() for w in plan.words]
        try:
            supabase_client.table("transcripts").upsert({
                "video_id": video_id,
                "full_text": plan.full_transcript,
                "words": words_payload,
            }, on_conflict="video_id").execute()
        except Exception as exc:  # noqa: BLE001
            # Fallback: legacy schema without `words` column. Insert full_text only.
            logger.warning(
                "Upsert with `words` failed (schema may not have the column yet): %s. "
                "Falling back to legacy insert.",
                exc,
            )
            supabase_client.table("transcripts").upsert({
                "video_id": video_id,
                "full_text": plan.full_transcript,
            }, on_conflict="video_id").execute()

        # Persist candidates (replace existing pending ones for this video so
        # re-runs don't pile up duplicates).
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("transcribe_and_plan failed for %s: %s", video_id, exc)
        return False
