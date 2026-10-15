"""Test 24: Caption placement for the fit layout (worker/edit/canvas.py + stages/caption.py).

Real ffmpeg on generated white test videos; the burned captions are located by their yellow
pixels. No network, no cost.
"""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from _helpers import test_case, assert_, section
from test_21_fit_canvas import make_src, picture_box as measure_box, render_fit

OLD_STYLE = ("Style: Default,Inter,72,&H00FFFFFF,&H00FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
             "5,3,2,60,60,420,1")
SHAPES = [
    ("16:9 1280x720", dict(w=1280, h=720)), ("4:3 640x480", dict(w=640, h=480)),
    ("square 1000x1000", dict(w=1000, h=1000)), ("vertical 1080x1920", dict(w=1080, h=1920)),
    ("tall 540x1260", dict(w=540, h=1260)), ("ultra-wide 2560x1080", dict(w=2560, h=1080)),
    ("4K 3840x2160", dict(w=3840, h=2160)), ("odd 1081x607", dict(w=1081, h=607)),
]


def yellow_rows(video, t_ms):
    """Rows (y) of the frame at t that contain caption-yellow pixels."""
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
    ok, frame = cap.read()
    cap.release()
    assert ok, "could not read a frame"
    b, g, r = frame[:, :, 0].astype(int), frame[:, :, 1].astype(int), frame[:, :, 2].astype(int)
    mask = (b < 90) & (g > 200) & (r > 200)
    rows = np.where(mask.any(axis=1))[0]
    return rows, int(mask.sum())


def words_for(seconds):
    return [{"text": f"word{i}", "start": round(i * 0.4, 2), "end": round(i * 0.4 + 0.35, 2)} for i in range(int(seconds / 0.4))]


