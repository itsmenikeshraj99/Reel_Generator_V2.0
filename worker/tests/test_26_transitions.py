"""Test 26: Crossfades between montage beats (worker/edit/transitions.py + stitch + caption timing).

Real ffmpeg on a generated 3-colour test video (red 0-10s, green 10-20s, blue 20-30s, each with its own
tone), so every join can be inspected by pixel colour. No network, no cost.
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


def make_three_scenes(path, audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in ("red", "green", "blue"):
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=1280x720:r=25:d=10"]
    graph = "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]"
    maps = ["-map", "[v]"]
    if audio:
        for f in (440, 660, 880):
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=10"]
        graph += ";[3:a][4:a][5:a]concat=n=3:v=0:a=1[a]"
        maps += ["-map", "[a]"]
    cmd += ["-filter_complex", graph] + maps + ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac"] if audio else ["-an"]
    subprocess.run(cmd + [path], check=True)


def colour_at(video, t_s):
    """Mean BGR of a 12x12 patch in the middle of the picture at time t."""
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000)
    ok, frame = cap.read()
    cap.release()
    assert ok, f"no frame at {t_s}s"
    return frame[634:646, 354:366].reshape(-1, 3).mean(axis=0)


def duration_of(path, kind="format"):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", f"{kind}=duration", "-of", "default=nw=1:nk=1", path],
                         capture_output=True, text=True, check=True).stdout.split()
    return float(out[0])


def stream_kinds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.split()
    return sorted(out)


def seg(a, b, title="t", join=None):
    return {"start_time": float(a), "end_time": float(b), "title": title, "reason": "", "join": join}


class FakeDB:
    def __init__(self, segments):
        self.plan = {"id": "p1", "segments": segments}

    def table(self, name):
        db = self

        class Q:
            def select(self, *a): return self
            def eq(self, *a): return self
            def order(self, *a): return self
            def limit(self, *a): return self
            def execute(self):
                return SimpleNamespace(data=[db.plan])

        return Q()


async def main() -> bool:
    section("Test 26: Smooth transitions")
    from worker.config import WorkerSettings, settings
    from worker.edit import overlays as ov, transitions as tr
    from worker.stages import caption, stitch
    from worker.text.snap import snap_plan

    results = []
    tmp = tempfile.mkdtemp(prefix="test26_")

    # ── pure logic ────────────────────────────────────────────────────
    with test_case("which joins fade: only topic/point changes between clips that are long enough") as t:
        segs = [seg(0, 8, join="topic_change"), seg(10, 18, join="continuous"), seg(20, 28, join="point_change"),
                seg(30, 38, join="topic_change")]
        assert_(tr.overlaps_for(segs, 0.3) == [0.3, 0.0, 0.3, 0.0], f"{tr.overlaps_for(segs, 0.3)}")
        assert_(tr.overlaps_for(segs, 0.0) == [0.0] * 4 and tr.overlaps_for(segs[:1], 0.3) == [0.0], "off / single clip")
        assert_(tr.overlaps_for([seg(0, 8), seg(10, 18), seg(20, 28, join="topic_change")], 0.3) == [0.0, 0.0, 0.0],
                "no join marked, or the last clip's join, never fades")
        assert_(tr.overlaps_for([seg(0, 8), seg(10, 18, join="topic_change"), seg(20, 28)], 0.3) == [0.0, 0.3, 0.0],
                "a join in the middle fades the join after that clip")
        short = [seg(0, 8, join="topic_change"), seg(10, 10.7, join="topic_change"), seg(20, 28)]
        assert_(tr.overlaps_for(short, 0.3) == [0.0, 0.0, 0.0], "a clip shorter than 2 x fade + 0.2s blocks both its joins")
        assert_(tr.overlaps_for([seg(0, 8, join="weird"), seg(10, 18)], 0.3) == [0.0, 0.0], "unknown join type")
    results.append(t["passed"])

    with test_case("the shared timeline: later beats start earlier by the overlaps; no overlaps = plain running total") as t:
        d = [8.0, 8.0, 8.0, 8.0]
        assert_(tr.beat_starts(d, [0, 0, 0, 0]) == [0.0, 8.0, 16.0, 24.0], "no fades")
        starts = tr.beat_starts(d, [0.3, 0.0, 0.3, 0.0])
        assert_([round(x, 3) for x in starts] == [0.0, 7.7, 15.7, 23.4], f"{starts}")
        assert_(abs(tr.reel_length(d, [0.3, 0.0, 0.3, 0.0]) - 31.4) < 1e-9, f"{tr.reel_length(d, [0.3, 0.0, 0.3, 0.0])}")
        assert_(tr.beat_starts(d, []) == [0.0, 8.0, 16.0, 24.0], "missing overlaps list")
    results.append(t["passed"])

    with test_case("the filter graph: xfade/acrossfade for fades, concat (with the timebase fix) for hard cuts, right offsets") as t:
        g = tr.build_graph([8, 8, 8, 8], [0.3, 0.0, 0.3, 0.0], has_audio=True)
        fc = g.filter_complex
        assert_(fc.count("xfade=") == 2 and fc.count("acrossfade=") == 2, "two fades")
        assert_(fc.count("concat=n=2:v=1:a=0") == 1 and fc.count("concat=n=2:v=0:a=1") == 1, "one hard join, video and audio")
        assert_("xfade=transition=fade:duration=0.300:offset=7.700" in fc, "first offset = clip length - fade")
        assert_("duration=0.300:offset=23.400" in fc, "the third fade starts after 8+8+8 seconds minus the first overlap")
        assert_(fc.count("settb=1/30,setpts=PTS-STARTPTS[vj") == 1, "timebase is normalised after the concat")
        assert_(abs(g.length - 31.4) < 1e-9 and g.video_label == "vout" and "[vj3]format=yuv420p[vout]" in fc and g.audio_label == "aj3", f"{g.length} {g.video_label}")
        for i in range(4):
            assert_(f"[{i}:v]" in fc and f"[{i}:a]" in fc, f"input {i} missing")
        silent = tr.build_graph([8, 8], [0.3, 0.0], has_audio=False)
        assert_("acrossfade" not in silent.filter_complex and "[0:a]" not in silent.filter_complex and silent.audio_label is None, "no audio branch")
        try:
            tr.build_graph([], [], True)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
    results.append(t["passed"])

    with test_case("the snapper hands each beat's join type to its segment") as t:
        words = [{"text": "w", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(40)]
        sents = [{"id": f"S{k + 1}", "first_word": k * 10, "last_word": k * 10 + 9} for k in range(4)]
        plan = {"beats": [{"from_id": "S1", "to_id": "S1", "join": "topic_change"}, {"from_id": "S3", "to_id": "S3", "join": "continuous"}]}
        r = snap_plan(plan, sents, words, [])
        assert_([s["join"] for s in r.segments] == ["topic_change", "continuous"], f"{r.segments}")
    results.append(t["passed"])

    with test_case("settings: off by default, 0.3s, and only 0.15 to 0.6s accepted") as t:
        assert_(settings.TRANSITIONS_ENABLED is False and settings.TRANSITION_SECONDS == 0.3, "defaults")
        base = dict(SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g")
        assert_(WorkerSettings(**base, TRANSITION_SECONDS=0.15).TRANSITION_SECONDS == 0.15, "lower bound")
        for bad in (0.05, 0.7, 2):
            try:
                WorkerSettings(**base, TRANSITION_SECONDS=bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
    results.append(t["passed"])

    with test_case("on-screen label timing follows the shortened timeline") as t:
        segs = [seg(0, 8, "Hook", "topic_change"), seg(10, 18, "The Study", "continuous"), seg(20, 28, "Take Control")]
        plain = ov.plan_items("Title", "montage", segs)
        faded = ov.plan_items("Title", "montage", segs, [0.3, 0.0, 0.0])
        assert_([(i.text, round(i.start, 2)) for i in plain if i.kind == "label"] == [("The Study", 8.0), ("Take Control", 16.0)], "plain")
        assert_([(i.text, round(i.start, 2)) for i in faded if i.kind == "label"] == [("The Study", 7.7), ("Take Control", 15.7)], "faded")
    results.append(t["passed"])

    # ── real renders ──────────────────────────────────────────────────
    src = os.path.join(tmp, "scenes.mp4")
    make_three_scenes(src)
    segments = [seg(1, 9, "Hook line", "topic_change"), seg(11, 19, "The Study", "continuous"), seg(21, 29, "Take Control")]

    async def run_stitch(source, segs, enabled=True, pipeline=None, name="reel.mp4"):
        out = os.path.join(tmp, name)
        if os.path.exists(out):
            os.remove(out)
        with patch.object(stitch, "supabase_client", SimpleNamespace(table=FakeDB(segs).table)), \
                patch.object(settings, "TRANSITIONS_ENABLED", enabled), patch.object(settings, "TRANSITION_SECONDS", 0.3):
            ok = await stitch.stitch_and_caption(pipeline, "v1", source, out)
        assert_(ok and os.path.exists(out), "stitch failed")
        return out

    def classify(video, t_s, refs):
        c = colour_at(video, t_s)
        near = min(refs, key=lambda k: np.linalg.norm(c - refs[k]))
        return near, float(np.linalg.norm(c - refs[near]))

    with test_case("real render: 8s+8s+8s with one fade and one hard cut is 23.7s; the fade blends, the hard cut does not") as t:
        pipe = SimpleNamespace(transition_info=None, reel_meta_extra={})
        out = await run_stitch(src, segments, pipeline=pipe)
        assert_(abs(duration_of(out) - 23.7) < 0.12, f"length {duration_of(out)}")
        assert_(stream_kinds(out) == ["audio", "video"], f"{stream_kinds(out)}")
        assert_(abs(duration_of(out, "stream") - duration_of(out)) < 0.2, "picture and sound drifted apart")
        refs = {"red": colour_at(out, 3.0), "green": colour_at(out, 11.0), "blue": colour_at(out, 20.0)}
        assert_(classify(out, 7.0, refs)[0] == "red" and classify(out, 7.0, refs)[1] < 25, "just before the fade is pure red")
        _, blend_dist = classify(out, 7.85, refs)
        assert_(blend_dist > 45, f"at the middle of the fade the picture must be a blend, distance {blend_dist:.0f}")
        assert_(classify(out, 8.4, refs)[0] == "green" and classify(out, 8.4, refs)[1] < 25, "after the fade it is pure green")
        for t_s in (15.45, 15.55, 15.62):
            name, dist = classify(out, t_s, refs)
            assert_(name == "green" and dist < 25, f"hard cut: t={t_s} should be pure green ({name}, {dist:.0f})")
        for t_s in (15.76, 15.85, 15.95):
            name, dist = classify(out, t_s, refs)
            assert_(name == "blue" and dist < 25, f"hard cut: t={t_s} should be pure blue ({name}, {dist:.0f})")
        assert_(pipe.transition_info == {"overlaps": [0.3, 0.0, 0.0], "fade_s": 0.3, "count": 1}, f"{pipe.transition_info}")
        assert_(pipe.reel_meta_extra["transitions"] == {"count": 1, "seconds": 0.3}, f"{pipe.reel_meta_extra}")
    results.append(t["passed"])

    with test_case("flag off: plain hard cuts, 24s, nothing recorded") as t:
        pipe = SimpleNamespace(transition_info="stale", reel_meta_extra={})
        out = await run_stitch(src, segments, enabled=False, pipeline=pipe)
        assert_(abs(duration_of(out) - 24.0) < 0.12, f"length {duration_of(out)}")
        refs = {"red": colour_at(out, 3.0), "green": colour_at(out, 11.0)}
        assert_(classify(out, 7.9, refs)[1] < 25 and classify(out, 8.1, refs)[1] < 25, "the join must be a clean cut")
        assert_(pipe.transition_info is None and pipe.reel_meta_extra == {}, f"{pipe.transition_info} {pipe.reel_meta_extra}")
    results.append(t["passed"])

    with test_case("if the crossfade render fails, the reel is still made with hard cuts") as t:
        pipe = SimpleNamespace(transition_info=None, reel_meta_extra={})

        def boom(*a, **k):
            raise RuntimeError("filter graph exploded")

        with patch.object(tr, "render", boom):
            out = await run_stitch(src, segments, pipeline=pipe)
        assert_(abs(duration_of(out) - 24.0) < 0.12, f"length {duration_of(out)}")
        assert_(pipe.transition_info is None and "transitions" not in pipe.reel_meta_extra, "must not claim transitions")
    results.append(t["passed"])

    with test_case("short clips and unmarked joins render as hard cuts even with the flag on") as t:
        pipe = SimpleNamespace(transition_info=None, reel_meta_extra={})
        shorty = [seg(1, 11, "A", "topic_change"), seg(12, 12.6, "B", "topic_change"), seg(20, 30, "C")]
        out = await run_stitch(src, shorty, pipeline=pipe)
        assert_(abs(duration_of(out) - 20.6) < 0.15 and pipe.transition_info is None, f"{duration_of(out)} {pipe.transition_info}")
    results.append(t["passed"])

    with test_case("a source with no audio still gets its crossfades (and no audio stream is invented)") as t:
        silent = os.path.join(tmp, "silent.mp4")
        make_three_scenes(silent, audio=False)
        out = await run_stitch(silent, segments, name="silent_reel.mp4")
        assert_(abs(duration_of(out) - 23.7) < 0.12 and stream_kinds(out) == ["video"], f"{duration_of(out)} {stream_kinds(out)}")
    results.append(t["passed"])

    with test_case("captions land on the right moment after a crossfade (real burn, located by pixel)") as t:
        pipe = SimpleNamespace(transition_info=None, reel_meta_extra={})
        reel = await run_stitch(src, segments, pipeline=pipe, name="for_captions.mp4")
        # one word, in the third beat, at source 22.0-22.4s = 1.0s into that beat
        words = [{"text": "HELLO", "start": 22.0, "end": 22.4}]
        out = os.path.join(tmp, "captioned.mp4")
        with patch.object(caption, "_fetch_video_data", lambda vid: (segments, words)), \
                patch.object(settings, "TEXT_OVERLAYS_ENABLED", False), patch.object(settings, "REFRAME_MODE", "fit"):
            ok = await caption.burn_captions(pipe, "v1", reel, out, source_path=src)
        assert_(ok, "burn failed")
        shown = lambda t_s: yellow_rows(out, t_s * 1000)[1] > 60
        assert_(not shown(16.4), "too early")
        assert_(shown(16.85), "the word should be visible at 16.85s (beat 3 starts at 15.7s, not 16.0s)")
        assert_(not shown(17.35), "and gone by 17.35s (a timeline that ignored the fade would still show it)")
        # same words with the stitch's fade information removed: the unshifted timeline
        plain_out = os.path.join(tmp, "captioned_plain.mp4")
        with patch.object(caption, "_fetch_video_data", lambda vid: (segments, words)), \
                patch.object(settings, "TEXT_OVERLAYS_ENABLED", False), patch.object(settings, "REFRAME_MODE", "fit"):
            await caption.burn_captions(SimpleNamespace(transition_info=None, reel_meta_extra={}), "v1", reel, plain_out, source_path=src)
        assert_(not yellow_rows(plain_out, 16.85 * 1000)[1] > 60, "without the shift the word would be late: this check proves the shift matters")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
