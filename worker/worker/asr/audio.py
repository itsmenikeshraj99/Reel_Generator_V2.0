"""Pull a small speech-only audio file out of a video with ffmpeg."""
import asyncio
import os
import subprocess
import tempfile
from typing import List

from worker.asr.base import ASRError

# Mono 16 kHz is what speech models use internally; 32 kbps MP3 keeps a
# 60-minute video well under the 25 MB upload limit of the free tier.
_AUDIO_ARGS = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k"]


def build_extract_cmd(video_path: str, out_path: str) -> List[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", video_path,
        "-map", "0:a:0",
        *_AUDIO_ARGS,
        out_path,
    ]


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


async def extract_audio(video_path: str, timeout: int = 900) -> str:
    """Return the path of a temporary .mp3; the caller deletes it."""
    fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="asr_")
    os.close(fd)
    cmd = build_extract_cmd(video_path, out_path)
    try:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        remove_quietly(out_path)
        raise ASRError(f"Audio extraction failed: {type(exc).__name__}") from exc
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        remove_quietly(out_path)
        raise ASRError(f"Audio extraction failed (no audio track?): {proc.stderr[-300:]}")
    return out_path
