"""Test 28: Music library (worker/edit/music.py): scan, mood mapping, deterministic pick, empty folders.

Tracks are tones generated with ffmpeg in a temp folder (MUSIC_DIR-style root), never the real library.
No network, no cost.
"""
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

from _helpers import test_case, assert_, section


def make_tone(path, seconds, freq=330):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    path], check=True)


async def main() -> bool:
    section("Test 28: Music library")
    from worker.config import settings
    from worker.edit import music

    results = []
    root = tempfile.mkdtemp(prefix="test28_")
    for m in music.MOODS + (music.SFX_FOLDER,):
        os.makedirs(os.path.join(root, m))

    with test_case("mood words map to the four folders; unknown and empty go to calm") as t:
        n = music.normalise_mood
        assert_(n("calm") == "calm" and n("Upbeat") == "upbeat" and n("serious") == "serious" and n("inspiring") == "inspiring", "exact")
        assert_(n("energetic and fun") == "upbeat", n("energetic and fun"))
        assert_(n("dramatic") == "serious" and n("motivational") == "inspiring" and n("relaxing") == "calm", "synonyms")
        assert_(n("") == "calm" and n(None) == "calm" and n("banana") == "calm", "fallback is calm")
    results.append(t["passed"])

    with test_case("'none' means the planner wants no music, and then no track is picked even if tracks exist") as t:
        make_tone(os.path.join(root, "calm", "a.wav"), 30)
        assert_(music.wants_no_music("none") and music.wants_no_music("No music") and not music.wants_no_music("calm")
                and not music.wants_no_music(None), "wants_no_music")
        assert_(music.pick_track("v1", "none", 20, root=root) is None, "no track for none")
        assert_(music.pick_track("v1", "calm", 20, root=root) is not None, "a track for calm")
    results.append(t["passed"])

    with test_case("empty library is 'no music', not a failure") as t:
        empty = tempfile.mkdtemp(prefix="test28_empty_")
        assert_(music.pick_track("v1", "calm", 30, root=empty) is None, "missing folders")
        os.makedirs(os.path.join(empty, "calm"))
        open(os.path.join(empty, "calm", ".gitkeep"), "w").close()
        assert_(music.pick_track("v1", "calm", 30, root=empty) is None, "folder with only .gitkeep")
        assert_(music.pick_sfx("v1", root=empty) is None, "no sfx")
    results.append(t["passed"])

    with test_case("unreadable or non-audio files and unknown extensions are skipped") as t:
        with open(os.path.join(root, "upbeat", "broken.mp3"), "wb") as f:
            f.write(b"not audio at all")
        open(os.path.join(root, "upbeat", "notes.pdf"), "w").write("x")
        assert_(music.list_tracks("upbeat", root=root) == [], "broken file skipped")
        assert_(music.pick_track("v1", "upbeat", 30, root=root) is not None, "falls to the nearest mood with a track (calm)")
        assert_(music.pick_track("v1", "upbeat", 30, root=root).mood == "calm", "nearest mood used")
    results.append(t["passed"])

    with test_case("sidecar text is read; the pick is fixed by the video id and prefers tracks long enough") as t:
        make_tone(os.path.join(root, "inspiring", "short.wav"), 8, 440)
        make_tone(os.path.join(root, "inspiring", "long1.wav"), 40, 440)
        make_tone(os.path.join(root, "inspiring", "long2.wav"), 45, 550)
        with open(os.path.join(root, "inspiring", "long1.txt"), "w") as f:
            f.write("Source: Pixabay\nLicence: Pixabay Content Licence")
        names = {tr.name: tr for tr in music.list_tracks("inspiring", root=root)}
        assert_(set(names) == {"short", "long1", "long2"}, f"{set(names)}")
        assert_("Pixabay" in names["long1"].credit and names["long2"].credit == "", "sidecar")
        picks = {music.pick_track(f"video-{i}", "inspiring", 38, root=root).name for i in range(40)}
        assert_("short" not in picks, "a track shorter than the reel is not chosen while longer ones exist")
        assert_(picks == {"long1", "long2"}, f"both long tracks get used across videos: {picks}")
        a = music.pick_track("same-video", "inspiring", 38, root=root)
        b = music.pick_track("same-video", "inspiring", 38, root=root)
        assert_(a.path == b.path, "same video id, same track (a retry keeps its music)")
        only_short = music.pick_track("v9", "inspiring", 100, root=root)
        assert_(only_short is not None, "when every track is shorter than the reel one is still used (it will loop)")
    results.append(t["passed"])

    with test_case("sound effects come from the sfx folder; MUSIC_DIR setting moves the library") as t:
        make_tone(os.path.join(root, "sfx", "whoosh.wav"), 1, 900)
        assert_(music.pick_sfx("v1", root=root).name == "whoosh", "sfx picked")
        with patch.object(settings, "MUSIC_DIR", root):
            assert_(music.music_root() == os.path.abspath(root), "override")
            assert_(music.pick_track("v1", "calm", 20) is not None, "uses the override by default")
        with patch.object(settings, "MUSIC_DIR", ""):
            assert_(music.music_root().replace("\\", "/").endswith("worker/assets/music"), music.music_root())
    results.append(t["passed"])

    with test_case("settings: kill switch on by default; loudness and music-level ranges are checked") as t:
        from worker.config import WorkerSettings
        s = WorkerSettings(_env_file=None, SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g")
        assert_(s.MUSIC_ENABLED is True and s.LOUDNESS_TARGET_LUFS == -14.0 and s.MUSIC_BELOW_SPEECH_DB == 20.0, "defaults")
        for bad in ({"LOUDNESS_TARGET_LUFS": -3}, {"MUSIC_BELOW_SPEECH_DB": 2}):
            try:
                WorkerSettings(_env_file=None, SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g", **bad)
            except Exception:
                continue
            raise AssertionError(f"{bad} should be rejected")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
