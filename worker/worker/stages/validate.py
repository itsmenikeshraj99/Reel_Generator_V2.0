"""Validates the uploaded video with ffprobe before any expensive Gemini call.

Enforces duration and codec limits so we don't waste Gemini quota on a 4-hour podcast.
"""
import json
import logging
import subprocess
from typing import Any, Dict

logger = logging.getLogger("stage_validate")

# Tunable limits — tighten/loosen as the product allows.
MAX_DURATION_SECONDS = 600   # 10 minutes
MIN_DURATION_SECONDS = 5     # 5 seconds
ALLOWED_CODECS = {"h264", "hevc", "vp9", "av1"}


def get_video_info(file_path: str) -> Dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams",
        "-of", "json",
        file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[-500:]}")
    return json.loads(result.stdout)


async def validate_video(video_path: str) -> bool:
    try:
        info = get_video_info(video_path)
    except FileNotFoundError as exc:
        logger.error("ffprobe not installed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not probe video %s: %s", video_path, exc)
        return False

    fmt = info.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    if duration < MIN_DURATION_SECONDS:
        logger.error("Video too short: %ss (min %ss)", duration, MIN_DURATION_SECONDS)
        return False
    if duration > MAX_DURATION_SECONDS:
        logger.error("Video too long: %ss (max %ss)", duration, MAX_DURATION_SECONDS)
        return False

    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        logger.error("No video stream in %s", video_path)
        return False

    codec = (video_streams[0].get("codec_name") or "").lower()
    if codec not in ALLOWED_CODECS:
        logger.error("Disallowed codec %s in %s (allowed: %s)", codec, video_path, ALLOWED_CODECS)
        return False

    logger.info("Validation OK: %ss, codec=%s", duration, codec)
    return True
