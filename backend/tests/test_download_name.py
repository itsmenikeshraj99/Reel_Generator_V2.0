"""Download file names (backend/app/services/download_name.py). Pure Python: no network.

Run from the backend folder:  .\\venv\\Scripts\\python.exe tests\\test_download_name.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services.download_name import download_filename as name  # noqa: E402

CASES = []


def case(label):
    def deco(fn):
        CASES.append((label, fn))
        return fn
    return deco


@case("a title becomes a tidy ASCII file name")
def _():
    assert name("Why Sleep Matters More Than You Think", "talk.mp4") == "Why-Sleep-Matters-More-Than-You-Think.mp4"
    assert name("It's 5 a.m.: do THIS!", "x.mp4") == "It-s-5-a-m-do-THIS.mp4"
    assert name("Café déjà vu", "x.mp4") == "Cafe-deja-vu.mp4"


@case("unsafe characters never reach the file name")
def _():
    n = name('../../etc/passwd "<>|?*:\\ x', "x.mp4")
    assert n == "etc-passwd-x.mp4", n
    assert all(c.isalnum() or c in "-_." for c in n), n


@case("long titles are cut at a word, at most 60 characters before .mp4")
def _():
    n = name("word " * 30, "x.mp4")
    assert n.endswith(".mp4") and len(n) <= 64 and not n.startswith("-") and "--" not in n, n
    assert name("a" * 200, "x.mp4") == "a" * 60 + ".mp4"


@case("Hindi, empty and placeholder titles fall back to the upload's name")
def _():
    assert name("भारत की कहानी", "My Podcast Ep 4.mov") == "My-Podcast-Ep-4_reel.mp4"
    assert name("", "My Podcast.mp4") == "My-Podcast_reel.mp4"
    assert name(None, "clip.mp4") == "clip_reel.mp4"
    assert name("AI Generated Reel", "Interview 2.mp4") == "Interview-2_reel.mp4"
    assert name("ai generated reel", "Interview 2.mp4") == "Interview-2_reel.mp4"


@case("nothing usable anywhere gives a plain 'reel.mp4'")
def _():
    assert name(None, None) == "reel.mp4"
    assert name("भारत", "भारत.mp4") == "reel.mp4"


@case("duplicate names in one gallery get _2, _3 (case-insensitive)")
def _():
    taken = []
    for _ in range(3):
        taken.append(name("Same Title", "x.mp4", taken))
    assert taken == ["Same-Title.mp4", "Same-Title_2.mp4", "Same-Title_3.mp4"], taken
    assert name("same title", "x.mp4", ["Same-Title.mp4"]) == "same-title_2.mp4"
    assert name("", "talk.mp4", ["talk_reel.mp4"]) == "talk_reel_2.mp4"


if __name__ == "__main__":
    failed = 0
    for label, fn in CASES:
        try:
            fn()
            print(f"  [PASS] {label}")
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {label}: {exc}")
    sys.exit(1 if failed else 0)
