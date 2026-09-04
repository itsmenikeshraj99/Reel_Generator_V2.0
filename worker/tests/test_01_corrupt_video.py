"""Test 01: Corrupt video file.

Feeds a non-MP4 (e.g. a text file renamed to .mp4) into the validate
stage and asserts it returns False. This is the most common
"this isn't actually a video" failure mode in the wild.
"""
import asyncio
import os
import tempfile

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 01: Corrupt video file")
    with test_case("validate_video rejects non-mp4 file") as t:
        from worker.stages.validate import validate_video

        # Write a fake "video" that is actually just bytes of text
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False, mode="wb"
        ) as f:
            f.write(b"this is not a video, just some random bytes " * 100)
            fake_path = f.name

        try:
            ok = await validate_video(fake_path)
            assert_(ok is False, f"expected False, got {ok}")
        finally:
            try:
                os.remove(fake_path)
            except OSError:
                pass

    return t["passed"]


if __name__ == "__main__":
    asyncio.run(main())
