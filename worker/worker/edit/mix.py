"""Mix quiet background music (and soft transition sounds) under a finished reel.

The picture is never re-encoded: the reel's video stream is copied and only the audio is rebuilt.

How it keeps the speaker clear
* The music is set relative to the speech, not to a fixed volume: the reel's speech loudness is
  measured, and the music is placed `below_db` (default 20 dB) under it, so it behaves the same for a
  quiet or a loud recording.
* While someone speaks the music is pushed down a further ~10 dB (a side-chain compressor listening to
  the speech, fast attack, slow release so it never pumps) and swells back between sentences.
* Slow fade in, longer fade out; a track shorter than the reel is looped.
* The whole result is then levelled to the target loudness with a measured two-pass `loudnorm`.

Everything here raises on trouble; the caller decides to ship the reel without music.
"""
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

FADE_IN_S = 1.2
FADE_OUT_S = 2.5
DUCK_RATIO = 6.0
DUCK_ATTACK_MS = 25
DUCK_RELEASE_MS = 600
DUCK_TRIGGER_BELOW_SPEECH_DB = 12.0   # music starts ducking when the level is within this of average speech
SFX_BELOW_SPEECH_DB = 16.0            # transition sounds sit this far under the speech
SFX_MAX_SECONDS = 0.9
MAX_SFX = 8
TRUE_PEAK_DB = -1.5
SILENT_LUFS = -60.0                   # at or below this a signal counts as silence (ebur128 floors at -70)
GAIN_RANGE_DB = (-70.0, 20.0)


@dataclass
class MixResult:
    metrics: Dict[str, object]


def _run(cmd: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {res.stderr[-400:]}")
    return res


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=30, check=True).stdout.split()
    return float(out[0])


def stream_duration(path: str, kind: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    return float(out[0])


def _av_gap(path: str) -> float:
    """Picture length minus sound length. Mixing must not change it (that would be drift)."""
    return stream_duration(path, "v:0") - stream_duration(path, "a:0")


def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return bool(out)


def measure_lufs(path: str, seconds: Optional[float] = None, start: float = 0.0) -> Dict[str, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's audio (optionally just a window)."""
    cmd = ["ffmpeg", "-nostats", "-hide_banner"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]
    text = _run(cmd).stderr
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text
    i = re.search(r"\bI:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)

    def num(m):
        return float("-inf") if (m is None or m.group(1) == "-inf") else float(m.group(1))

    return {"lufs": num(i), "peak_db": num(p)}


def _loudnorm_json(text: str) -> Dict[str, str]:
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("loudnorm printed no measurement")
    return json.loads(text[start:end + 1])


def sfx_times(beat_starts: Sequence[float], overlaps: Sequence[float]) -> List[float]:
    """Where to place a transition sound: centred on each crossfade (reel timeline, seconds)."""
    times = []
    for i, ov in enumerate(overlaps):
        if ov and ov > 0 and i + 1 < len(beat_starts):
            times.append(max(0.0, beat_starts[i + 1] + ov / 2.0 - SFX_MAX_SECONDS * 0.35))
    return times[:MAX_SFX]


def duck_threshold(speech_lufs: float) -> float:
    """Linear side-chain threshold: a little under the average speech level, so words trigger it
    and room noise between them does not."""
    db = speech_lufs - DUCK_TRIGGER_BELOW_SPEECH_DB
    return min(0.5, max(0.001, 10 ** (db / 20.0)))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def build_music_stem(track_path: str, reel_s: float, gain_db: float, speech_path: str, threshold: float,
                     out_wav: str) -> None:
    """Music, looped/trimmed to the reel, quiet, faded and ducked under the speech, as a 48k stereo wav."""
    fade_out = min(FADE_OUT_S, max(0.5, reel_s / 4.0))
    fade_in = min(FADE_IN_S, max(0.3, reel_s / 6.0))
    graph = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{reel_s:.3f},asetpts=PTS-STARTPTS,"
        f"volume={gain_db:.2f}dB,afade=t=in:st=0:d={fade_in:.2f},"
        f"afade=t=out:st={max(0.0, reel_s - fade_out):.3f}:d={fade_out:.2f}[mus];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo[sc];"
        f"[mus][sc]sidechaincompress=threshold={threshold:.5f}:ratio={DUCK_RATIO}:attack={DUCK_ATTACK_MS}:"
        f"release={DUCK_RELEASE_MS}:makeup=1[out]"
    )
    _run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", track_path, "-i", speech_path,
          "-filter_complex", graph, "-map", "[out]", "-t", f"{reel_s:.3f}", "-c:a", "pcm_f32le", out_wav])


