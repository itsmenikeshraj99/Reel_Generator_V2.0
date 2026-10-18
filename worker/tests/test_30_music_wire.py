"""Test 30: Music in the pipeline (worker/stages/music.py + pipeline.py hook).

A generated reel and generated tracks, a fake database. Checks the rules that matter to users: music only
when the box was ticked, an untouched reel in every other case, and a recorded explanation either way.
No network, no cost.
"""
import hashlib
import inspect
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section
from test_29_music_mix import make_reel, make_tone


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class FakeDB:
    def __init__(self, add_music=True, mood="calm", segments=None, raise_on_videos=False):
        self.add_music, self.raise_on_videos = add_music, raise_on_videos
        self.plan = {"id": "p1", "plan": {"mood": mood}, "segments": segments or
                     [{"start_time": 0.0, "end_time": 6.0, "join": "topic_change"},
                      {"start_time": 20.0, "end_time": 26.0, "join": None}]}

    def table(self, name):
        db = self

        class Q:
            def select(self, *a): return self
            def eq(self, *a): return self
            def order(self, *a): return self
            def limit(self, *a): return self
            def single(self): return self

            def execute(self):
                if name == "videos":
                    if db.raise_on_videos:
                        raise RuntimeError('column "add_music" does not exist')
                    return SimpleNamespace(data={"add_music": db.add_music})
                return SimpleNamespace(data=[db.plan])

        return Q()


async def main() -> bool:
    section("Test 30: Music in the pipeline")
    from worker.config import settings
    from worker.edit import mix
    from worker.stages import music as stage
    from worker import pipeline as pl

    results = []
    tmp = tempfile.mkdtemp(prefix="test30_")
    lib = os.path.join(tmp, "lib")
    for m in ("calm", "upbeat", "serious", "inspiring", "sfx"):
        os.makedirs(os.path.join(lib, m))
    make_tone(os.path.join(lib, "calm", "pad.wav"), 40, 220)
    make_tone(os.path.join(lib, "sfx", "whoosh.wav"), 1, 900)
    base = os.path.join(tmp, "base.mp4")
    make_reel(base)

    def fresh():
        p = os.path.join(tmp, f"reel_{len(os.listdir(tmp))}.mp4")
        open(p, "wb").write(open(base, "rb").read())
        return p

    def pipe(overlaps=None):
        return SimpleNamespace(reel_meta_extra={}, transition_info={"overlaps": overlaps} if overlaps else None)

    async def run(db, reel, p, **flags):
        with patch.object(stage, "supabase_client", db), patch.object(settings, "MUSIC_DIR", lib), \
                patch.object(settings, "MUSIC_ENABLED", flags.get("enabled", True)):
            return await stage.add_music(p, "vid-1", reel)

    with test_case("box not ticked: the reel is byte-identical and nothing is recorded") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(add_music=False), reel, p) is False, "returns False")
        assert_(sha(reel) == before and "music" not in p.reel_meta_extra, "untouched, no meta")
    results.append(t["passed"])

    with test_case("old database (no add_music column) or any DB trouble means no music, not a failure") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(raise_on_videos=True), reel, p) is False and sha(reel) == before, "treated as unticked")
    results.append(t["passed"])

    with test_case("kill switch MUSIC_ENABLED=false wins over the box") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(), reel, p, enabled=False) is False and sha(reel) == before, "untouched")
    results.append(t["passed"])

    with test_case("box ticked: music is mixed in place, with the track, mood and quality checks recorded") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(mood="relaxing"), reel, p) is True, "applied")
        assert_(sha(reel) != before and abs(mix.probe_duration(reel) - 12.0) < 0.2, "file replaced, same length")
        info = p.reel_meta_extra["music"]
        assert_(info["applied"] and info["track"] == "pad" and info["track_mood"] == "calm", f"{info}")
        assert_(info["qa"]["loudness_ok"] and info["qa"]["drift_ok"], f"{info['qa']}")
        assert_(not os.path.exists(reel + ".music.mp4"), "temporary file removed")
    results.append(t["passed"])

    with test_case("crossfades get a transition sound at the right moment; no crossfade, no sound") as t:
        reel, p = fresh(), pipe(overlaps=[0.3, 0.0])
        assert_(await run(FakeDB(), reel, p) is True, "applied")
        assert_(p.reel_meta_extra["music"]["qa"]["sfx_count"] == 1 and p.reel_meta_extra["music"]["sfx"] == "whoosh", "one sound")
        reel2, p2 = fresh(), pipe()
        await run(FakeDB(), reel2, p2)
        assert_(p2.reel_meta_extra["music"]["qa"]["sfx_count"] == 0, "none without crossfades")
    results.append(t["passed"])

    with test_case("planner said 'none' or the library is empty: reel untouched and the reason is recorded") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(mood="none"), reel, p) is False and sha(reel) == before, "none")
        assert_("without music" in p.reel_meta_extra["music"]["reason"], f"{p.reel_meta_extra}")
        os.rename(os.path.join(lib, "calm", "pad.wav"), os.path.join(tmp, "pad.wav"))
        reel, p = fresh(), pipe()
        before = sha(reel)
        assert_(await run(FakeDB(), reel, p) is False and sha(reel) == before, "empty library")
        assert_("no music tracks" in p.reel_meta_extra["music"]["reason"], f"{p.reel_meta_extra}")
        os.rename(os.path.join(tmp, "pad.wav"), os.path.join(lib, "calm", "pad.wav"))
    results.append(t["passed"])

    with test_case("a failing mix leaves the reel exactly as it was") as t:
        reel, p = fresh(), pipe()
        before = sha(reel)

        def boom(*a, **k):
            raise RuntimeError("ffmpeg exploded")

        with patch.object(stage.mix, "mix_reel", boom):
            assert_(await run(FakeDB(), reel, p) is False, "returns False")
        assert_(sha(reel) == before, "untouched")
        assert_(p.reel_meta_extra["music"]["applied"] is False and "exploded" in p.reel_meta_extra["music"]["reason"], f"{p.reel_meta_extra}")
        assert_(not os.path.exists(reel + ".music.mp4"), "temp removed")
    results.append(t["passed"])

    with test_case("the render stage calls the music stage after captions and before the size check") as t:
        src = inspect.getsource(pl.Pipeline._stage_render)
        a, b, c = src.index("burn_captions("), src.index("add_music("), src.index("Adaptive Size Control")
        assert_(a < b < c, "order: captions, music, compression")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
