"""Test 27: The verifier's draft has the same crossfades as the final reel (worker/edit/draft.py).

Real ffmpeg on the generated 3-colour test video (red 0-10s, green 10-20s, blue 20-30s). No network, no cost.
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
from test_26_transitions import duration_of, make_three_scenes, seg, stream_kinds


def size_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=s=x:p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def centre_colour(video, t_s):
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok, f"no frame at {t_s}s"
    h, w = frame.shape[:2]
    return frame[h // 2 - 6: h // 2 + 6, w // 2 - 6: w // 2 + 6].reshape(-1, 3).mean(axis=0)


def leftover_drafts():
    return sorted(n for n in os.listdir(tempfile.gettempdir()) if n.startswith("draft_"))


async def main() -> bool:
    section("Test 27: Draft with crossfades")
    from worker.edit import draft as dr, transitions as tr
    from worker.config import settings
    from worker.stages import smart_plan as sp

    results = []
    tmp = tempfile.mkdtemp(prefix="test27_")
    src = os.path.join(tmp, "scenes.mp4")
    make_three_scenes(src)
    segs = [seg(1, 9, "A", "topic_change"), seg(11, 19, "B", "continuous"), seg(21, 29, "C")]

    with test_case("with crossfades: 23.7s on a small 9:16 canvas, a blend at the fade, a clean cut at the hard join") as t:
        before = leftover_drafts()
        out = os.path.join(tmp, "fade.mp4")
        await dr.render_draft(src, segs, out, fade_s=0.3)
        assert_(abs(duration_of(out) - 23.7) < 0.15, f"length {duration_of(out)}")
        assert_(size_of(out) == dr.DRAFT_CANVAS and stream_kinds(out) == ["audio", "video"], f"{size_of(out)} {stream_kinds(out)}")
        refs = {"red": centre_colour(out, 3.0), "green": centre_colour(out, 11.0), "blue": centre_colour(out, 20.0)}

        def nearest(t_s):
            c = centre_colour(out, t_s)
            k = min(refs, key=lambda n: np.linalg.norm(c - refs[n]))
            return k, float(np.linalg.norm(c - refs[k]))

        assert_(nearest(7.0)[0] == "red" and nearest(7.0)[1] < 25, "pure red before the fade")
        assert_(nearest(7.85)[1] > 45, f"a blend in the middle of the fade ({nearest(7.85)[1]:.0f})")
        assert_(nearest(8.4)[0] == "green" and nearest(8.4)[1] < 25, "pure green after")
        for t_s in (15.45, 15.55):
            assert_(nearest(t_s)[0] == "green" and nearest(t_s)[1] < 25, f"hard join, before: t={t_s}")
        for t_s in (15.85, 15.95):
            assert_(nearest(t_s)[0] == "blue" and nearest(t_s)[1] < 25, f"hard join, after: t={t_s}")
        assert_(leftover_drafts() == before, f"temp folders left behind: {set(leftover_drafts()) - set(before)}")
    results.append(t["passed"])

    with test_case("without crossfades (fade_s 0, or no join marked) it is the plain hard-cut draft, exactly as before") as t:
        plain = os.path.join(tmp, "plain.mp4")
        await dr.render_draft(src, segs, plain)
        assert_(abs(duration_of(plain) - 24.0) < 0.15 and size_of(plain)[1] == dr.DRAFT_HEIGHT, f"{duration_of(plain)} {size_of(plain)}")
        zero = os.path.join(tmp, "zero.mp4")
        await dr.render_draft(src, segs, zero, fade_s=0.0)
        assert_(abs(duration_of(zero) - 24.0) < 0.15 and size_of(zero)[1] == dr.DRAFT_HEIGHT, "fade_s=0")
        unmarked = os.path.join(tmp, "unmarked.mp4")
        await dr.render_draft(src, [seg(1, 9, "A"), seg(11, 19, "B"), seg(21, 29, "C")], unmarked, fade_s=0.3)
        assert_(abs(duration_of(unmarked) - 24.0) < 0.15 and size_of(unmarked)[1] == dr.DRAFT_HEIGHT, "no join marked")
    results.append(t["passed"])

    with test_case("if the crossfade draft fails, a hard-cut draft is still produced") as t:
        out = os.path.join(tmp, "fallback.mp4")

        def boom(*a, **k):
            raise RuntimeError("graph exploded")

        with patch.object(tr, "build_command", boom):
            await dr.render_draft(src, segs, out, fade_s=0.3)
        assert_(os.path.exists(out) and abs(duration_of(out) - 24.0) < 0.15 and size_of(out)[1] == dr.DRAFT_HEIGHT,
                f"{os.path.exists(out)}")
    results.append(t["passed"])

    with test_case("through the worker's runner: one command per clip plus one crossfade join on the small canvas") as t:
        cmds = []

        async def recording_runner(cmd):
            cmds.append(cmd)
            open(cmd[-1], "wb").write(b"x")

        with patch.object(tr, "probe_duration", lambda p: 8.0), patch.object(tr, "has_audio_stream", lambda p: True):
            await dr.render_draft(src, segs, os.path.join(tmp, "rec.mp4"), runner=recording_runner, fade_s=0.3)
        assert_(len(cmds) == 4, f"{len(cmds)} commands")
        join = cmds[-1]
        graph = join[join.index("-filter_complex") + 1]
        assert_("xfade=transition=fade:duration=0.300:offset=7.700" in graph and "480/854" in graph, graph[:300])
        assert_("-crf" in join and join[join.index("-crf") + 1] == "32" and "64k" in join, "draft quality settings")
        assert_(sum(1 for c in cmds[:3] if "-ss" in c) == 3, "each clip is cut first")
    results.append(t["passed"])

    with test_case("the filter graph can target another canvas; the default is unchanged") as t:
        small = tr.build_graph([8, 8], [0.3, 0.0], True, canvas=(480, 854)).filter_complex
        big = tr.build_graph([8, 8], [0.3, 0.0], True).filter_complex
        assert_("480/854" in small and "720/1280" not in small, "small canvas")
        assert_("720/1280" in big and "480/854" not in big, "default canvas")
    results.append(t["passed"])

    with test_case("the pipeline passes the crossfade setting to the draft, and 0 when transitions are off") as t:
        seen = []

        async def fake_render(video, segments, out, runner=None, fade_s=None):
            seen.append(fade_s)
            open(out, "wb").write(b"x")
            return out

        async def fake_select(**kw):
            await kw["make_draft"]([{"start_time": 0.0, "end_time": 5.0, "title": "t", "reason": "", "join": None}])
            from worker.edit.select_reel import Selection
            return Selection(ok=True, plan={"shape": "single_stretch", "beats": [{}], "reason": "r"},
                             segments=[{"start_time": 0.0, "end_time": 25.0}], verifier={"verdict": "pass"}, total_seconds=25.0)

        async def noop(*a, **k):
            return None

        async def silences(path, dur=None):
            return []

        async def duration(path):
            return 100.0

        async def run_one(enabled):
            pipe = SimpleNamespace(video_id="v", smart_state={"understanding": object(), "sentences": [], "words": [], "metrics": {}},
                                   reel_meta_extra={}, _heartbeat=noop, _get_video_duration=duration, _run_with_heartbeat=noop)
            with patch.object(sp, "select_reel", fake_select), patch.object(sp, "render_draft", fake_render), \
                    patch.object(sp, "detect_silences", silences), patch.object(sp, "save_plan", lambda *a: None), \
                    patch.object(settings, "TRANSITIONS_ENABLED", enabled), patch.object(settings, "TRANSITION_SECONDS", 0.3):
                await sp.run_select(pipe, "src.mp4")

        await run_one(True)
        await run_one(False)
        assert_(seen == [0.3, 0.0], f"{seen}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
