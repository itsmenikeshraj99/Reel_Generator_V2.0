"""Smart pipeline stages (used only when SMART_PIPELINE_ENABLED is on).

They replace the two AI stages of the classic pipeline and leave every
other stage (validate, reframe, stitch, captions, compression, upload)
untouched:

* TRANSCRIBING_PLANNING -> `run_transcribe_understand`: speech-to-text
  with word times, sentence index, and the understanding call.
* REVIEWING            -> `run_select`: story plan, exact cuts, draft,
  verification and revisions, then the accepted plan is saved in the same
  `edit_plans` shape the render stage already reads.

State between the two stages lives on the Pipeline object
(`smart_state`); metrics for `reels.meta` go to `reel_meta_extra`.
"""
import asyncio
import logging
import os
import tempfile
import time
import uuid
from typing import Any, Dict, List

from worker.asr import transcribe_with_fallback
from worker.config import settings
from worker.edit.draft import render_draft
from worker.edit.select_reel import select_reel
from worker.gemini.client import gemini_client
from worker.gemini.plan_story import plan_story
from worker.gemini.understand import UnderstandingError, understand
from worker.gemini.verify import verify_draft
from worker.services.plan_store import save_plan, save_sentences
from worker.services.supabase import supabase_client
from worker.text.sentences import build_sentences
from worker.text.snap import detect_silences

logger = logging.getLogger("stage_smart_plan")

MIN_SPEECH_SECONDS = 20.0


def _non_retryable(message: str) -> Exception:
    from worker.pipeline import NonRetryableError  # lazy: pipeline imports this module lazily too

    return NonRetryableError(message)


async def run_transcribe_understand(pipe: Any, video_path: str) -> bool:
    """Transcribe, build the sentence index and run the understanding call."""
    state = pipe.smart_state
    if state.get("understanding") is not None:
        return True  # an earlier attempt in this run already got this far
    vid = pipe.video_id
    metrics: Dict[str, Any] = state.setdefault("metrics", {})
    await pipe._heartbeat()

    # 1. Transcript (reuse a stored one on retries)
    t0 = time.time()
    row = supabase_client.table("transcripts").select("full_text, words").eq(
        "video_id", vid
    ).limit(1).execute()
    stored = row.data[0] if row.data else None
    if stored and stored.get("words"):
        words, full_text = stored["words"], stored.get("full_text") or ""
        metrics["asr"] = {"provider": "reused"}
    else:
        result, asr_meta = await transcribe_with_fallback(video_path)
        words, full_text = result.words_for_db(), result.full_text
        metrics["asr"] = {
            "provider": asr_meta["provider"], "fallback_used": asr_meta["fallback_used"],
            "primary_error": asr_meta["primary_error"], "language": result.language,
        }
        if words and full_text.strip():
            supabase_client.table("transcripts").upsert({
                "video_id": vid, "full_text": full_text, "words": words,
            }, on_conflict="video_id").execute()
    if not words or not full_text.strip():
        raise _non_retryable("No speech was detected in this video.")
    metrics["asr"]["seconds"] = round(time.time() - t0, 1)
    await pipe._heartbeat()

    # 2. Sentence index
    sentences = build_sentences(words)
    span = (sentences[-1].end - sentences[0].start) if sentences else 0.0
    if span < MIN_SPEECH_SECONDS:
        raise _non_retryable(
            f"Not enough speech to build a reel ({span:.0f}s of speech, at least {MIN_SPEECH_SECONDS:.0f}s needed).")
    try:
        save_sentences(vid, sentences)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Could not save the sentence index (is migration 005 applied?): {exc}") from exc
    sentence_dicts = [s.to_dict() for s in sentences]

    # 3. Understanding (the uploaded copy is always deleted afterwards)
    t1 = time.time()
    uploaded = None
    try:
        uploaded = await asyncio.to_thread(gemini_client.upload_video, video_path)
        uploaded = await gemini_client.wait_for_processing(uploaded)
        await pipe._heartbeat()
        uri = uploaded.uri
        if not uri.startswith(("https:", "http:")):
            uri = f"https://generativelanguage.googleapis.com/{uri}"
        understanding = await understand(uri, sentence_dicts, video_id=vid)
    except UnderstandingError as exc:
        raise RuntimeError(str(exc)) from exc
    finally:
        if uploaded is not None and getattr(uploaded, "name", None):
            try:
                gemini_client.client.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001
                pass

    metrics.update({
        "sentences": len(sentence_dicts),
        "key_points": len(understanding.key_points),
        "understand_seconds": round(time.time() - t1, 1),
    })
    state.update(sentences=sentence_dicts, words=words, understanding=understanding)
    return True


async def run_select(pipe: Any, video_path: str) -> bool:
    """Plan, cut, verify and save the accepted plan for the render stage."""
    if pipe.smart_state.get("understanding") is None:
        await run_transcribe_understand(pipe, video_path)
    state = pipe.smart_state
    vid = pipe.video_id
    metrics: Dict[str, Any] = state.setdefault("metrics", {})
    sentences: List[Dict[str, Any]] = state["sentences"]
    await pipe._heartbeat()
    t0 = time.time()

    media_duration = await pipe._get_video_duration(video_path)
    try:
        silences = await detect_silences(video_path, media_duration or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Silence detection failed for %s (%s); cutting on word edges only", vid, exc)
        silences = []
        metrics["silence_detection"] = "failed"

    async def make_draft(segments: List[Dict[str, Any]]) -> str:
        out = os.path.join(tempfile.gettempdir(), f"draft_{vid}_{uuid.uuid4().hex[:6]}.mp4")
        try:
            return await render_draft(
                video_path, segments, out, runner=pipe._run_with_heartbeat,
                fade_s=settings.TRANSITION_SECONDS if settings.TRANSITIONS_ENABLED else 0.0)
        except Exception:
            try:
                os.remove(out)
            except OSError:
                pass
            raise

    async def verify(draft: str, plan: Dict[str, Any]):
        await pipe._heartbeat()
        return await verify_draft(draft, plan, sentences)

    selection = await select_reel(
        understanding=state["understanding"],
        sentences=sentences,
        words=state["words"],
        silences=silences,
        media_duration=media_duration or None,
        plan_fn=plan_story,
        make_draft=make_draft,
        verify=verify,
        max_revisions=settings.MAX_REVISIONS,
        target_seconds=settings.REEL_TARGET_SECONDS,
    )
    if not selection.ok:
        raise _non_retryable("Could not build a reel from this video: " + "; ".join(selection.history)[:300])

    try:
        save_plan(vid, selection.plan, selection.segments, selection.verifier)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Could not save the plan (is migration 005 applied?): {exc}") from exc

    metrics.update({
        "select_seconds": round(time.time() - t0, 1),
        "shape": selection.plan["shape"],
        "beats": len(selection.plan["beats"]),
        "revisions": selection.revisions,
        "used_fallback": selection.used_fallback,
        "mode": selection.mode,
        "verdict": (selection.verifier or {}).get("verdict"),
        "total_seconds": selection.total_seconds,
        "history": selection.history,
        "attempts": selection.attempts,
    })
    pipe.reel_meta_extra = {
        "smart_pipeline": True,
        "needs_review": selection.needs_review,
        "smart": metrics,
    }
    return True
