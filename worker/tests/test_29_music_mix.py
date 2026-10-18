"""Test 29: Music mix (worker/edit/mix.py): level, ducking, fades, looping, transition sounds, loudness.

Real ffmpeg on generated audio: "speech" is a tone that talks for 0-4 s and 7-12 s with a quiet gap
4-7 s; the music is another tone. Levels are measured with ebur128. No network, no cost.
"""
import hashlib
import os
import subprocess
import sys
import tempfile

from _helpers import test_case, assert_, section

REEL_S = 12.0
GAP = (4.5, 2.0)      # quiet gap window (start, length) inside the 4-7 s pause
TALK = (8.0, 3.0)     # talking window


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def make_reel(path, speech_db=0.0, talk=True):
    """12 s 640x360 video with "speech" that is silent between 4 and 7 s."""
    gate = "if(between(t,4,7),0,1)" if talk else "0"
    af = f"volume='{gate}':eval=frame,volume={speech_db}dB"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s=640x360:r=25:d={REEL_S}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={REEL_S}", "-af", af,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path])


def make_tone(path, seconds, freq=220):
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ac", "2", path])


def video_md5(path):
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out


async def main() -> bool:
    section("Test 29: Music mix")
    from worker.edit import mix

    results = []
    tmp = tempfile.mkdtemp(prefix="test29_")
    reel = os.path.join(tmp, "reel.mp4")
    music = os.path.join(tmp, "music.wav")
    sfx = os.path.join(tmp, "whoosh.wav")
    make_reel(reel)
    make_tone(music, 40, 220)
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.8:c=pink:r=48000", "-ac", "2", sfx])

    lufs = lambda p, start, length: mix.measure_lufs(p, seconds=length, start=start)["lufs"]

    with test_case("pure helpers: transition sounds sit on the crossfades; the duck threshold follows the speech level") as t:
        times = mix.sfx_times([0.0, 9.7, 20.0, 30.0], [0.3, 0.0, 0.3, 0.0])
        assert_(len(times) == 2 and abs(times[0] - (9.7 + 0.15 - mix.SFX_MAX_SECONDS * 0.35)) < 1e-6, f"{times}")
        assert_(mix.sfx_times([0.0, 9.0], [0.0, 0.0]) == [], "no crossfade, no sound")
        assert_(len(mix.sfx_times(list(range(0, 40, 2)), [0.3] * 20)) == mix.MAX_SFX, "capped")
        assert_(mix.duck_threshold(-30.0) < mix.duck_threshold(-15.0), "quieter speech, lower threshold")
        assert_(0.001 <= mix.duck_threshold(-90.0) <= mix.duck_threshold(0.0) <= 0.5, "clamped")
    results.append(t["passed"])

    out = os.path.join(tmp, "out.mp4")
    with test_case("mix: loudness lands on target, picture is bit-identical, audio and picture stay in sync") as t:
        res = mix.mix_reel(reel, out, music, target_lufs=-14.0, below_db=20.0)
        m = res.metrics
        assert_(abs(m["final_lufs"] - (-14.0)) <= 1.0, f"final loudness {m['final_lufs']}")
        assert_(m["final_peak_db"] <= -1.0, f"true peak {m['final_peak_db']}")
        assert_(video_md5(reel) == video_md5(out), "the video stream must be copied, not re-encoded")
        assert_(m["av_drift_s"] < 0.08, f"drift {m['av_drift_s']}")
        assert_(abs(mix.probe_duration(out) - REEL_S) < 0.15, "same length")
    results.append(t["passed"])

    with test_case("the music is quiet: about 20 dB under the speech between sentences, even lower while speaking") as t:
        speech_only = os.path.join(tmp, "speech_only.mp4")
        make_reel(speech_only)
        talking = lufs(out, TALK[0], TALK[1])
        gap = lufs(out, GAP[0], GAP[1])
        diff = talking - gap
        assert_(14.0 <= diff <= 27.0, f"speech is {diff:.1f} dB above the music in the gap (want ~20)")
    results.append(t["passed"])

    with test_case("ducking: the music itself is at least 6 dB lower while someone is talking than in a pause") as t:
        stem = os.path.join(tmp, "stem.wav")
        speech_wav = os.path.join(tmp, "speech.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", reel, "-vn", "-ac", "2", "-ar", "48000", speech_wav])
        sp = mix.measure_lufs(speech_wav)["lufs"]
        mix.build_music_stem(music, REEL_S, -30.0, speech_wav, mix.duck_threshold(sp), stem)
        in_gap = lufs(stem, GAP[0], GAP[1])
        in_talk = lufs(stem, TALK[0], TALK[1])
        assert_(in_gap - in_talk >= 6.0, f"music: gap {in_gap:.1f} vs talking {in_talk:.1f} LUFS")
        # releases smoothly: right after the pause begins it has not snapped fully up within 50 ms
        early = lufs(stem, 4.0, 0.15)
        late = lufs(stem, 5.5, 0.5)
        assert_(late - early >= 3.0, f"music swells back during the pause ({early:.1f} -> {late:.1f})")
    results.append(t["passed"])

    with test_case("fades: the music starts and ends softly (no sudden start or stop)") as t:
        silent_speech = os.path.join(tmp, "silent.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", str(REEL_S),
             "-c:a", "pcm_s16le", silent_speech])
        stem2 = os.path.join(tmp, "stem2.wav")
        mix.build_music_stem(music, REEL_S, -20.0, silent_speech, 0.5, stem2)   # threshold never reached: no ducking
        start = lufs(stem2, 0.0, 0.4)
        middle = lufs(stem2, 5.0, 1.0)
        end = lufs(stem2, REEL_S - 0.4, 0.4)
        assert_(middle - start >= 6.0, f"fade in: {start:.1f} vs middle {middle:.1f}")
        assert_(middle - end >= 6.0, f"fade out: {end:.1f} vs middle {middle:.1f}")
    results.append(t["passed"])

    with test_case("a track shorter than the reel is looped (music still plays at the end)") as t:
        short = os.path.join(tmp, "short.wav")
        make_tone(short, 4, 330)
        out2 = os.path.join(tmp, "out_short.mp4")
        mix.mix_reel(reel, out2, short)
        # the pause 4-7 s has only music; a looped track keeps it alive after the first 4 s of music ended
        assert_(lufs(out2, 5.5, 1.0) > -60.0, "music present at 5.5-6.5 s")
        stem3 = os.path.join(tmp, "stem3.wav")
        mix.build_music_stem(short, REEL_S, -20.0, silent_speech, 0.5, stem3)
        assert_(lufs(stem3, 8.0, 1.5) > -60.0, "looped track still audible after 8 s")
    results.append(t["passed"])

    with test_case("transition sounds are audible at the crossfade and quiet compared with the speech") as t:
        with_sfx = os.path.join(tmp, "with_sfx.mp4")
        r = mix.mix_reel(reel, with_sfx, music, join_times=[5.0], sfx_path=sfx)
        assert_(r.metrics["sfx_count"] == 1, "one sound used")
        base = lufs(out, 5.05, 0.5)
        with_ = lufs(with_sfx, 5.05, 0.5)
        assert_(with_ - base >= 3.0, f"sound adds {with_ - base:.1f} dB in the pause")
        talking = lufs(with_sfx, TALK[0], TALK[1])
        assert_(talking - with_ >= 8.0, f"but stays {talking - with_:.1f} dB under the speech")
    results.append(t["passed"])

    with test_case("level follows the speech: quiet and loud recordings get the same relative music level") as t:
        quiet, loud = os.path.join(tmp, "q.mp4"), os.path.join(tmp, "l.mp4")
        make_reel(quiet, speech_db=-12.0)
        make_reel(loud, speech_db=+6.0)
        a = mix.mix_reel(quiet, os.path.join(tmp, "oq.mp4"), music).metrics
        b = mix.mix_reel(loud, os.path.join(tmp, "ol.mp4"), music).metrics
        assert_(abs(a["music_below_speech_db"] - b["music_below_speech_db"]) <= 3.0, f"{a['music_below_speech_db']} vs {b['music_below_speech_db']}")
        assert_(abs(a["final_lufs"] + 14) <= 1.0 and abs(b["final_lufs"] + 14) <= 1.0, "both on target")
    results.append(t["passed"])

    with test_case("trouble raises (the caller then ships the reel unchanged): no audio, silent speech, silent or broken track") as t:
        no_audio = os.path.join(tmp, "noaudio.mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", no_audio])
        quiet_reel = os.path.join(tmp, "mute_speech.mp4")
        make_reel(quiet_reel, talk=False)
        silent_track = os.path.join(tmp, "silent_track.wav")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "20", silent_track])
        bad_track = os.path.join(tmp, "bad.wav")
        open(bad_track, "wb").write(b"junk")
        for name, args in (("no audio", (no_audio, music)), ("silent speech", (quiet_reel, music)),
                           ("silent track", (reel, silent_track)), ("broken track", (reel, bad_track))):
            o = os.path.join(tmp, "x.mp4")
            try:
                mix.mix_reel(args[0], o, args[1])
            except Exception:
                continue
            raise AssertionError(f"{name} should raise")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
