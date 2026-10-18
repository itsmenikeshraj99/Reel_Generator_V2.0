"""Stage 4b — stitch the accepted segments into a final reel."""
import json
import logging
import os
import subprocess
import tempfile
from typing import List

from worker.config import settings
from worker.edit import transitions
from worker.edit.canvas import fit_filter
from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_stitch")


def _run_ffmpeg(cmd: List[str]) -> None:
    """Run ffmpeg and raise with a useful message on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}"
        )


def _try_transitions(pipeline, segments, clips: List[str], output_path: str) -> bool:
    """Join the cut clips with crossfades where the plan marks them (and the setting is on).

    Returns False, leaving no partial output, when there is nothing to fade or anything goes wrong, so
    the caller falls back to plain hard cuts.
    """
    if pipeline is not None:
        pipeline.transition_info = None
    fade_s = settings.TRANSITION_SECONDS if settings.TRANSITIONS_ENABLED else 0.0
    overlaps = transitions.overlaps_for(segments, fade_s)
    if not any(overlaps):
        return False
    try:
        graph = transitions.render(clips, overlaps, output_path, _run_ffmpeg)
    except Exception as exc:  # noqa: BLE001 - never lose the reel over a transition
        logger.warning("Crossfades failed (%s); using hard cuts instead", exc)
        try:
            os.remove(output_path)
        except OSError:
            pass
        return False
    count = sum(1 for o in overlaps if o > 0)
    if pipeline is not None:
        pipeline.transition_info = {"overlaps": overlaps, "fade_s": fade_s, "count": count}
        if hasattr(pipeline, "reel_meta_extra"):
            pipeline.reel_meta_extra["transitions"] = {"count": count, "seconds": fade_s}
    logger.info("Joined %d clips with %d crossfade(s), %.1fs long", len(clips), count, graph.length)
    return True


async def stitch_and_caption(pipeline, video_id: str, video_path: str, output_path: str) -> bool:
    """Cut segments per the accepted edit plan and stitch into one MP4.

    No captions are burned in yet (despite the historical name) — that's a follow-up.
    """
    temp_clips: List[str] = []
    concat_list_path: str = ""
    try:
        res = supabase_client.table("edit_plans").select("*").eq(
            "video_id", video_id
        ).eq("status", "accepted").order("candidate_index").limit(1).execute()
        if not res.data:
            logger.error("No accepted edit plan for %s", video_id)
            return False
        plan = res.data[0]
        segments = plan.get("segments") or []
        if not segments:
            logger.error("Accepted plan %s has no segments", plan.get("id"))
            return False

        # Safety net (Phase 8): the prompt and Pydantic schema both
        # enforce a 20-35s total stitched duration, but a buggy older
        # edit_plan row that was reviewed + accepted before this
        # constraint existed could still be in the DB. Surface a
        # clear error instead of silently shipping a 5s reel.
        total_duration = sum(
            float(seg["end_time"]) - float(seg["start_time"])
            for seg in segments
        )
        if total_duration < 20.0:
            logger.error(
                "Accepted plan %s has total duration %.1fs (< 20s minimum); "
                "rejecting before stitch",
                plan.get("id"), total_duration,
            )
            return False

        concat_list_path = f"{output_path}.txt"

        # 1. Cut each segment with re-encoding (keyframe-accurate)
        #    Memory note: -threads 1 + ultrafast preset keeps peak RSS under
        #    ~700MB so two parallel ffmpegs (refreme+stitch+caption pipeline)
        #    stay within Railway's 2GB worker limit. The trade-off is ~2x
        #    slower encoding, which is fine for a 30-90s reel.
        for i, seg in enumerate(segments):
            clip_path = f"{output_path}_part_{i}.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-threads", "1",
                "-filter_threads", "1",
                "-ss", str(seg["start_time"]),
                "-to", str(seg["end_time"]),
                "-i", video_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                clip_path,
            ])
            temp_clips.append(clip_path)

        # 1b. Crossfades at the joins the planner marked (if enabled); otherwise hard cuts below.
        if _try_transitions(pipeline, segments, temp_clips, output_path):
            logger.info("Stitched %d segments with crossfades into %s", len(temp_clips), output_path)
            return True

        # 2. Concat
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip in temp_clips:
                f.write(f"file '{clip}'\n")

        # 3. Stitch onto the 720x1280 (9:16 portrait) canvas. The whole frame
        #    is scaled to fit and centred on black (never cropped or
        #    stretched); a reframed 9:16 input passes through unchanged.
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-threads", "1",
            "-filter_threads", "1",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-vf", fit_filter(),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-af", "aresample=async=1",  # fix any A/V drift between cuts
            "-movflags", "+faststart",
            output_path,
        ])

        logger.info("Stitched %d segments into %s", len(temp_clips), output_path)
        return True

    except Exception as exc:  # noqa: BLE001
        logger.exception("stitch_and_caption failed for %s: %s", video_id, exc)
        return False
    finally:
        # Always clean temp files — even on success
        for clip in temp_clips:
            try:
                os.remove(clip)
            except OSError:
                pass
        if concat_list_path and os.path.exists(concat_list_path):
            try:
                os.remove(concat_list_path)
            except OSError:
                pass
