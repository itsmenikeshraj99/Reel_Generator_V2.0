"""Test 05: Low-quality video (very short / no video stream).

The validate stage enforces a minimum duration (5s) and at least one
video stream. We test both:
  a) A 2-second video (below the 5s floor) — should be rejected
  b) An audio-only MP4 (no video stream) — should be rejected
"""
import asyncio
import os
import subprocess
import tempfile

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 05: Low-quality / invalid video")

    with test_case("validate rejects 2-second video (below 5s min)") as t:
        from worker.stages.validate import validate_video

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            short_path = f.name

        ffmpeg = _find_ffmpeg()
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            short_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)

        try:
            ok = await validate_video(short_path)
            assert_(ok is False, f"expected False for 2s video, got {ok}")
        finally:
            try:
                os.remove(short_path)
            except OSError:
                pass

    if not t["passed"]:
        return False

    with test_case("validate rejects audio-only MP4 (no video stream)") as t2:
        from worker.stages.validate import validate_video

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            audio_path = f.name

        ffmpeg = _find_ffmpeg()
        # 10s of sine wave — no video stream
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:a", "aac",
            audio_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)

        try:
            ok = await validate_video(audio_path)
            assert_(ok is False, f"expected False for audio-only, got {ok}")
        finally:
            try:
                os.remove(audio_path)
            except OSError:
                pass

    return t2["passed"]


def _find_ffmpeg() -> str:
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        r"C:\Users\itsme\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("ffmpeg not found")


if __name__ == "__main__":
    asyncio.run(main())
