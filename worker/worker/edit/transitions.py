"""Smooth joins between montage beats.

A join marked `topic_change` or `point_change` by the planner gets a short crossfade (picture and
sound); `continuous` joins stay clean hard cuts, because crossfading continuous speech would blur it.
Everything is rendered in a single ffmpeg pass that also fits every clip onto the 9:16 canvas.

A crossfade overlaps two clips, so everything after it starts earlier on the reel's timeline. The
helpers here (`overlaps_for`, `beat_starts`) are the one place that arithmetic lives; captions and the
on-screen title/labels use them so their timing stays in step with the picture.
"""
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from worker.edit.canvas import fit_filter

FADE_JOINS = ("topic_change", "point_change")
CLIP_MARGIN_S = 0.2   # a clip must be at least 2 x fade + this long to be faded on both sides
FPS = 30


def segment_durations(segments: Sequence[Any]) -> List[float]:
    return [float(s["end_time"]) - float(s["start_time"]) for s in segments]


def overlaps_for(segments: Sequence[Any], fade_s: float) -> List[float]:
    """Seconds of overlap at the END of each segment: `fade_s` where that join fades, else 0.

    A join fades only if the planner marked it topic_change/point_change and both neighbouring clips
    are long enough. The last segment never overlaps anything.
    """
    n = len(segments)
    out = [0.0] * n
    if fade_s <= 0 or n < 2:
        return out
    durations = segment_durations(segments)
    need = 2 * fade_s + CLIP_MARGIN_S
    for i in range(n - 1):
        if segments[i].get("join") in FADE_JOINS and durations[i] >= need and durations[i + 1] >= need:
            out[i] = fade_s
    return out


def beat_starts(durations: Sequence[float], overlaps: Sequence[float]) -> List[float]:
    """Where each beat starts on the finished reel's timeline."""
    starts, t = [], 0.0
    for i, d in enumerate(durations):
        starts.append(t)
        t += d - (overlaps[i] if i < len(overlaps) else 0.0)
    return starts


def reel_length(durations: Sequence[float], overlaps: Sequence[float]) -> float:
    return sum(durations) - sum(overlaps[: max(0, len(durations) - 1)])


@dataclass
class Graph:
    filter_complex: str
    video_label: str
    audio_label: Optional[str]
    length: float


def build_graph(durations: Sequence[float], overlaps: Sequence[float], has_audio: bool, fps: int = FPS) -> Graph:
    """The ffmpeg filter graph that fits every input clip onto the canvas and joins them left to right.

    Input i is clip i. Fading joins use xfade + acrossfade; the others use concat. After a concat the
    video's timebase differs from the next clip's, which xfade rejects, so it is normalised again.
    """
    n = len(durations)
    if n == 0:
        raise ValueError("no clips to join")
    fit = fit_filter()
    parts: List[str] = []
    for i in range(n):
        parts.append(f"[{i}:v]{fit},fps={fps},format=yuv420p,settb=1/{fps},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                         f"asetpts=PTS-STARTPTS[a{i}]")
    cur_v, cur_a, cur_len = "v0", "a0", float(durations[0])
    for i in range(1, n):
        fade = overlaps[i - 1] if i - 1 < len(overlaps) else 0.0
        nv, na = f"vj{i}", f"aj{i}"
        if fade > 0:
            parts.append(f"[{cur_v}][v{i}]xfade=transition=fade:duration={fade:.3f}:offset={cur_len - fade:.3f}[{nv}]")
            if has_audio:
                parts.append(f"[{cur_a}][a{i}]acrossfade=d={fade:.3f}[{na}]")
            cur_len += durations[i] - fade
        else:
            parts.append(f"[{cur_v}][v{i}]concat=n=2:v=1:a=0[{nv}c];"
                         f"[{nv}c]fps={fps},settb=1/{fps},setpts=PTS-STARTPTS[{nv}]")
            if has_audio:
                parts.append(f"[{cur_a}][a{i}]concat=n=2:v=0:a=1[{na}]")
            cur_len += durations[i]
        cur_v, cur_a = nv, na
    return Graph(";".join(parts), cur_v, cur_a if has_audio else None, cur_len)


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return float(out)


def has_audio_stream(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return bool(out)


def render(clips: Sequence[str], overlaps: Sequence[float], output_path: str,
           run: Callable[[List[str]], None]) -> Graph:
    """Join the already-cut clips with the planned crossfades into `output_path` (one ffmpeg pass)."""
    durations = [probe_duration(c) for c in clips]
    if any(d <= 0 for d in durations):
        raise RuntimeError("a cut clip is empty")
    audio = all(has_audio_stream(c) for c in clips)
    graph = build_graph(durations, overlaps, audio)
    cmd = ["ffmpeg", "-y", "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-filter_complex", graph.filter_complex, "-map", f"[{graph.video_label}]"]
    if audio:
        cmd += ["-map", f"[{graph.audio_label}]"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20"]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if audio else ["-an"]
    cmd += ["-movflags", "+faststart", output_path]
    run(cmd)
    return graph
