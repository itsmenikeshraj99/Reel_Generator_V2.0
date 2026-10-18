"""The music library: a folder of tracks, one subfolder per mood, plus a `sfx` folder for sound effects.

Nothing is registered anywhere: the folder is scanned every time music is needed, so a file dropped
in is used on the next reel. An empty folder simply means "no music" - it is never an error.

    assets/music/calm | upbeat | serious | inspiring   background tracks (instrumental)
    assets/music/sfx                                   short transition sounds (whooshes)

Each track may have a `<name>.txt` next to it recording where it came from.
"""
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from worker.config import settings

MOODS = ("calm", "upbeat", "serious", "inspiring")
SFX_FOLDER = "sfx"
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
DEFAULT_MOOD = "calm"

# Words the planner (or a person) might use for each folder.
_MOOD_WORDS: Dict[str, tuple] = {
    "calm": ("calm", "relax", "peace", "gentle", "soft", "ambient", "thoughtful", "reflective", "chill",
             "mellow", "soothing", "neutral", "informative", "educational", "explain"),
    "upbeat": ("upbeat", "energetic", "energy", "happy", "fun", "playful", "positive", "cheerful", "lively",
               "bright", "excited", "exciting", "vibrant", "casual", "light", "humor", "funny"),
    "serious": ("serious", "dramatic", "tense", "dark", "somber", "grave", "weighty", "intense", "urgent",
                "sad", "emotional", "warning", "news", "suspense"),
    "inspiring": ("inspir", "motivat", "uplift", "hope", "empower", "triumph", "achievement", "success",
                  "cinematic", "epic", "determin", "confident"),
}
# When a mood's folder is empty, the nearest moods to try, in order.
_NEIGHBOURS: Dict[str, tuple] = {
    "calm": ("serious", "inspiring", "upbeat"),
    "upbeat": ("inspiring", "calm", "serious"),
    "serious": ("calm", "inspiring", "upbeat"),
    "inspiring": ("upbeat", "calm", "serious"),
}
NO_MUSIC_WORDS = ("none", "no music", "no_music", "silence", "silent")


def music_root() -> str:
    if settings.MUSIC_DIR:
        return os.path.abspath(settings.MUSIC_DIR)
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "music"))


@dataclass(frozen=True)
class Track:
    path: str
    mood: str
    name: str
    duration_s: Optional[float] = None
    credit: str = ""      # contents of the sidecar .txt, if any


def wants_no_music(mood: Optional[str]) -> bool:
    """True when the planner explicitly said this reel is better without music."""
    m = (mood or "").strip().lower()
    return bool(m) and any(m == w or m.startswith(w) for w in NO_MUSIC_WORDS)


def normalise_mood(mood: Optional[str]) -> str:
    """Map the planner's free-text mood to one of the four folders (default: calm)."""
    text = (mood or "").strip().lower()
    if not text:
        return DEFAULT_MOOD
    best, best_score = DEFAULT_MOOD, 0
    for folder, words in _MOOD_WORDS.items():
        score = 0
        for w in words:
            if text == w:
                score += 3
            elif re.search(r"\b" + re.escape(w), text):
                score += 1
        if score > best_score:
            best, best_score = folder, score
    return best


def probe_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        v = float(out.stdout.split()[0])
        return v if v > 0 else None
    except Exception:  # noqa: BLE001 - an unreadable file is simply skipped
        return None


def _scan(folder: str, mood: str, probe: Callable[[str], Optional[float]]) -> List[Track]:
    tracks: List[Track] = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return tracks
    for fn in names:
        full = os.path.join(folder, fn)
        base, ext = os.path.splitext(fn)
        if ext.lower() not in AUDIO_EXTENSIONS or not os.path.isfile(full):
            continue
        dur = probe(full)
        if dur is None:       # unreadable or not really audio
            continue
        credit = ""
        side = os.path.join(folder, base + ".txt")
        if os.path.isfile(side):
            try:
                with open(side, "r", encoding="utf-8", errors="replace") as f:
                    credit = f.read(600).strip()
            except OSError:
                pass
        tracks.append(Track(path=full, mood=mood, name=base, duration_s=dur, credit=credit))
    return tracks


def list_tracks(mood: str, root: Optional[str] = None,
                probe: Callable[[str], Optional[float]] = probe_duration) -> List[Track]:
    return _scan(os.path.join(root or music_root(), mood), mood, probe)


def list_sfx(root: Optional[str] = None, probe: Callable[[str], Optional[float]] = probe_duration) -> List[Track]:
    return _scan(os.path.join(root or music_root(), SFX_FOLDER), SFX_FOLDER, probe)


def _stable_index(seed: str, n: int) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % n


def pick_track(video_id: str, mood: Optional[str], reel_seconds: float, root: Optional[str] = None,
               probe: Callable[[str], Optional[float]] = probe_duration) -> Optional[Track]:
    """Choose the track for a reel, or None when there is nothing suitable (= no music).

    1. The planner's mood is mapped to a folder; if that folder is empty the nearest mood is tried.
    2. Within the folder, tracks at least as long as the reel are preferred (no looping needed).
    3. The choice is fixed by the video id, so a retry of the same job uses the same track.
    """
    if wants_no_music(mood):
        return None
    primary = normalise_mood(mood)
    for folder in (primary,) + _NEIGHBOURS[primary]:
        tracks = list_tracks(folder, root, probe)
        if not tracks:
            continue
        long_enough = [t for t in tracks if (t.duration_s or 0) >= reel_seconds]
        pool = long_enough or tracks
        return pool[_stable_index(video_id, len(pool))]
    return None


def pick_sfx(video_id: str, root: Optional[str] = None,
             probe: Callable[[str], Optional[float]] = probe_duration) -> Optional[Track]:
    sfx = list_sfx(root, probe)
    return sfx[_stable_index("sfx:" + video_id, len(sfx))] if sfx else None
