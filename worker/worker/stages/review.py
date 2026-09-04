"""Stage 3 — Gemini Call #2: review each candidate, pick the best one.

Phase 4 behavior
----------------
1. Each pending candidate is reviewed by Gemini (Call 2). The reviewer can
   mark it `accepted` or `revise` (with concrete feedback).
2. If the best-scoring candidate is `revise` (below the 0.7 threshold),
   we feed the reviewer's feedback back into the planning prompt and
   regenerate candidates. This is "Call 1.5": same prompt + feedback
   + prior best. Capped at `MAX_REVISE_ATTEMPTS` (default 2).
3. When the cap is exhausted, we fall back to the next-best candidate
   by original `hook_score` and accept it (rather than fail the whole
   pipeline).
"""
import json
import logging
from typing import List, Optional

from worker.config import settings
from worker.gemini.client import gemini_client
from worker.gemini.prompts import REVIEWER_PROMPT
from worker.gemini.schemas import REVIEW_SCHEMA_DICT, ReviewResponse
from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_review")

# Score threshold below which a candidate is sent back for revision.
ACCEPT_THRESHOLD = 0.7

# Hard cap on how many times we let the planner regenerate after
# reviewer feedback. Bounded so a bad feedback loop never spins forever.
MAX_REVISE_ATTEMPTS = max(0, getattr(settings, "MAX_REVISE_ATTEMPTS", 2))


