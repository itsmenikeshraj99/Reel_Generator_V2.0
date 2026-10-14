"""Test 21: Fit-to-canvas (worker/worker/edit/canvas.py and the stitch stage).

Real ffmpeg on generated white test videos, so the picture area can be
measured exactly (white = picture, black = canvas). No network, no cost.
"""
import os
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section

W, H = 720, 1280


def make_src(path, w, h, seconds=3, sar=None, rotate=None):
    vf = f"setsar={sar}" if sar else "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=white:s={w}x{h}:r=25:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-vf", vf,
           "-c:v", "libx264", "-pix_fmt", "yuv444p" if (w % 2 or h % 2) else "yuv420p",  # 4:2:0 cannot be odd-sized
           "-c:a", "aac", "-shortest", path]
    subprocess.run(cmd, check=True)
    if rotate:
        rotated = path + ".rot.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-display_rotation", str(rotate), "-i", path,
                        "-c", "copy", rotated], check=True)
        os.replace(rotated, path)


def probe_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=s=x:p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def picture_box(path):
    """(w, h, x, y) of the non-black area, measured by ffmpeg's cropdetect."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-vf", "cropdetect=limit=24:round=2:reset=0",
                          "-frames:v", "20", "-f", "null", "-"], capture_output=True, text=True).stderr
    boxes = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", err)
    assert boxes, "cropdetect found nothing"
    return tuple(int(v) for v in boxes[-1])


def render_fit(src, out):
    from worker.edit.canvas import fit_filter

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", fit_filter(), "-frames:v", "20",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", out], check=True)


def expected_box(disp_w, disp_h):
    """Ideal fit: largest size inside 720x1280 with the same proportions, centred."""
    scale = min(W / disp_w, H / disp_h)
    w, h = disp_w * scale, disp_h * scale
    return w, h, (W - w) / 2, (H - h) / 2


async def main() -> bool:
    section("Test 21: Fit-to-canvas")
    from worker.edit.canvas import fit_filter

    results = []
    tmp = tempfile.mkdtemp(prefix="test21_")

    cases = [
        ("16:9 landscape 1280x720", dict(w=1280, h=720)),
        ("16:9 full HD 1920x1080", dict(w=1920, h=1080)),
        ("4K 3840x2160", dict(w=3840, h=2160)),
        ("4:3 640x480", dict(w=640, h=480)),
        ("square 1000x1000", dict(w=1000, h=1000)),
        ("vertical 9:16 1080x1920", dict(w=1080, h=1920)),
        ("tall 9:21 540x1260", dict(w=540, h=1260)),
        ("odd size 1081x607", dict(w=1081, h=607)),
        ("small 320x180 (scaled up to the canvas width)", dict(w=320, h=180)),
        ("ultra-wide 2560x1080", dict(w=2560, h=1080)),
    ]
    for label, spec in cases:
        with test_case(f"{label}: 720x1280 canvas, proportions kept, centred") as t:
            src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
            make_src(src, spec["w"], spec["h"])
            render_fit(src, out)
            assert_(probe_size(out) == (W, H), f"canvas is {probe_size(out)}")
            bw, bh, bx, by = picture_box(out)
            ew, eh, ex, ey = expected_box(spec["w"], spec["h"])
            assert_(abs(bw - ew) <= 4 and abs(bh - eh) <= 4, f"picture {bw}x{bh}, expected about {ew:.0f}x{eh:.0f}")
            assert_(abs(bx - ex) <= 3 and abs(by - ey) <= 3, f"picture at ({bx},{by}), expected about ({ex:.0f},{ey:.0f})")
            assert_(abs(bw / bh - spec["w"] / spec["h"]) / (spec["w"] / spec["h"]) < 0.02, "proportions changed (stretched)")
        results.append(t["passed"])

    with test_case("non-square pixels keep the true display shape (960x720 with 4:3 pixels is really 16:9)") as t:
        src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
        make_src(src, 960, 720, sar="4/3")
        render_fit(src, out)
        bw, bh, _, by = picture_box(out)
        assert_(abs(bw / bh - 16 / 9) / (16 / 9) < 0.03, f"picture {bw}x{bh} is not 16:9")
        assert_(abs(by - (H - bh) / 2) <= 3, "not centred")
    results.append(t["passed"])

    with test_case("a phone video with rotation metadata comes out upright and un-stretched") as t:
        src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
        try:
            make_src(src, 1280, 720, rotate=90)  # stored landscape, displayed portrait
        except subprocess.CalledProcessError:
            print("    (this ffmpeg cannot write rotation metadata; case skipped)")
        else:
            render_fit(src, out)
            bw, bh, _, _ = picture_box(out)
            assert_(bh > bw, f"picture {bw}x{bh} is not portrait, rotation was ignored")
            assert_(abs(bw / bh - 9 / 16) / (9 / 16) < 0.03, f"picture {bw}x{bh} is stretched")
    results.append(t["passed"])

    with test_case("a video already 9:16 at the canvas size passes through unchanged in size") as t:
        src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
        make_src(src, 720, 1280)
        render_fit(src, out)
        assert_(picture_box(out)[:2] == (W, H), f"{picture_box(out)}")
    results.append(t["passed"])

    with test_case("the filter works for another canvas size too") as t:
        src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
        make_src(src, 1280, 720)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", fit_filter(1080, 1920), "-frames:v", "10",
                        "-an", out], check=True)
        assert_(probe_size(out) == (1080, 1920), f"{probe_size(out)}")
        assert_(picture_box(out)[0] >= 1076, f"{picture_box(out)}")
    results.append(t["passed"])

    with test_case("the stitch stage puts the picture on the centred canvas (end to end, real ffmpeg)") as t:
        from worker.stages import stitch

        src = os.path.join(tmp, "podcast.mp4")
        make_src(src, 1280, 720, seconds=40)
        out = os.path.join(tmp, "reel.mp4")
        plan = {"id": "p1", "segments": [{"start_time": 2.0, "end_time": 14.0}, {"start_time": 20.0, "end_time": 32.0}]}

        class Q:
            def select(self, *a):
                return self

            def eq(self, *a):
                return self

            def order(self, *a):
                return self

            def limit(self, *a):
                return self

            def execute(self):
                return SimpleNamespace(data=[plan])

        fake_db = SimpleNamespace(table=lambda name: Q())
        with patch.object(stitch, "supabase_client", fake_db):
            ok = await stitch.stitch_and_caption(None, "v1", src, out)
        assert_(ok and os.path.exists(out), "stitch failed")
        assert_(probe_size(out) == (W, H), f"{probe_size(out)}")
        bw, bh, bx, by = picture_box(out)
        assert_(abs(bw - 720) <= 4 and abs(bh - 404) <= 4 and abs(by - 438) <= 3, f"picture {bw}x{bh} at ({bx},{by})")
    results.append(t["passed"])

    with test_case("the old off-centre padding is gone from the stitch stage") as t:
        text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "worker", "stages", "stitch.py"), encoding="utf-8").read()
        assert_("fit_filter(" in text and "pad=720:1280:0:280" not in text, "stitch still uses the old filter")
    results.append(t["passed"])

    with test_case("REFRAME_MODE defaults to fit, accepts crop in any case, rejects anything else") as t:
        from worker.config import WorkerSettings, settings

        assert_(settings.REFRAME_MODE == "fit", f"default is {settings.REFRAME_MODE}")
        base = dict(SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g")
        assert_(WorkerSettings(**base, REFRAME_MODE=" CROP ").REFRAME_MODE == "crop", "normalise")
        for bad in ("stretch", "", "full"):
            try:
                WorkerSettings(**base, REFRAME_MODE=bad)
                raise AssertionError(f"accepted {bad!r}")
            except ValueError:
                pass
    results.append(t["passed"])

    with test_case("pipeline: fit mode skips the subject-crop pass and stitches the source; crop mode still reframes") as t:
        from worker.config import settings
        from worker.pipeline import Pipeline
        from worker.stages import reframe as reframe_mod, stitch as stitch_mod

        calls = {"reframe": 0, "stitch_input": None}

        async def fake_reframe(pipe, video_path, out):
            calls["reframe"] += 1
            return True

        async def fake_stitch(pipe, vid, stitch_input, out):
            calls["stitch_input"] = stitch_input
            return False  # stop the render stage right after the stitch call

        async def run(mode):
            calls.update(reframe=0, stitch_input=None)
            with patch.object(settings, "REFRAME_MODE", mode), patch.object(reframe_mod, "reframe_video", fake_reframe),                     patch.object(stitch_mod, "stitch_and_caption", fake_stitch):
                await Pipeline("v-mode")._stage_render("source.mp4", "reframed.mp4", "out.mp4", "reels/x.mp4")
            return dict(calls)

        fit = await run("fit")
        assert_(fit["reframe"] == 0 and fit["stitch_input"] == "source.mp4", f"{fit}")
        crop = await run("crop")
        assert_(crop["reframe"] == 1 and crop["stitch_input"] == "reframed.mp4", f"{crop}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
