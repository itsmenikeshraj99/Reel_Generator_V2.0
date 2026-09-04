"""Stage 4b — stitch the accepted segments into a final reel."""
import json
import logging
import os
import subprocess
import tempfile
from typing import List

from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_stitch")


def _run_ffmpeg(cmd: List[str]) -> None:
    """Run ffmpeg and raise with a useful message on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}"
        )


async def stitch_and_caption(video_id: str, video_path: str, output_path: str) -> bool:
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

        concat_list_path = f"{output_path}.txt"

        # 1. Cut each segment with re-encoding (keyframe-accurate)
        for i, seg in enumerate(segments):
            clip_path = f"{output_path}_part_{i}.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-ss", str(seg["start_time"]),
                "-to", str(seg["end_time"]),
                "-i", video_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                clip_path,
            ])
            temp_clips.append(clip_path)

        # 2. Concat
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip in temp_clips:
                f.write(f"file '{clip}'\n")

        # 3. Stitch. The reframed input is already 1080x1920 9:16, so we just
        #    concat + re-encode for consistency. The per-clip scale and pad
        #    is dropped — each clip is already the correct size and the
        #    concat keeps dimensions as-is.
        #    (Previously this had `scale=...,pad=...:0:0:black` to guard
        #    against future reframe size drift, but ffmpeg's runtime
        #    arithmetic in pad offsets can fail with `Error reinitializing
        #    filters!` on some inputs — and we don't actually need it
        #    because reframe.py produces 1080x1920 by construction.)
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
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