async def main() -> bool:
    section("Test 24: Caption placement for the fit layout")
    from worker.config import settings
    from worker.edit import canvas
    from worker.edit.canvas import DEFAULT_CAPTION_LAYOUT, Box, caption_layout, picture_box, probe_display_size
    from worker.stages import caption

    results = []
    tmp = tempfile.mkdtemp(prefix="test24_")

    for label, spec in SHAPES:
        with test_case(f"{label}: the calculated picture box matches a real render") as t:
            src, out = os.path.join(tmp, "s.mp4"), os.path.join(tmp, "o.mp4")
            make_src(src, spec["w"], spec["h"])
            render_fit(src, out)
            mw, mh, mx, my = measure_box(out)
            box = picture_box(spec["w"], spec["h"])
            assert_(abs(mw - box.width) <= 4 and abs(mh - box.height) <= 4, f"measured {mw}x{mh}, calculated {box.width}x{box.height}")
            assert_(abs(mx - box.left) <= 3 and abs(my - box.top) <= 3, f"measured at ({mx},{my}), calculated ({box.left},{box.top})")
        results.append(t["passed"])

    with test_case("display size is read correctly: plain, non-square pixels, phone rotation; unreadable gives None") as t:
        src = os.path.join(tmp, "plain.mp4")
        make_src(src, 1280, 720)
        w, h = probe_display_size(src)
        assert_((round(w), round(h)) == (1280, 720), f"{(w, h)}")
        sar = os.path.join(tmp, "sar.mp4")
        make_src(sar, 960, 720, sar="4/3")
        w, h = probe_display_size(sar)
        assert_(abs(w / h - 16 / 9) < 0.02, f"{(w, h)}")
        rot = os.path.join(tmp, "rot.mp4")
        try:
            make_src(rot, 1280, 720, rotate=90)
        except subprocess.CalledProcessError:
            print("    (this ffmpeg cannot write rotation metadata; rotation case skipped)")
        else:
            w, h = probe_display_size(rot)
            assert_(h > w, f"rotation ignored: {(w, h)}")
        assert_(probe_display_size(os.path.join(tmp, "missing.mp4")) is None, "missing file")
        junk = os.path.join(tmp, "junk.mp4")
        open(junk, "wb").write(b"not a video")
        assert_(probe_display_size(junk) is None, "junk file")
    results.append(t["passed"])

    with test_case("layout: captions go under the picture when a line fits in the safe area, else the default") as t:
        expect_bar = {"16:9 1280x720": True, "4:3 640x480": True, "square 1000x1000": False, "vertical 1080x1920": False,
                      "tall 540x1260": False, "ultra-wide 2560x1080": True, "4K 3840x2160": True, "odd 1081x607": True}
        for label, spec in SHAPES:
            box = picture_box(spec["w"], spec["h"])
            lay = caption_layout(box)
            assert_(lay.in_bar == expect_bar[label], f"{label}: in_bar={lay.in_bar}, picture {box.top}-{box.bottom}")
            if lay.in_bar:
                assert_(lay.alignment == 8, f"{label}: {lay}")
                assert_(lay.margin_v >= box.bottom + 20, f"{label}: caption starts at {lay.margin_v}, picture ends {box.bottom}")
                assert_(lay.margin_v + 90 <= 1088, f"{label}: caption runs into the bottom 15%")
            else:
                assert_(lay == DEFAULT_CAPTION_LAYOUT, f"{label}: {lay}")
        edge = {b: caption_layout(Box(0, 0, 720, b)).in_bar for b in (977, 978, 979, 1100)}
        assert_(edge[977] and edge[978] and not edge[979] and not edge[1100], f"{edge}")
    results.append(t["passed"])

    with test_case("layout: for any picture shape, in-bar captions never touch the picture or the unsafe bottom") as t:
        for height in range(200, 1281, 2):
            box = Box(0, (1280 - height) // 2, 720, height)
            lay = caption_layout(box)
            if lay.in_bar:
                assert_(lay.margin_v >= box.bottom + 20 and lay.margin_v + 90 <= 1088, f"picture {box.top}-{box.bottom}: {lay}")
    results.append(t["passed"])

    with test_case("the default caption style is exactly today's; a bar layout changes only that one style line") as t:
        assert_(OLD_STYLE in caption._ASS_HEADER, "reference style line is no longer in the header")
        assert_(caption._header() == caption._ASS_HEADER, "default header changed")
        bar = caption._header(canvas.CaptionLayout(8, 902, True))
        diff = [(a, b) for a, b in zip(caption._ASS_HEADER.split("\n"), bar.split("\n")) if a != b]
        assert_(len(diff) == 1 and diff[0][1].endswith(",1,5,3,8,60,60,902,1"), f"{diff}")
        segs, words = [{"start_time": 0.0, "end_time": 3.0}], words_for(3)
        assert_(caption._build_ass(segs, words) == caption._build_ass(segs, words, DEFAULT_CAPTION_LAYOUT), "default differs")
        a, b = caption._build_ass(segs, words), caption._build_ass(segs, words, canvas.CaptionLayout(8, 902, True))
        assert_(a.split("[Events]")[1] == b.split("[Events]")[1], "caption lines or timing changed")
    results.append(t["passed"])

    async def burn(src_path, mode="fit", source_path="__src__"):
        """Make a canvas reel from src_path, burn captions, return the output file."""
        reel, out = os.path.join(tmp, "reel.mp4"), os.path.join(tmp, "captioned.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src_path, "-vf", canvas.fit_filter(), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", reel], check=True)
        segs = [{"start_time": 0.0, "end_time": 6.0}]
        with patch.object(caption, "_fetch_video_data", lambda vid: (segs, words_for(6))), \
                patch.object(settings, "REFRAME_MODE", mode):
            ok = await caption.burn_captions(None, "v1", reel, out, source_path=src_path if source_path == "__src__" else source_path)
        assert_(ok and os.path.exists(out), "burning captions failed")
        return out

    with test_case("real render, 16:9 video: every caption pixel is in the black space below the picture") as t:
        src = os.path.join(tmp, "land.mp4")
        make_src(src, 1280, 720, seconds=6)
        out = await burn(src)
        rows, count = yellow_rows(out, 1000)
        assert_(count > 150, f"no caption visible ({count} yellow pixels)")
        assert_(rows.min() >= 842 and rows.max() < 1088, f"caption rows {rows.min()}-{rows.max()}; picture ends at 842, unsafe from 1088")
    results.append(t["passed"])

    with test_case("real render, 4:3 video: captions also sit below the picture") as t:
        src = os.path.join(tmp, "four3.mp4")
        make_src(src, 640, 480, seconds=6)
        out = await burn(src)
        rows, count = yellow_rows(out, 1000)
        assert_(count > 150 and rows.min() >= 910 and rows.max() < 1088, f"rows {rows.min() if count else None}-{rows.max() if count else None}")
    results.append(t["passed"])

    with test_case("real render, vertical video: captions keep today's position over the picture") as t:
        src = os.path.join(tmp, "vert.mp4")
        make_src(src, 1080, 1920, seconds=6)
        out = await burn(src)
        rows, count = yellow_rows(out, 1000)
        assert_(count > 150 and 760 <= rows.min() and rows.max() <= 880, f"rows {rows.min() if count else None}-{rows.max() if count else None}")
    results.append(t["passed"])

    with test_case("crop mode, an unreadable source or no source all keep today's position") as t:
        src = os.path.join(tmp, "land.mp4")
        out = await burn(src, mode="crop")
        rows, count = yellow_rows(out, 1000)
        assert_(count > 150 and rows.min() >= 760 and rows.max() <= 880, f"crop mode rows {rows.min()}-{rows.max()}")
        with patch.object(settings, "REFRAME_MODE", "fit"):
            assert_(caption._layout_for(None) == DEFAULT_CAPTION_LAYOUT, "no source")
            assert_(caption._layout_for(os.path.join(tmp, "missing.mp4")) == DEFAULT_CAPTION_LAYOUT, "unreadable source")
            assert_(caption._layout_for(src).in_bar, "readable 16:9 source should use the bar")
    results.append(t["passed"])

    with test_case("the pipeline hands the source video to the caption stage") as t:
        from worker.pipeline import Pipeline
        from worker.stages import caption as caption_mod, stitch as stitch_mod

        seen = {}

        async def fake_stitch(pipe, vid, stitch_input, out):
            return True

        async def fake_burn(pipe, vid, video_path, output_path, source_path=None):
            seen["source_path"] = source_path
            return False

        with patch.object(stitch_mod, "stitch_and_caption", fake_stitch), patch.object(caption_mod, "burn_captions", fake_burn):
            await Pipeline("v-cap")._stage_render("source.mp4", "reframed.mp4", "out.mp4", "reels/x.mp4")  # fails later on the missing file; that is fine
        assert_(seen.get("source_path") == "source.mp4", f"{seen}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
