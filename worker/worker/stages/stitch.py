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
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                clip_path,
            ])
            temp_clips.append(clip_path)

        # 2. Concat
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for clip in temp_clips:
                f.write(f"file '{clip}'\n")

        # 3. Stitch. We force the output to 1080x1920 (9:16 portrait) here
        #    so the raw-cut fallback path (where stitch_input is the
        #    un-reframed source) still produces a properly-shaped reel.
        #    When the reframed path is used, the per-clip is already
        #    1080x1920 and the scale+pad are no-ops; the cost is one
        #    extra re-encode pass which is cheap (concat copy can't
        #    combine clips from different filter graphs).
        #
        #    IMPORTANT: use literal pad offsets (not `(ow-iw)/2` etc).
        #    ffmpeg's runtime arithmetic in pad offsets can fail with
        #    `Error reinitializing filters!` on some inputs. With
        #    force_original_aspect_ratio=decrease the padded dim is
        #    always 1080x1080 (square) sitting in a 1080x1920 frame,
        #    so the y offset is literally 420 = (1920-1080)/2.
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-threads", "1",
            "-filter_threads", "1",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:0:420:black,"
            "setsar=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
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