def _review_candidate(cand: dict) -> ReviewResponse:
    """Run the reviewer prompt on a single candidate. Returns a
    ReviewResponse, falling back to a safe default if Gemini fails.
    """
    try:
        prompt = (
            f"Review this edit plan (candidate {cand['candidate_index']}): "
            f"{json.dumps(cand['segments'])}\n\n{REVIEWER_PROMPT}"
        )
        review_text = gemini_client.generate_content(
            contents=[],
            prompt=prompt,
            response_schema=REVIEW_SCHEMA_DICT,
        )
        return ReviewResponse(**json.loads(review_text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Review parse failed for candidate %s: %s", cand["id"], exc)
        # On parse failure, treat the candidate as acceptable so we don't
        # loop on a bad reviewer response. Fall back to the original score.
        return ReviewResponse(
            status="revise" if cand.get("overall_score", 0) < ACCEPT_THRESHOLD else "accepted",
            feedback="auto-fallback (reviewer error)",
            overall_score=float(cand.get("overall_score", 0) or 0),
        )


async def _regenerate_candidates(
    video_id: str,
    prior_candidates: List[dict],
    feedback: str,
    attempt: int,
) -> List[dict]:
    """Call Call 1 again, this time injecting reviewer feedback.

    The structured-output JSON for the planner is the same shape as the
    first pass, but the prompt now asks the model to address the
    reviewer's specific complaints. We then **replace** the pending
    candidates for this video with the new ones.

    Async because Gemini's `wait_for_processing` is async. We MUST
    `await` it — calling `asyncio.get_event_loop().run_until_complete`
    from inside a running event loop raises
    `RuntimeError: This event loop is already running` on Python 3.10+.
    """
    from worker.gemini.prompts import TRANSCRIPTION_PLANNING_PROMPT
    from worker.gemini.schemas import TRANSCRIPT_PLAN_SCHEMA_DICT, TranscriptPlanResponse
    from worker.services.storage import worker_storage

    # The planner needs the video file again. Re-fetch the source path.
    vid_res = supabase_client.table("videos").select("gcs_uri").eq(
        "id", video_id
    ).single().execute()
    gcs_uri = vid_res.data["gcs_uri"]

    # Re-download the source so Gemini can re-process it. Cheap if the
    # file is already cached locally on the worker.
    import os
    import tempfile

    temp_src = os.path.join(tempfile.gettempdir(), f"src_{video_id}.mp4")
    if not os.path.exists(temp_src):
        file_bytes = worker_storage.download_file(gcs_uri)
        with open(temp_src, "wb") as f:
            f.write(file_bytes)

    augmented_prompt = (
        TRANSCRIPTION_PLANNING_PROMPT
        + "\n\nREVISION INSTRUCTIONS (attempt "
        + str(attempt + 1)
        + " of "
        + str(MAX_REVISE_ATTEMPTS)
        + "):\n"
        f"Reviewer feedback on the previous attempt: {feedback}\n"
        "Regenerate 3-5 candidates that explicitly address this feedback. "
        "Use stronger hooks, tighter pacing, or different segments as required."
    )

    video_file = gemini_client.upload_video(temp_src)
    video_file = await gemini_client.wait_for_processing(video_file)

    response_text = gemini_client.generate_content(
        contents=[video_file],
        prompt=augmented_prompt,
        response_schema=TRANSCRIPT_PLAN_SCHEMA_DICT,
    )
    data = json.loads(response_text)
    plan = TranscriptPlanResponse(**data)

    # Replace existing pending candidates so the next reviewer pass works
    # against the new ones.
    supabase_client.table("edit_plans").delete().eq(
        "video_id", video_id
    ).eq("status", "pending_review").execute()

    rows = []
    for i, cand in enumerate(plan.candidates):
        rows.append({
            "video_id": video_id,
            "candidate_index": i,
            "segments": [seg.model_dump() for seg in cand.segments],
            "status": "pending_review",
            "hook_score": cand.hook_score,
            "overall_score": cand.overall_score,
        })
    if rows:
        supabase_client.table("edit_plans").insert(rows).execute()
    return rows


async def review_candidates(video_id: str) -> bool:
    try:
        for attempt in range(MAX_REVISE_ATTEMPTS + 1):
            res = supabase_client.table("edit_plans").select("*").eq(
                "video_id", video_id
            ).eq("status", "pending_review").execute()
            candidates = res.data or []
            if not candidates:
                logger.warning("No pending_review candidates for %s — review step no-op", video_id)
                return True

            # 1. Review every candidate. Persist the verdict on each row.
            for cand in candidates:
                review = _review_candidate(cand)
                supabase_client.table("edit_plans").update({
                    "status": "accepted" if review.status == "accepted" else "rejected",
                    "feedback": review.feedback,
                    "overall_score": review.overall_score,
                }).eq("id", cand["id"]).execute()

            # 2. Pick the best candidate. The "best" is the highest-scoring
            #    one whose reviewer status is "accepted". If none was
            #    accepted, fall back to highest overall_score (so the user
            #    still gets a reel rather than nothing).
            best_id: Optional[str] = None
            best_score: float = -1.0
            any_accepted = False
            best_accepted_score: float = -1.0
            best_accepted_id: Optional[str] = None
            best_rejected_score: float = -1.0
            best_rejected_id: Optional[str] = None
            best_rejected_feedback: str = ""

            for cand in candidates:
                # Re-fetch the row to get the post-review status/score.
                cur = supabase_client.table("edit_plans").select(
                    "status, overall_score, feedback"
                ).eq("id", cand["id"]).single().execute()
                if not cur.data:
                    continue
                cur_status = cur.data.get("status")
                cur_score = float(cur.data.get("overall_score") or 0)
                cur_feedback = cur.data.get("feedback") or ""

                if cur_status == "accepted" and cur_score > best_accepted_score:
                    best_accepted_score = cur_score
                    best_accepted_id = cand["id"]
                    any_accepted = True
                if cur_status == "rejected" and cur_score > best_rejected_score:
                    best_rejected_score = cur_score
                    best_rejected_id = cand["id"]
                    best_rejected_feedback = cur_feedback

            chosen_id: Optional[str] = None
            if any_accepted and best_accepted_score >= ACCEPT_THRESHOLD:
                chosen_id = best_accepted_id
            else:
                # No acceptable candidate. If we still have revision budget,
                # regenerate with the best-rejected candidate's feedback.
                if attempt < MAX_REVISE_ATTEMPTS and best_rejected_id is not None:
                    logger.info(
                        "Attempt %d: no accepted candidate (best=%.2f); "
                        "regenerating with feedback",
                        attempt + 1, best_rejected_score,
                    )
                    try:
                        await _regenerate_candidates(
                            video_id, candidates, best_rejected_feedback, attempt,
                        )
                        continue
                    except Exception as regen_exc:  # noqa: BLE001
                        # If regeneration crashes, don't spin the loop.
                        # Fall through to the cap-reached path so the user
                        # still gets the best existing plan rather than a
                        # silent no-op success (the previous behavior).
                        logger.exception(
                            "Regeneration crashed for %s on attempt %d: %s",
                            video_id, attempt + 1, regen_exc,
                        )
                        # Fall through to cap-reached logic.
                # Cap reached — accept the best we have by hook_score
                # (the planner's "viral potential" estimate) so the user
                # still gets a reel.
                if best_rejected_id is not None:
                    logger.warning(
                        "Revise cap reached; falling back to best hook_score candidate %s",
                        best_rejected_id,
                    )
                    chosen_id = best_rejected_id

            if chosen_id is None:
                logger.error("Review failed: no viable candidate for %s", video_id)
                return False

            supabase_client.table("edit_plans").update({
                "status": "accepted",
            }).eq("id", chosen_id).execute()
            logger.info(
                "Accepted candidate %s for %s (score=%.2f, attempt=%d)",
                chosen_id, video_id,
                best_accepted_score if chosen_id == best_accepted_id else best_rejected_score,
                attempt + 1,
            )
            return True

        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("review_candidates crashed for %s: %s", video_id, exc)
        return False
