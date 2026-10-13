"""Test 17: Draft renderer (worker/worker/edit/draft.py).

Uses real ffmpeg on a generated 30-second test video. No network, no cost.
"""
import json
import os
import subprocess
import sys
import tempfile

from _helpers import test_case, assert_, section


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,height:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    kinds = [s["codec_type"] for s in data["streams"]]
    height = next((s["height"] for s in data["streams"] if s["codec_type"] == "video"), None)
    return float(data["format"]["duration"]), kinds, height


async def main() -> bool:
    section("Test 17: Draft renderer")
    from worker.edit.draft import render_draft

    results = []
    tmp = tempfile.mkdtemp(prefix="test17_")
    src = os.path.join(tmp, "src.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", src], check=True)
    segs = [{"start_time": 2.0, "end_time": 5.0, "title": "a", "reason": ""},
            {"start_time": 20.0, "end_time": 24.0, "title": "b", "reason": ""}]

    with test_case("two segments become one small draft with picture and sound") as t:
        out = os.path.join(tmp, "draft.mp4")
        got = await render_draft(src, segs, out)
        assert_(got == out and os.path.getsize(out) > 0, "no output")
        duration, kinds, height = probe(out)
        assert_(abs(duration - 7.0) < 0.6, f"duration {duration}")
        assert_("video" in kinds and "audio" in kinds, f"streams {kinds}")
        assert_(height == 360, f"height {height}")
        assert_(os.path.getsize(out) < os.path.getsize(src), "draft should be smaller than the source")
    results.append(t["passed"])

    with test_case("no temporary files are left behind") as t:
        before = set(os.listdir(tempfile.gettempdir()))
        await render_draft(src, segs, os.path.join(tmp, "again.mp4"))
        leftovers = [n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith("draft_")]
        assert_(not leftovers, f"left behind: {leftovers}")
    results.append(t["passed"])

    with test_case("the injected runner is used: one command per segment plus the join") as t:
        cmds = []

        async def fake_runner(cmd):
            cmds.append(cmd)
            if "-f" in cmd and "concat" in cmd:
                open(cmd[-1], "wb").write(b"x")
            else:
                open(cmd[-1], "wb").write(b"x")

        out = os.path.join(tmp, "fake.mp4")
        await render_draft(src, segs, out, runner=fake_runner)
        assert_(len(cmds) == 3, f"{len(cmds)} commands")
        assert_("-ss" in cmds[0] and "2.000" in cmds[0] and "3.000" in cmds[0], f"{cmds[0]}")
        assert_("concat" in cmds[2], f"{cmds[2]}")
    results.append(t["passed"])

    with test_case("a failing command raises and still cleans up") as t:
        before = set(os.listdir(tempfile.gettempdir()))

        async def boom(cmd):
            raise RuntimeError("ffmpeg exploded")

        try:
            await render_draft(src, segs, os.path.join(tmp, "x.mp4"), runner=boom)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert_("exploded" in str(exc), str(exc))
        leftovers = [n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith("draft_")]
        assert_(not leftovers, f"left behind: {leftovers}")
    results.append(t["passed"])

    with test_case("bad or missing segments are refused before any work") as t:
        for bad in ([], [{"start": 1.0, "end": 3.0}], [{"start_time": 5.0, "end_time": 5.0}],
                    [{"start_time": -1.0, "end_time": 2.0}], [{"start_time": "a", "end_time": 2.0}]):
            try:
                await render_draft(src, bad, os.path.join(tmp, "n.mp4"))
                raise AssertionError(f"accepted {bad!r}")
            except ValueError:
                pass
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