def mix_reel(reel_path: str, out_path: str, track_path: str, *, join_times: Sequence[float] = (),
             sfx_path: Optional[str] = None, target_lufs: float = -14.0, below_db: float = 20.0) -> MixResult:
    """Write `out_path`: the reel with music (+ transition sounds), levelled to `target_lufs`."""
    if not has_audio(reel_path):
        raise RuntimeError("the reel has no audio to put music under")
    reel_s = probe_duration(reel_path)
    work = tempfile.mkdtemp(prefix="mix_")
    speech_wav = os.path.join(work, "speech.wav")
    stem_wav = os.path.join(work, "music.wav")
    mix_wav = os.path.join(work, "mix.wav")
    try:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", reel_path, "-vn", "-ac", "2", "-ar", "48000",
              "-c:a", "pcm_f32le", speech_wav])
        speech = measure_lufs(speech_wav)
        if speech["lufs"] <= SILENT_LUFS:
            raise RuntimeError("the reel's audio is silent")
        music = measure_lufs(track_path, seconds=reel_s)
        if music["lufs"] <= SILENT_LUFS:
            raise RuntimeError("the music track is silent")
        gain = _clamp(speech["lufs"] - below_db - music["lufs"], *GAIN_RANGE_DB)
        build_music_stem(track_path, reel_s, gain, speech_wav, duck_threshold(speech["lufs"]), stem_wav)

        # mix: speech + ducked music + (optional) transition sounds
        inputs = ["-i", speech_wav, "-i", stem_wav]
        parts = ["[0:a]anull[sp]", "[1:a]anull[mu]"]
        labels = ["[sp]", "[mu]"]
        used_sfx = 0
        if sfx_path and join_times:
            sfx_lufs = measure_lufs(sfx_path)["lufs"]
            if sfx_lufs > SILENT_LUFS:
                sfx_gain = _clamp(speech["lufs"] - SFX_BELOW_SPEECH_DB - sfx_lufs, *GAIN_RANGE_DB)
                times = list(join_times)[:MAX_SFX]
                inputs += ["-i", sfx_path]
                n = len(times)
                split = "[2:a]aresample=48000,aformat=channel_layouts=stereo," \
                        f"atrim=0:{SFX_MAX_SECONDS},asetpts=PTS-STARTPTS,volume={sfx_gain:.2f}dB," \
                        f"afade=t=out:st={SFX_MAX_SECONDS * 0.5:.2f}:d={SFX_MAX_SECONDS * 0.5:.2f}"
                if n == 1:
                    parts.append(split + "[s0]")
                else:
                    parts.append(split + f",asplit={n}" + "".join(f"[r{i}]" for i in range(n)))
                    for i in range(n):
                        parts.append(f"[r{i}]anull[s{i}]")
                for i, t in enumerate(times):
                    ms = int(round(t * 1000))
                    parts.append(f"[s{i}]adelay={ms}|{ms}[d{i}]")
                    labels.append(f"[d{i}]")
                used_sfx = n
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95[out]")
        _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts), "-map", "[out]",
              "-t", f"{reel_s:.3f}", "-c:a", "pcm_f32le", mix_wav])

        # measured two-pass loudness normalisation, then mux with the untouched video
        ln = f"loudnorm=I={target_lufs}:TP={TRUE_PEAK_DB}:LRA=11"
        probe = _run(["ffmpeg", "-nostats", "-hide_banner", "-i", mix_wav, "-af", ln + ":print_format=json",
                      "-f", "null", "-"]).stderr
        m = _loudnorm_json(probe)
        second = (f"{ln}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
                  f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", reel_path, "-i", mix_wav, "-map", "0:v:0", "-map", "1:a:0",
              "-af", second + f",aresample=48000,apad=whole_dur={stream_duration(reel_path, 'v:0'):.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
              "-movflags", "+faststart", "-t", f"{reel_s:.3f}", out_path])

        final = measure_lufs(out_path)
        stem = measure_lufs(stem_wav)
        return MixResult(metrics={
            "speech_lufs": round(speech["lufs"], 1),
            "music_gain_db": round(gain, 1),
            "music_lufs_in_mix": round(stem["lufs"], 1) if math.isfinite(stem["lufs"]) else None,
            "music_below_speech_db": round(speech["lufs"] - stem["lufs"], 1) if math.isfinite(stem["lufs"]) else None,
            "final_lufs": round(final["lufs"], 1),
            "target_lufs": target_lufs,
            "final_peak_db": round(final["peak_db"], 1),
            "sfx_count": used_sfx,
            "av_drift_s": round(abs(_av_gap(out_path) - _av_gap(reel_path)), 3),
        })
    finally:
        for p in (speech_wav, stem_wav, mix_wav):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass
