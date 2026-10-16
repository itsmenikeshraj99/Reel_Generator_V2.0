"""Turn a validated plan into exact cut times that never land inside a word.

For every beat:
1. Start/end come from the first/last word of its first/last sentence.
2. A little padding is added (lead-in before, tail after), but never
   past the neighbouring word.
3. Each cut then moves to the nearest quiet spot (from ffmpeg
   `silencedetect`) within SNAP_MAX_S, staying inside the gap between
   words.
Adjacent beats that would overlap in the gap between them are split at
the midpoint. Finally the total length is rechecked.
"""
import asyncio
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

LEAD_IN_S = 0.15
TAIL_S = 0.25
SNAP_MAX_S = 0.4
NOISE_DB = -35
MIN_SILENCE_S = 0.2
TOTAL_MAX_S = 60.0
MIN_BEATS = 3

Silence = Tuple[float, float]

_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


@dataclass
class SnapResult:
    ok: bool
    beats: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[Dict[str, Any]] = field(default_factory=list)  # {start_time, end_time, title, reason}
    total_seconds: float = 0.0
    dropped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def parse_silencedetect(stderr: str, media_duration: Optional[float] = None) -> List[Silence]:
    """Parse ffmpeg `silencedetect` log lines into (start, end) intervals."""
    out: List[Silence] = []
    start: Optional[float] = None
    for line in stderr.splitlines():
        m = _START_RE.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _END_RE.search(line)
        if m and start is not None:
            end = float(m.group(1))
            if end > start:
                out.append((start, end))
            start = None
    if start is not None:  # silence runs to the end of the file
        end = media_duration if media_duration is not None else start
        if end > start:
            out.append((start, end))
    return out


async def detect_silences(
    media_path: str,
    media_duration: Optional[float] = None,
    noise_db: int = NOISE_DB,
    min_silence_s: float = MIN_SILENCE_S,
    timeout: int = 600,
) -> List[Silence]:
    """Run ffmpeg silencedetect over the audio of `media_path`."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", media_path, "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"silencedetect failed: {proc.stderr[-300:]}")
    return parse_silencedetect(proc.stderr, media_duration)


def _word(w: Any) -> Tuple[float, float]:
    if isinstance(w, dict):
        return float(w["start"]), float(w["end"])
    return float(w.start), float(w.end)


def _nearest_quiet_point(cut: float, lo: float, hi: float, silences: Sequence[Silence]) -> Optional[float]:
    """Closest point to `cut` inside a silence, limited to [lo, hi] and SNAP_MAX_S."""
    best: Optional[Tuple[float, float]] = None
    for s, e in silences:
        cs, ce = max(s, lo), min(e, hi)
        if cs > ce:
            continue
        p = min(max(cut, cs), ce)
        d = abs(p - cut)
        if d <= SNAP_MAX_S and (best is None or d < best[0]):
            best = (d, p)
    return best[1] if best else None


def snap_plan(
    plan: Dict[str, Any],
    sentences: Sequence[Any],
    words: Sequence[Any],
    silences: Sequence[Silence],
    media_duration: Optional[float] = None,
) -> SnapResult:
    """Compute exact cuts for a plan that already passed `validate_plan`."""
    sents = {(s if isinstance(s, dict) else s.to_dict())["id"]:
             (s if isinstance(s, dict) else s.to_dict()) for s in sentences}
    spans = [_word(w) for w in words]
    silences = sorted(silences)
    result = SnapResult(ok=False)

    cuts: List[Dict[str, Any]] = []
    for beat in plan.get("beats", []):
        a, z = sents.get(beat.get("from_id")), sents.get(beat.get("to_id"))
        if a is None or z is None:
            result.errors.append(f"unknown sentence id in beat {beat.get('from_id')}-{beat.get('to_id')}")
            return result
        first, last = a["first_word"], z["last_word"]
        if not (0 <= first <= last < len(spans)):
            result.errors.append("sentence word indexes do not match the word list")
            return result
        raw_start, raw_end = spans[first][0], spans[last][1]

        prev_end = spans[first - 1][1] if first > 0 else 0.0
        lo_s = min(prev_end, raw_start)
        start0 = max(raw_start - LEAD_IN_S, lo_s)
        snapped_start = _nearest_quiet_point(start0, lo_s, raw_start, silences)

        if last + 1 < len(spans):
            next_start = spans[last + 1][0]
        elif media_duration is not None:
            next_start = media_duration
        else:
            next_start = raw_end + TAIL_S
        hi_e = max(next_start, raw_end)
        end0 = min(raw_end + TAIL_S, hi_e)
        snapped_end = _nearest_quiet_point(end0, raw_end, hi_e, silences)

        cuts.append({
            "beat": beat,
            "start": snapped_start if snapped_start is not None else start0,
            "end": snapped_end if snapped_end is not None else end0,
            "snapped_start": snapped_start is not None,
            "snapped_end": snapped_end is not None,
        })

    # Beats that sit right next to each other in the source share the gap
    # between them: split it at the midpoint so no audio plays twice.
    by_source = sorted(cuts, key=lambda c: c["start"])
    for left, right in zip(by_source, by_source[1:]):
        if left["end"] > right["start"]:
            mid = (left["end"] + right["start"]) / 2
            left["end"] = right["start"] = mid

    def total() -> float:
        return sum(c["end"] - c["start"] for c in cuts)

    while total() > TOTAL_MAX_S and len(cuts) > MIN_BEATS:
        for pos in range(len(cuts) - 1, 0, -1):
            if cuts[pos]["beat"].get("role") != "close":
                gone = cuts.pop(pos)["beat"]
                result.dropped.append(f"{gone.get('from_id')}-{gone.get('to_id')}")
                break
        else:
            break

    if total() > TOTAL_MAX_S:
        result.errors.append(f"total length {total():.1f}s is over {TOTAL_MAX_S:.0f}s after snapping")
        return result

    for c in cuts:
        result.beats.append(dict(
            c["beat"],
            cut_start=round(c["start"], 3), cut_end=round(c["end"], 3),
            snapped_start=c["snapped_start"], snapped_end=c["snapped_end"],
        ))
        # Same keys the render stage reads (stitch.py, caption.py).
        result.segments.append({
            "start_time": round(c["start"], 3),
            "end_time": round(c["end"], 3),
            "title": c["beat"].get("label") or f"{c['beat'].get('from_id')}-{c['beat'].get('to_id')}",
            "reason": c["beat"].get("why") or "",
            "join": c["beat"].get("join"),  # how this beat joins the NEXT one (used for crossfades)
        })
    result.total_seconds = round(total(), 3)
    result.ok = True
    return result
