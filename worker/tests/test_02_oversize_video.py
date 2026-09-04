"""Test 02: Oversize video.

A real 11-minute video (just past the 10-min cap) should be rejected by
the validate stage. We don't actually create a 600MB file — we just
generate a 11-min test pattern with ffmpeg, which is small because
testsrc compresses to almost nothing.
"""
import asyncio
import os
import subprocess
import sys
import tempfile

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 02: Oversize video (>10 min)")
    with test_case("validate_video rejects 11-minute video") as t:
        from worker.stages.validate import validate_video, MAX_DURATION_SECONDS

        # 11 minutes = 660s, just past the 600s cap
        long_dur = MAX_DURATION_SECONDS + 60
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False
        ) as f:
            long_path = f.name

        # Use ffmpeg's testsrc filter — generates a 1920x1080 test pattern
        # at any duration. Compresses to almost nothing.
        ffmpeg = _find_ffmpeg()
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={long_dur}:size=320x240:rate=15",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            long_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[-300:]}")

        try:
            ok = await validate_video(long_path)
            assert_(ok is False, f"expected False for {long_dur}s video, got {ok}")
        finally:
            try:
                os.remove(long_path)
            except OSError:
                pass

    return t["passed"]


def _find_ffmpeg() -> str:
    """Locate ffmpeg. We try the worker's PATH-prepend first, then
    common Windows install locations."""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        r"C:\Users\itsme\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "C:/ffmpeg/bin/ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("ffmpeg not found — install it or add to PATH")


if __name__ == "__main__":
    asyncio.run(main())
