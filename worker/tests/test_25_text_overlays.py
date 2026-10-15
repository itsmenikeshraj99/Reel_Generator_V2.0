"""Test 25: Hook title and point labels (worker/edit/overlays.py, stages/caption.py, bundled fonts).

Real ffmpeg: text is fitted by measuring renders, and the burned text is located by its pixels on a
mid-grey picture (text is pure white, captions are yellow). No network, no cost.
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
from test_24_caption_layout import yellow_rows

HINDI_TITLE = "फोन आपको कैसे कमज़ोर बनाता है"
HINDI_LABEL = "अध्ययन का नतीजा"


def make_gray(path, w, h, seconds):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}:r=25:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", path], check=True)


def frame_at(video, t_ms):
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
    ok, frame = cap.read()
    cap.release()
    assert ok, "could not read a frame"
    return frame


def white_rows(video, t_ms, y0=0, y1=1280):
    """(rows with pure-white text pixels in [y0, y1), number of such pixels)."""
    f = frame_at(video, t_ms)[y0:y1]
    mask = (f[:, :, 0] > 235) & (f[:, :, 1] > 235) & (f[:, :, 2] > 235)
    rows = np.where(mask.any(axis=1))[0] + y0
    return rows, int(mask.sum())


def words_for(seconds):
    return [{"text": f"word{i}", "start": round(i * 0.4, 2), "end": round(i * 0.4 + 0.35, 2)} for i in range(int(seconds / 0.4))]


async def main() -> bool:
    section("Test 25: Hook title and point labels")
    from worker.config import settings
    from worker.edit import canvas, overlays as ov
    from worker.edit.canvas import Box, picture_box
    from worker.stages import caption

    results = []
    tmp = tempfile.mkdtemp(prefix="test25_")

    with test_case("the bundled fonts and their licences are in the repo, and are the real families") as t:
        for name, min_bytes in (("NotoSans-Bold.ttf", 600_000), ("NotoSansDevanagari-Bold.ttf", 200_000)):
            data = open(os.path.join(ov.FONTS_DIR, name), "rb").read()
            assert_(data[:4] == b"\x00\x01\x00\x00" and len(data) >= min_bytes, f"{name}: {len(data)} bytes")
        dev = open(os.path.join(ov.FONTS_DIR, "NotoSansDevanagari-Bold.ttf"), "rb").read()
        assert_("Noto Sans Devanagari".encode("utf-16-be") in dev, "family name (stored as UTF-16 in the font)")
        for lic in ("OFL-NotoSans.txt", "OFL-NotoSansDevanagari.txt"):
            text = open(os.path.join(ov.FONTS_DIR, lic), encoding="utf-8", errors="replace").read()
            assert_("SIL OPEN FONT LICENSE Version 1.1" in text and "Noto Project Authors" in text, lic)
    results.append(t["passed"])

    with test_case("script detection picks the Devanagari font only for Devanagari text") as t:
        assert_(ov.script_of("Phones make you dumber") == "lat" and ov.font_for("Hello") == "Noto Sans", "latin")
        assert_(ov.script_of(HINDI_TITLE) == "dev" and ov.font_for(HINDI_TITLE) == "Noto Sans Devanagari", "devanagari")
        assert_(ov.script_of("Phone फोन") == "dev" and ov.script_of("") == "lat", "mixed / empty")
    results.append(t["passed"])

    segs = [{"start_time": 0.0, "end_time": 4.0, "title": "Hook line"}, {"start_time": 10.0, "end_time": 14.0, "title": "The Phone Study"},
            {"start_time": 20.0, "end_time": 24.0, "title": "S12-S14"}, {"start_time": 30.0, "end_time": 34.0, "title": "Take Control"}]

    with test_case("timeline: title for 2.5s, a 1.2s label at the start of each later montage beat, none for the hook beat") as t:
        items = ov.plan_items("Phones Make You Dumber", "montage", segs)
        assert_([(i.kind, i.text, i.start, i.end) for i in items] ==
                [("title", "Phones Make You Dumber", 0.0, 2.5), ("label", "The Phone Study", 4.0, 5.2),
                 ("label", "Take Control", 12.0, 13.2)], f"{[(i.kind, i.text, i.start, i.end) for i in items]}")
        assert_([i.kind for i in ov.plan_items("T", "single_stretch", segs)] == ["title"], "a single stretch shows only the title")
        assert_(ov.plan_items("", "single_stretch", segs) == [], "no title, no overlay")
    results.append(t["passed"])

    with test_case("timeline edge cases: short beats and short reels clamp, empty or zero-length parts are ignored") as t:
        short = [{"start_time": 0.0, "end_time": 1.5, "title": "A"}, {"start_time": 5.0, "end_time": 5.8, "title": "Quick"},
                 {"start_time": 9.0, "end_time": 9.0, "title": "Zero"}, {"start_time": 12.0, "end_time": 14.0, "title": ""}]
        items = ov.plan_items("Title", "montage", short)
        assert_(abs(items[0].end - 2.5) < 1e-9, f"title keeps its 2.5s when the reel is longer: {items[0].end}")
        assert_([(i.text, round(i.start, 2), round(i.end, 2)) for i in items[1:]] == [("Quick", 1.5, 2.3)], f"{[(i.text, i.start, i.end) for i in items[1:]]}")
        tiny = ov.plan_items("Title", "montage", short[:2])  # a 2.3s reel
        assert_(abs(tiny[0].end - 2.3) < 1e-9, f"title ends with a short reel: {tiny[0].end}")
        assert_(ov.plan_items("T", "montage", []) == [], "no segments")
    results.append(t["passed"])

    with test_case("subtitle lines: right styles per script, layer 1, fade, a real line break, no control characters") as t:
        lines = ov.ass_lines([ov.Item("title", "Why a dead phone\nstill lowers {IQ}\\", 0.0, 2.5, 48, 2),
                              ov.Item("label", HINDI_LABEL, 4.0, 5.2, 40, 1), ov.Item("label", "Take Control", 12.0, 13.2, 36, 1)])
        assert_(lines[0].startswith("Dialogue: 1,0:00:00.00,0:00:02.50,TitleLat,,0,0,0,,"), lines[0])
        assert_(r"\fs48\fad(200,300)" in lines[0] and r"Why a dead phone\Nstill lowers IQ" in lines[0], lines[0])
        assert_("{" not in lines[0].split("}", 1)[1] and "\\\\" not in lines[0], "control characters leaked into the text")
        assert_(",LabelDev,," in lines[1] and "0:00:04.00,0:00:05.20" in lines[1], lines[1])
        assert_(",LabelLat,," in lines[2], lines[2])
    results.append(t["passed"])

    shapes = {"16:9": (1280, 720), "4:3": (640, 480), "square": (1000, 1000), "vertical": (1080, 1920), "ultra-wide": (2560, 1080)}
    with test_case("layout: text goes above the picture when there is room, otherwise over it on a dark box; never in the top 12%") as t:
        for name, (w, h) in shapes.items():
            box = picture_box(w, h)
            lay = ov.overlay_layout(box)
            assert_(lay.boxed == (box.top < 397), f"{name}: boxed={lay.boxed}, picture starts at {box.top}")
            assert_(lay.title_margin_v >= int(1280 * 0.12), f"{name}: title starts at {lay.title_margin_v}, inside the top 12%")
            if not lay.boxed:  # title (up to 2 lines at the largest size) must end above the label, which sits above the picture
                title_bottom = lay.title_margin_v + int(2 * 56 * 1.37)
                label_top = box.top - 24 - int(40 * 1.37)
                assert_(title_bottom <= label_top, f"{name}: title ends {title_bottom}, label starts {label_top}")
                assert_(lay.label_alignment == 2 and lay.label_margin_v == 1280 - box.top + 24, f"{name}: {lay}")
            else:
                assert_(lay.label_alignment == 8 and lay.label_margin_v == lay.title_margin_v, f"{name}: {lay}")
        assert_(ov.overlay_layout(Box(0, 0, 720, 1280)).boxed, "full-canvas picture (crop mode)")
        styles = ov.style_lines(ov.overlay_layout(picture_box(1280, 720))).split("\n")
        assert_([s.split(",")[0] for s in styles] == ["Style: TitleLat", "Style: TitleDev", "Style: LabelLat", "Style: LabelDev"], styles)
        assert_(",3,12,0," in ov.style_lines(ov.overlay_layout(Box(0, 0, 720, 1280))).split("\n")[0], "boxed style should use an opaque box")
    results.append(t["passed"])

    with test_case("measuring: longer text is wider, empty is 0, bigger is wider, big batches work") as t:
        w = ov.measure_widths(["Phones", "Phones Make You Dumber", "", "WWWWWWWWWWWWWWWW"], ov.LATIN_FONT, 44)
        assert_(w[2] == 0 and 0 < w[0] < w[1] < w[3] < 1000, f"{w}")
        assert_(ov.measure_widths(["Phones Make You Dumber"], ov.LATIN_FONT, 56)[0] > w[1], "a bigger size must be wider")
        many = ov.measure_widths([f"line number {i}" for i in range(40)], ov.LATIN_FONT, 44)
        assert_(len(many) == 40 and all(30 < x < 500 for x in many), f"{many[:5]}")
        hi = ov.measure_widths([HINDI_TITLE], ov.DEVANAGARI_FONT, 44)[0]
        assert_(120 < hi < 1000, f"Hindi width {hi}")
        assert_(hi != ov.measure_widths([HINDI_TITLE], "Noto Sans", 44)[0], "the Devanagari font must be the one actually drawing Hindi")
    results.append(t["passed"])

    with test_case("fitting a title: short stays on one line at the biggest size; long wraps into two lines that each fit") as t:
        text, size, lines = ov.fit_title("Phones Make You Dumber")
        assert_(lines == 1 and size == 56 and "\n" not in text, f"{(text, size, lines)}")
        long = "Why a dead phone on your desk still lowers your"
        text, size, lines = ov.fit_title(long)
        assert_(lines == 2 and text.count("\n") == 1 and text.replace("\n", " ") == long, f"{(text, size, lines)}")
        for part in text.split("\n"):
            assert_(ov.measure_widths([part], ov.LATIN_FONT, size)[0] <= ov.MAX_TEXT_WIDTH, f"line too wide: {part!r}")
        text, size, lines = ov.fit_title(HINDI_TITLE)
        assert_(lines <= 2 and all(ov.measure_widths([p], ov.DEVANAGARI_FONT, size)[0] <= ov.MAX_TEXT_WIDTH for p in text.split("\n")), f"{(text, size, lines)}")
    results.append(t["passed"])

    with test_case("fitting: the size is the largest that works, and unfittable text is dropped instead of overflowing") as t:
        text, size, lines = ov.fit_title("Why a dead phone on your desk still lowers your focus")
        if size != ov.TITLE_SIZES[0]:
            bigger = ov.TITLE_SIZES[ov.TITLE_SIZES.index(size) - 1]
            words = text.replace("\n", " ").split()
            cands = [" ".join(words)] + [x for k in range(1, len(words)) for x in (" ".join(words[:k]), " ".join(words[k:]))]
            w = ov.measure_widths(cands, ov.LATIN_FONT, bigger)
            fits = w[0] <= ov.MAX_TEXT_WIDTH or any(max(w[2 * k - 1], w[2 * k]) <= ov.MAX_TEXT_WIDTH for k in range(1, len(words)))
            assert_(not fits, f"size {size} chosen although {bigger} fits")
        assert_(ov.fit_title("W" * 90)[2] == 0, "an unbreakable 90-letter string cannot fit")
        assert_(ov.fit_title("")[2] == 0, "empty")
        label, lsize = ov.fit_label("The Phone Study")
        assert_(label == "The Phone Study" and lsize == 40, f"{(label, lsize)}")
        long_label, lsize = ov.fit_label("An extremely long point label for this beat")
        assert_("\n" not in long_label and ov.measure_widths([long_label], ov.LATIN_FONT, lsize)[0] <= ov.MAX_TEXT_WIDTH, f"{(long_label, lsize)}")
    results.append(t["passed"])

    with test_case("captions: English is unchanged; Devanagari cues use the Devanagari caption style; overlays are appended") as t:
        csegs, words = [{"start_time": 0.0, "end_time": 3.0}], words_for(3)
        plain = caption._build_ass(csegs, words)
        assert_("DefaultDev" not in plain and ",Default,,0,0,0,," in plain, "English captions must not change")
        hi_words = [{"text": HINDI_TITLE.split()[0], "start": 0.1, "end": 0.5}, {"text": HINDI_TITLE.split()[1], "start": 0.5, "end": 0.9}]
        hindi = caption._build_ass(csegs, hi_words)
        assert_("Style: DefaultDev,Noto Sans Devanagari," in hindi and ",DefaultDev,,0,0,0,," in hindi, "Hindi captions style")
        assert_(hindi.count("Style: DefaultDev") == 1, "style defined once")
        lay = ov.overlay_layout(picture_box(1280, 720))
        full = caption._build_ass(csegs, words, canvas.DEFAULT_CAPTION_LAYOUT, ["Dialogue: 1,0:00:00.00,0:00:02.50,TitleLat,,0,0,0,,x"], ov.style_lines(lay))
        assert_(full.count("Style: TitleLat") == 1 and full.index("Style: TitleLat") < full.index("[Events]"), "styles belong in the style section")
        assert_(full.rstrip().split("\n")[-1].startswith("Dialogue: 1,"), "overlay lines come after the captions")
        assert_(caption._build_ass(csegs, words, canvas.DEFAULT_CAPTION_LAYOUT, (), "") == plain, "no overlays = identical output")
    results.append(t["passed"])

    # ── real renders ──────────────────────────────────────────────────
    segments = [{"start_time": 0.0, "end_time": 4.0, "title": "Hook line"}, {"start_time": 4.0, "end_time": 8.0, "title": "The Phone Study"},
                {"start_time": 8.0, "end_time": 12.0, "title": "Take Control"}]

    async def burn(src, plan, label_titles=None, enabled=True, pipeline=None, fail=False):
        reel, out = os.path.join(tmp, "reel.mp4"), os.path.join(tmp, "captioned.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", canvas.fit_filter(), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", reel], check=True)
        segs_used = [dict(s, title=label_titles[i]) for i, s in enumerate(segments)] if label_titles else segments
        patches = [patch.object(caption, "_fetch_video_data", lambda vid: (segs_used, words_for(12))),
                   patch.object(caption, "_fetch_plan", lambda vid: plan),
                   patch.object(settings, "REFRAME_MODE", "fit"), patch.object(settings, "TEXT_OVERLAYS_ENABLED", enabled)]
        if fail:
            async def boom(items):
                raise RuntimeError("measuring exploded")
            patches.append(patch.object(ov, "fit_items", boom))
        for p in patches:
            p.start()
        try:
            ok = await caption.burn_captions(pipeline, "v1", reel, out, source_path=src)
        finally:
            for p in reversed(patches):
                p.stop()
        assert_(ok and os.path.exists(out), "burning failed")
        return out

    land = os.path.join(tmp, "land.mp4")
    make_gray(land, 1280, 720, 12)
    PLAN = {"title": "Phones Make You Dumber", "shape": "montage"}

    with test_case("real render, 16:9 montage: the title is above the picture for 2.5s, a label shows at the start of beat 2 only") as t:
        meta = SimpleNamespace(reel_meta_extra={})
        out = await burn(land, PLAN, pipeline=meta)
        rows, count = white_rows(out, 1000)
        assert_(count > 400, f"no title visible ({count} white pixels)")
        assert_(rows.min() >= 153 and rows.max() < 438, f"title rows {rows.min()}-{rows.max()}; allowed 153-437")
        assert_(white_rows(out, 3300, 0, 438)[1] < 30, "the title should be gone after 2.5s")
        rows, count = white_rows(out, 4500, 0, 438)
        assert_(count > 150 and rows.min() >= 330 and rows.max() < 438, f"label rows {rows.min() if count else None}-{rows.max() if count else None}; must sit just above the picture")
        assert_(white_rows(out, 6500, 0, 438)[1] < 30, "the label should be gone after 1.2s")
        crows, ccount = yellow_rows(out, 1000)
        assert_(ccount > 150 and crows.min() >= 842, f"captions moved: {crows.min() if ccount else None}")
        assert_(meta.reel_meta_extra["overlays"] == {"title": True, "labels": 2, "boxed": False}, f"{meta.reel_meta_extra}")
    results.append(t["passed"])

    with test_case("real render: a Hindi title and label in Devanagari are drawn within the zone") as t:
        hindi_plan = {"title": HINDI_TITLE, "shape": "montage"}
        out = await burn(land, hindi_plan, label_titles=[HINDI_LABEL, HINDI_LABEL, HINDI_LABEL])
        rows, count = white_rows(out, 1000)
        assert_(count > 400 and rows.min() >= 153 and rows.max() < 438, f"Hindi title rows {rows.min() if count else None}-{rows.max() if count else None}")
        rows, count = white_rows(out, 4500, 0, 438)
        assert_(count > 100 and rows.max() < 438, f"Hindi label {count}")
    results.append(t["passed"])

    with test_case("real render, vertical video: the title sits over the top of the picture on a dark box") as t:
        vert = os.path.join(tmp, "vert.mp4")
        make_gray(vert, 1080, 1920, 12)
        out = await burn(vert, PLAN)
        rows, count = white_rows(out, 1000)
        assert_(count > 400 and rows.min() >= 153 and rows.max() < 420, f"rows {rows.min() if count else None}-{rows.max() if count else None}")
        f = frame_at(out, 1000)
        band = f[160:340, :, :].mean(axis=2)
        assert_(band.min() < 70 and f[700:800, :, :].mean() > 110, "the box behind the title should be darker than the grey picture")
    results.append(t["passed"])

    with test_case("flag off, a classic (no smart plan) reel, and a failing overlay all leave a normal captioned reel") as t:
        meta = SimpleNamespace(reel_meta_extra={})
        off = await burn(land, PLAN, enabled=False, pipeline=meta)
        assert_(white_rows(off, 1000, 0, 438)[1] < 30 and yellow_rows(off, 1000)[1] > 150, "flag off must show captions only")
        assert_(meta.reel_meta_extra == {}, "flag off must not touch the metadata")
        classic = await burn(land, None, pipeline=meta)
        assert_(white_rows(classic, 1000, 0, 438)[1] < 30, "no smart plan, no overlay")
        assert_(meta.reel_meta_extra["overlays"] == {"skipped": "no smart plan"}, f"{meta.reel_meta_extra}")
        meta2 = SimpleNamespace(reel_meta_extra={})
        broken = await burn(land, PLAN, pipeline=meta2, fail=True)
        assert_(white_rows(broken, 1000, 0, 438)[1] < 30 and yellow_rows(broken, 1000)[1] > 150, "captions must survive a failing overlay")
        assert_("measuring exploded" in meta2.reel_meta_extra["overlays"]["error"], f"{meta2.reel_meta_extra}")
    results.append(t["passed"])

    with test_case("Devanagari captions render with the bundled font") as t:
        out_dir = os.path.join(tmp, "hc")
        os.makedirs(out_dir, exist_ok=True)
        reel = os.path.join(out_dir, "r.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", land, "-vf", canvas.fit_filter(), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", reel], check=True)
        hw = [{"text": w, "start": i * 0.5, "end": i * 0.5 + 0.45} for i, w in enumerate(HINDI_TITLE.split() * 3)]
        out = os.path.join(out_dir, "o.mp4")
        with patch.object(caption, "_fetch_video_data", lambda vid: ([{"start_time": 0.0, "end_time": 12.0}], hw)), \
                patch.object(settings, "TEXT_OVERLAYS_ENABLED", False), patch.object(settings, "REFRAME_MODE", "fit"):
            ok = await caption.burn_captions(None, "v1", reel, out, source_path=land)
        rows, count = yellow_rows(out, 700)
        assert_(ok and count > 300 and rows.min() >= 842, f"Hindi caption pixels {count}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
