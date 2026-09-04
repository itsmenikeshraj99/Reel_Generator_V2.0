"""Stage 4c — burn captions onto the stitched reel.

Approach
--------
1. Fetch the accepted edit plan + word-level timestamps for the video.
2. Walk the segments, in source-time order, collecting words whose
   `[start, end]` overlaps the segment.
3. For each segment, compute caption **clip-time** = word.start - seg.start
   (so when we cut + concat, the captions stay in sync without per-clip
   timeline translation).
4. Generate an ASS subtitle file with each cue as 1-3 words, bottom-centered,
   high-contrast (white text, black outline, yellow highlight per word).
5. Run ffmpeg with the `ass` filter to burn the subtitles into the video.

If the captions step fails, we **don't fail the whole pipeline** — we
return False but the upstream stitch step has already produced a valid
silent-or-without-captions video, so the user still gets a reel.
"""
import logging
import os
import subprocess
from typing import Any, Dict, List, Tuple

from worker.services.supabase import supabase_client

logger = logging.getLogger("stage_caption")


# ASS color codes (BGR for ASS): yellow is &H00FFFF (B=00, G=FF, R=FF)
_HIGHLIGHT_COLOR = "&H00FFFF"
_BODY_COLOR = "&H00FFFFFF"
_OUTLINE_COLOR = "&H00000000"

_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Inter,72,&H00FFFFFF,&H00FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,3,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    """Convert seconds to ASS H:MM:SS.cs (centiseconds)."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    if s == 60:
        s = 0
        m += 1
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


def _fetch_video_data(video_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (segments, words). Empty if anything is missing."""
    plan_res = supabase_client.table("edit_plans").select("segments").eq(
        "video_id", video_id
    ).eq("status", "accepted").order("candidate_index").limit(1).execute()
    if not plan_res.data or not plan_res.data[0].get("segments"):
        return [], []

    tr_res = supabase_client.table("transcripts").select("words").eq(
        "video_id", video_id
    ).limit(1).execute()
    words: List[Dict[str, Any]] = []
    if tr_res.data and tr_res.data[0].get("words"):
        raw_words = tr_res.data[0]["words"]
        if isinstance(raw_words, list):
            words = raw_words

    return list(plan_res.data[0]["segments"]), words


def _build_ass(
    segments: List[Dict[str, Any]],
    words: List[Dict[str, Any]],
) -> str:
    """Build the full ASS script. Cue time is relative to the *concatenated*
    output (not the source), so we walk segments in order and add per-segment
    `time_offset` to convert source-time → clip-time.
    """
    if not words:
        # Without words we can't make per-cue captions; emit a single ASS
        # file with no events (ass filter still works, video just won't
        # display any captions).
        return _ASS_HEADER

    # Build a per-source-time word stream and a segment lookup.
    # Pre-sort words defensively.
    words_sorted = sorted(
        ({"text": w["text"], "start": float(w["start"]), "end": float(w["end"])} for w in words),
        key=lambda w: w["start"],
    )

    events: List[str] = []
    output_t = 0.0
    for seg in segments:
        seg_start = float(seg["start_time"])
        seg_end = float(seg["end_time"])
        if seg_end <= seg_start:
            continue
        seg_duration = seg_end - seg_start

        # Words overlapping this segment.
        seg_words = [w for w in words_sorted if w["end"] > seg_start and w["start"] < seg_end]
        if not seg_words:
            # No words in this segment — still bump the timeline.
            output_t += seg_duration
            continue

        # Group into 2-3 word chunks for readability.
        chunks: List[List[Dict[str, float]]] = []
        chunk: List[Dict[str, float]] = []
        for w in seg_words:
            chunk.append(w)
            if len(chunk) >= 2:
                chunks.append(chunk)
                chunk = []
        if chunk:
            chunks.append(chunk)

        for c in chunks:
            c_src_start = c[0]["start"]
            c_src_end = c[-1]["end"]
            # Clip-relative times
            cue_in = output_t + max(0.0, c_src_start - seg_start)
            cue_out = output_t + min(seg_duration, c_src_end - seg_start)
            if cue_out <= cue_in:
                cue_out = cue_in + 0.5
            # Build the styled text: each word highlighted in yellow.
            text_parts = []
            for w in c:
                word = str(w["text"]).replace("\n", " ").strip()
                if not word:
                    continue
                # ASS override tag: {\c&H00FFFF&}word{\c&HFFFFFF&}
                text_parts.append(
                    r"{\c" + _HIGHLIGHT_COLOR + r"&}" + word + r"{\c" + _BODY_COLOR + r"&}"
                )
            text = " ".join(text_parts)
            if not text:
                continue
            events.append(
                f"Dialogue: 0,{_ass_time(cue_in)},{_ass_time(cue_out)},Default,,0,0,0,,{text}"
            )

        output_t += seg_duration

    return _ASS_HEADER + "\n".join(events) + "\n"


async def burn_captions(video_id: str, video_path: str, output_path: str) -> bool:
    """Burn ASS captions into the reel. Returns True on success.

    On failure, returns False but logs — the upstream video is still
    considered valid; the user gets a reel without captions rather than
    no reel at all.
    """
    try:
        segments, words = _fetch_video_data(video_id)
        if not segments:
            logger.warning("No accepted segments for %s; skipping captions", video_id)
            return False

        ass_text = _build_ass(segments, words)
        if "Dialogue:" not in ass_text:
            logger.warning("No words overlap segments for %s; skipping captions", video_id)
            return False

        ass_path = video_path + ".ass"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_text)

        # Windows ffmpeg + ass filter: the filter parses the path with
        # ffmpeg's option parser, which chokes on backslashes and the
        # drive-letter colon. Normalize the path and quote it explicitly
        # in the filter expression.
        safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")

        # Use the `ass` filter to burn in. Use libx264 again to keep the
        # file web-playable, and `+faststart` for streaming.
        # Memory: single-thread + ultrafast keeps caption stage under
        # ~700MB so we don't OOM on Railway's 2GB worker after reframe.
        cmd = [
            "ffmpeg", "-y",
            "-threads", "1",
            "-filter_threads", "1",
            "-i", video_path,
            "-vf", f"ass='{safe_ass}'",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-c:a", "copy",  # never re-encode audio here
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.error(
                "ffmpeg caption burn failed (rc=%s): %s",
                result.returncode, result.stderr[-500:],
            )
            return False

        logger.info("Burned captions into %s", output_path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("burn_captions failed for %s: %s", video_id, exc)
        return False
    finally:
        # Best-effort cleanup of the .ass file
        try:
            ass_path = video_path + ".ass"
            if os.path.exists(ass_path):
                os.remove(ass_path)
        except OSError:
            pass
