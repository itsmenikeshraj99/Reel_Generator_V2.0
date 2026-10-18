"""Test 31: Every reel is an ordinary, shareable MP4 (H.264 in 4:2:0, AAC, index at the front).

Players other than VLC (phones, WhatsApp, Windows Media Player, browsers) cannot open 4:4:4 video. The
crossfade step used to hand back 4:4:4. This test runs the real stitch -> captions -> music chain on both a
4:2:0 and a deliberately awkward 4:4:4 source, with and without crossfades, and checks each result.
No network, no cost.
"""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section
from test_26_transitions import FakeDB, seg
from test_29_music_mix import make_tone

OK_PROFILES = ("Baseline", "Constrained Baseline", "Main", "High")


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def make_source(path, pix_fmt):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in ("red", "green", "blue"):
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=1280x720:r=25:d=10"]
    for f in (440, 660, 880):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=10"]
    cmd += ["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a][4:a][5:a]concat=n=3:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", pix_fmt, "-c:a", "aac", path]
    run(cmd)


def probe(path):
    v = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,profile,pix_fmt,width,height", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True).stdout.strip().split(",")
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    data = open(path, "rb").read(200000)
    moov, mdat = data.find(b"moov"), data.find(b"mdat")
    return {"codec": v[0], "profile": v[1], "pix_fmt": v[4] if len(v) > 4 else v[2], "audio": a,
            "faststart": 0 < moov < mdat or (moov > 0 and mdat < 0), "brand": data[8:12], "raw": v}


def check(path, label):
    p = probe(path)
    assert_(p["codec"] == "h264", f"{label}: codec {p['codec']}")
    assert_("yuv420p" in p["raw"], f"{label}: pixel format {p['raw']}")
    assert_(any(p["profile"] == ok for ok in OK_PROFILES), f"{label}: profile {p['profile']}")
    assert_(p["audio"] == "aac", f"{label}: audio {p['audio']}")
    assert_(p["brand"] in (b"isom", b"mp42", b"iso2"), f"{label}: brand {p['brand']}")
    assert_(p["faststart"], f"{label}: index (moov) must come before the media data")


async def main() -> bool:
    section("Test 31: Output is a shareable MP4")
    from worker.config import settings
    from worker.edit import mix
    from worker.stages import caption, stitch

    results = []
    tmp = tempfile.mkdtemp(prefix="test31_")
    segments = [seg(1, 9, "Hook line", "topic_change"), seg(11, 19, "The Study", "continuous"), seg(21, 29, "Take Control")]
    words = [{"text": "HELLO", "start": 2.0, "end": 2.5}, {"text": "WORLD", "start": 12.0, "end": 12.5}]
    music = os.path.join(tmp, "music.wav")
    make_tone(music, 40, 220)

    async def chain(source, crossfades, name):
        out = os.path.join(tmp, f"{name}_stitched.mp4")
        pipe = SimpleNamespace(transition_info=None, reel_meta_extra={})
        with patch.object(stitch, "supabase_client", SimpleNamespace(table=FakeDB(segments).table)), \
                patch.object(settings, "TRANSITIONS_ENABLED", crossfades), patch.object(settings, "TRANSITION_SECONDS", 0.3), \
                patch.object(settings, "REFRAME_MODE", "fit"):
            assert_(await stitch.stitch_and_caption(pipe, "v1", source, out), "stitch failed")
        check(out, f"{name} stitched")
        capped = os.path.join(tmp, f"{name}_captioned.mp4")
        with patch.object(caption, "_fetch_video_data", lambda vid: (segments, words)), \
                patch.object(settings, "TEXT_OVERLAYS_ENABLED", False), patch.object(settings, "REFRAME_MODE", "fit"):
            assert_(await caption.burn_captions(pipe, "v1", out, capped, source_path=source), "captions failed")
        check(capped, f"{name} captioned")
        final = os.path.join(tmp, f"{name}_music.mp4")
        mix.mix_reel(capped, final, music)
        check(final, f"{name} with music")
        return pipe

    src420 = os.path.join(tmp, "src420.mp4")
    src444 = os.path.join(tmp, "src444.mp4")
    make_source(src420, "yuv420p")
    make_source(src444, "yuv444p")
    assert_("yuv444p" in probe(src444)["raw"], "the awkward source really is 4:4:4")

    with test_case("hard cuts, 4:2:0 source: stitch, captions and music all give a standard MP4") as t:
        await chain(src420, False, "hard420")
    results.append(t["passed"])

    with test_case("crossfades, 4:2:0 source (the case that used to produce 4:4:4): standard MP4 at every stage") as t:
        pipe = await chain(src420, True, "fade420")
        assert_(pipe.transition_info and pipe.transition_info["count"] >= 1, "a crossfade really happened")
    results.append(t["passed"])

    with test_case("crossfades and hard cuts from a 4:4:4 source are still delivered as 4:2:0") as t:
        await chain(src444, True, "fade444")
        await chain(src444, False, "hard444")
    results.append(t["passed"])

    with test_case("the draft the verifier watches is also a standard MP4") as t:
        from worker.edit import draft
        out = os.path.join(tmp, "draft.mp4")

        async def runner(cmd):
            run(cmd)

        cuts = [{"start_time": 1.0, "end_time": 9.0, "join": "topic_change"}, {"start_time": 21.0, "end_time": 29.0, "join": None}]
        await draft.render_draft(src444, cuts, out, runner=runner, fade_s=0.3)
        check(out, "draft")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
