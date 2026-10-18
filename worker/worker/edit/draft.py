"""Render a small draft of the plan for the verifier to watch.

Hard cuts only, low resolution, audio kept. The draft is thrown away
after verification; it is never shown to users.
"""
import asyncio
import logging
import os
import subprocess
import tempfile
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from worker.edit import transitions

logger = logging.getLogger("edit_draft")

Runner = Callable[[List[str]], Awaitable[None]]

DRAFT_HEIGHT = 360
DRAFT_CANVAS = (480, 854)  # a small 9:16 canvas, used when the draft has crossfades (as the final reel does)


async def _default_runner(cmd: List[str], timeout: int = 900) -> None:
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"Draft render failed (rc={proc.returncode}): {proc.stderr[-300:]}")


def _check_segments(segments: Sequence[Dict[str, Any]]) -> List[Dict[str, float]]:
    if not segments:
        raise ValueError("no segments to render")
    out = []
    for seg in segments:
        try:
            start, end = float(seg["start_time"]), float(seg["end_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad segment {seg!r}") from exc
        if not end > start >= 0:
            raise ValueError(f"bad segment {seg!r}")
        out.append({"start": start, "dur": end - start, "join": seg.get("join")})
    return out


async def render_draft(
    video_path: str,
    segments: Sequence[Dict[str, Any]],
    out_path: str,
    runner: Optional[Runner] = None,
    fade_s: float = 0.0,
) -> str:
    """Cut each segment small and join them into `out_path`: with the same short crossfades the final
    reel will have (when `fade_s` > 0 and the plan marks fading joins), otherwise with hard cuts.

    `runner` runs one ffmpeg command (pass `pipeline._run_with_heartbeat`
    inside the worker so the job keeps sending heartbeats).
    """
    cuts = _check_segments(segments)
    run = runner or _default_runner
    work = tempfile.mkdtemp(prefix="draft_")
    parts: List[str] = []
    list_path = os.path.join(work, "list.txt")
    try:
        for i, c in enumerate(cuts):
            part = os.path.join(work, f"part_{i}.mp4")
            await run([
                "ffmpeg", "-y", "-loglevel", "error", "-threads", "1",
                "-ss", f"{c['start']:.3f}", "-i", video_path, "-t", f"{c['dur']:.3f}",
                "-vf", f"scale=-2:{DRAFT_HEIGHT},setsar=1", "-r", "24",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-crf", "32",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "64k",
                part,
            ])
            parts.append(part)
        overlaps = transitions.overlaps_for(
            [{"start_time": 0.0, "end_time": c["dur"], "join": c["join"]} for c in cuts], fade_s)
        if any(overlaps):
            try:
                cmd, _ = transitions.build_command(parts, overlaps, out_path, canvas=DRAFT_CANVAS, crf=32, audio_kbps=64)
                await run(cmd)
                return out_path
            except Exception as exc:  # noqa: BLE001 - the draft only needs to exist; fall back to hard cuts
                logger.warning("Crossfade draft failed (%s); using hard cuts", exc)
                try:
                    os.remove(out_path)
                except OSError:
                    pass
        with open(list_path, "w", encoding="utf-8") as f:
            for part in parts:
                f.write("file '" + part.replace("\\", "/") + "'\n")
        await run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", out_path,
        ])
        return out_path
    finally:
        for p in parts + [list_path]:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass
