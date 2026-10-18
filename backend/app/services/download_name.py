"""File names for downloaded reels.

The stored file is called after the video id (`reels/<uuid>.mp4`), which is what a browser would save. The
name a person sees should instead come from the reel's title: `Why-sleep-matters.mp4`.

Names are plain ASCII so every phone, chat app and file system accepts them. A title that has no usable
ASCII letters (Hindi, for example) falls back to the uploaded file's name: `my-podcast_reel.mp4`.
"""
import os
import re
import unicodedata
from typing import Iterable, Optional

MAX_STEM = 60
PLACEHOLDER_TITLES = ("ai generated reel",)


def _slug(text: Optional[str]) -> str:
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    if len(ascii_text) > MAX_STEM:
        cut = ascii_text[:MAX_STEM]
        ascii_text = cut[: cut.rfind("-")] if "-" in cut and ascii_text[MAX_STEM] != "-" else cut
    return ascii_text.strip("-")


def download_filename(title: Optional[str], original_filename: Optional[str], taken: Iterable[str] = (),
                      fallback: str = "reel") -> str:
    """The `.mp4` name for one reel. `taken` holds names already used in the same gallery (case-insensitive)."""
    stem = ""
    if title and title.strip().lower() not in PLACEHOLDER_TITLES:
        stem = _slug(title)
    if not stem and original_filename:
        base = _slug(os.path.splitext(os.path.basename(original_filename))[0])
        stem = f"{base}_reel" if base else ""
    stem = stem or fallback
    used = {t.lower() for t in taken}
    name, n = f"{stem}.mp4", 2
    while name.lower() in used:
        name, n = f"{stem}_{n}.mp4", n + 1
    return name
