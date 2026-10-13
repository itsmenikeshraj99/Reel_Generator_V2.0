"""Test 14: Cut snapper (worker/worker/text/snap.py).

Offline. The audio checks use ffmpeg to generate a tone with known
silences; everything else is pure Python.
"""
import copy
import os
import random
import subprocess
import sys
import tempfile

from _helpers import test_case, assert_, section


def dense_words(n, dur=0.3, gap=0.1):
    out, t = [], 0.0
    for _ in range(n):
        out.append({"text": "w", "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur + gap
    return out


def sentences_of(words_per_sentence, n_words):
    out = []
    for i in range(0, n_words, words_per_sentence):
        k = i // words_per_sentence + 1
        out.append({"id": f"S{k}", "first_word": i, "last_word": min(i + words_per_sentence, n_words) - 1})
    return out


def plan_of(*ranges, roles=None):
    beats = []
    for n, (a, b) in enumerate(ranges):
        beat = {"from_id": a, "to_id": b, "label": "x", "join": "topic_change"}
        if roles and roles.get(n):
            beat["role"] = roles[n]
        beats.append(beat)
    return {"shape": "montage", "beats": beats}


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


async def main() -> bool:
    section("Test 14: Cut snapper")
    from worker.text import snap
    from worker.text.snap import detect_silences, parse_silencedetect, snap_plan

    results = []

    with test_case("silencedetect output is parsed (noise lines, negative start, open-ended silence)") as t:
        log = "\n".join([
            "[silencedetect @ 0x1] silence_start: -0.00133",
            "[silencedetect @ 0x1] silence_end: 0.5 | silence_duration: 0.50133",
            "size=N/A time=00:00:03 bitrate=N/A speed=500x",
            "[silencedetect @ 0x1] silence_start: 2.25",
            "[silencedetect @ 0x1] silence_end: 3.1 | silence_duration: 0.85",
            "[silencedetect @ 0x1] silence_start: 9.0",
        ])
        assert_(parse_silencedetect(log) == [(0.0, 0.5), (2.25, 3.1)], f"{parse_silencedetect(log)}")
        assert_(parse_silencedetect(log, media_duration=10.0)[-1] == (9.0, 10.0), "open silence")
        assert_(parse_silencedetect("") == [], "empty")
    results.append(t["passed"])

    # 6 sentences of 4 dense words (0.4 s per word): sentence k spans 1.5 s
    words = dense_words(24)
    sents = sentences_of(4, 24)

    with test_case("padding is held back by the neighbouring words") as t:
        r = snap_plan(plan_of(("S3", "S3")), sents, words, [])
        b = r.beats[0]
        assert_(r.ok and approx(b["cut_start"], 3.2 - 0.1) and approx(b["cut_end"], 4.7 + 0.1), f"{b}")
    results.append(t["passed"])

    with test_case("with room to spare, lead-in is 0.15s and tail 0.25s") as t:
        spaced = [{"text": "w", "start": 10 + i * 2.0, "end": 10 + i * 2.0 + 0.3} for i in range(8)]
        sp_sents = sentences_of(2, 8)
        r = snap_plan(plan_of(("S2", "S2")), sp_sents, spaced, [])
        b = r.beats[0]  # words 2-3: 14.0 .. 16.3
        assert_(approx(b["cut_start"], 14.0 - 0.15) and approx(b["cut_end"], 16.3 + 0.25), f"{b}")
    results.append(t["passed"])

    with test_case("a cut snaps into a nearby quiet spot") as t:
        spaced = [{"text": "w", "start": 10 + i * 2.0, "end": 10 + i * 2.0 + 0.3} for i in range(8)]
        sp_sents = sentences_of(2, 8)
        r = snap_plan(plan_of(("S2", "S2")), sp_sents, spaced, [(16.65, 17.5), (13.3, 13.7)])
        b = r.beats[0]  # padded cuts are 16.55 and 13.85; both move into the silences
        assert_(b["snapped_end"] and approx(b["cut_end"], 16.65), f"{b}")
        assert_(b["snapped_start"] and approx(b["cut_start"], 13.7), f"{b}")
    results.append(t["passed"])

    with test_case("quiet spots further than 0.4s away are ignored") as t:
        spaced = [{"text": "w", "start": 10 + i * 2.0, "end": 10 + i * 2.0 + 0.3} for i in range(8)]
        r = snap_plan(plan_of(("S2", "S2")), sentences_of(2, 8), spaced, [(17.0, 17.8), (12.0, 12.5)])
        b = r.beats[0]
        assert_(not b["snapped_end"] and not b["snapped_start"], f"{b}")
        assert_(approx(b["cut_end"], 16.55) and approx(b["cut_start"], 13.85), f"{b}")
    results.append(t["passed"])

    with test_case("a cut never moves into a neighbouring word") as t:
        spaced = [{"text": "w", "start": 10 + i * 1.0, "end": 10 + i * 1.0 + 0.3} for i in range(8)]
        # beat = words 2-3 (12.0-13.3); next word starts at 14.0; silence reaches beyond it
        r = snap_plan(plan_of(("S2", "S2")), sentences_of(2, 8), spaced, [(13.9, 14.5), (11.2, 11.4)])
        b = r.beats[0]
        assert_(b["cut_end"] <= 14.0 + 1e-9 and b["cut_start"] >= 11.3 - 1e-9, f"{b}")
    results.append(t["passed"])

    with test_case("neighbouring beats split the shared gap at the midpoint") as t:
        r = snap_plan(plan_of(("S1", "S1"), ("S2", "S2"), ("S3", "S3")), sents, words, [])
        a, b, c = r.beats
        assert_(a["cut_end"] <= b["cut_start"] + 1e-9 and b["cut_end"] <= c["cut_start"] + 1e-9, f"{a} {b} {c}")
        assert_(approx(a["cut_end"], 1.55) and approx(b["cut_start"], 1.55), f"{a['cut_end']} {b['cut_start']}")
    results.append(t["passed"])

    with test_case("a total over 60s drops the last point, keeping hook, close and 3 beats") as t:
        long_words = dense_words(2000)
        long_sents = sentences_of(40, 2000)  # each sentence 16 s
        plan = plan_of(("S1", "S1"), ("S5", "S5"), ("S10", "S10"), ("S15", "S15"), ("S20", "S20"),
                       roles={4: "close"})  # 5 x ~16 s = 80 s
        r = snap_plan(plan, long_sents, long_words, [])
        assert_(r.ok and r.total_seconds <= 60 and len(r.beats) == 3, f"{r.errors} {r.total_seconds} {len(r.beats)}")
        assert_(r.beats[0]["from_id"] == "S1" and r.beats[-1]["role"] == "close", "hook or close lost")
        assert_(len(r.dropped) == 2, f"{r.dropped}")
    results.append(t["passed"])

    with test_case("a plan that cannot fit 60s is reported, not silently cut") as t:
        long_words = dense_words(2000)
        long_sents = sentences_of(40, 2000)
        r = snap_plan(plan_of(("S1", "S2"), ("S5", "S6"), ("S10", "S11")), long_sents, long_words, [])  # 3 x ~32 s
        assert_(r.ok is False and r.errors and "60" in r.errors[0], f"{r.errors}")
    results.append(t["passed"])

    with test_case("unknown ids and broken word indexes give an error, not a crash") as t:
        r = snap_plan(plan_of(("S99", "S99")), sents, words, [])
        assert_(not r.ok and r.errors, "unknown id")
        r = snap_plan(plan_of(("S1", "S1")), [{"id": "S1", "first_word": 0, "last_word": 999}], words, [])
        assert_(not r.ok and r.errors, "bad indexes")
    results.append(t["passed"])

    with test_case("the last beat's tail is limited by the media length") as t:
        r = snap_plan(plan_of(("S6", "S6")), sents, words, [], media_duration=9.65)  # last word ends 9.5
        assert_(approx(r.beats[0]["cut_end"], 9.65), f"{r.beats[0]}")
    results.append(t["passed"])

    with test_case("segments follow plan order and match the beats; input is untouched") as t:
        plan = plan_of(("S5", "S5"), ("S1", "S1"), ("S3", "S3"))
        before = copy.deepcopy(plan)
        r = snap_plan(plan, sents, words, [])
        assert_([s["start_time"] for s in r.segments] == [b["cut_start"] for b in r.beats], "segments")
        assert_(r.beats[0]["from_id"] == "S5" and r.beats[1]["from_id"] == "S1", "order")
        assert_(plan == before, "plan modified")
        assert_(approx(r.total_seconds, sum(s["end_time"] - s["start_time"] for s in r.segments), 1e-2), "total")
    results.append(t["passed"])

    with test_case("snapped segments are readable by the real caption builder and stitch checks") as t:
        from worker.stages.caption import _build_ass

        r = snap_plan(plan_of(("S1", "S1"), ("S3", "S3")), sents, words, [])
        assert_(all(set(("start_time", "end_time", "title", "reason")) <= set(s) for s in r.segments), f"{r.segments}")
        ass = _build_ass(r.segments, words)  # the function caption.py runs on the saved plan
        assert_("Dialogue:" in ass, "no caption lines were built from the snapped segments")
        total = sum(float(s["end_time"]) - float(s["start_time"]) for s in r.segments)  # stitch.py's own sum
        assert_(approx(total, r.total_seconds, 1e-2), f"{total} vs {r.total_seconds}")
    results.append(t["passed"])

    with test_case("random timelines: cuts always enclose the words and never enter another word") as t:
        rng = random.Random(7)
        for trial in range(400):
            n = rng.randint(12, 60)
            ws, tt = [], rng.random()
            for _ in range(n):
                tt += rng.choice([0.0, 0.02, 0.1, 0.3, 1.0])
                d = rng.choice([0.1, 0.3, 0.6])
                ws.append({"text": "w", "start": round(tt, 3), "end": round(tt + d, 3)})
                tt += d
            ss = sentences_of(rng.randint(1, 5), n)
            picks = sorted(rng.sample(range(len(ss)), min(len(ss), rng.randint(1, 4))))
            plan = plan_of(*[(ss[i]["id"], ss[i]["id"]) for i in picks])
            sil = []
            for _ in range(rng.randint(0, 12)):
                s0 = rng.uniform(0, tt)
                sil.append((s0, s0 + rng.choice([0.2, 0.5, 1.5])))
            r = snap_plan(plan, ss, ws, sil, media_duration=tt + rng.choice([0.0, 0.1, 2.0]))
            if not r.ok:
                continue
            for beat in r.beats:
                f = next(s for s in ss if s["id"] == beat["from_id"])["first_word"]
                l = next(s for s in ss if s["id"] == beat["to_id"])["last_word"]
                rs, re_ = ws[f]["start"], ws[l]["end"]
                cs, ce = beat["cut_start"], beat["cut_end"]
                assert_(cs <= rs + 1e-6 and ce >= re_ - 1e-6, f"trial {trial}: words cut off")
                for k, w in enumerate(ws):
                    if f <= k <= l:
                        continue
                    inside = min(ce, w["end"]) - max(cs, w["start"])
                    assert_(inside <= 1e-6, f"trial {trial}: cut enters word {k} by {inside:.3f}")
    results.append(t["passed"])

    with test_case("real ffmpeg audio: silences are found and a cut snaps to the tone's edge") as t:
        tmp = tempfile.mkdtemp(prefix="test14_")
        wav = os.path.join(tmp, "tone.wav")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=7",
            "-af", "volume=enable='between(t,2,3)+between(t,5,5.5)':volume=0", wav], check=True)
        found = await detect_silences(wav, media_duration=7.0)
        assert_(len(found) == 2, f"{found}")
        assert_(abs(found[0][0] - 2.0) < 0.06 and abs(found[0][1] - 3.0) < 0.06, f"{found[0]}")
        assert_(abs(found[1][0] - 5.0) < 0.06 and abs(found[1][1] - 5.5) < 0.06, f"{found[1]}")
        w = [{"text": "w", "start": s, "end": e} for s, e in
             [(0.2, 0.5), (0.6, 0.9), (1.0, 1.3), (1.4, 1.7), (3.05, 3.4), (3.5, 3.8), (3.9, 4.2), (4.3, 4.6)]]
        ss = [{"id": "S1", "first_word": 0, "last_word": 3}, {"id": "S2", "first_word": 4, "last_word": 7}]
        r = snap_plan(plan_of(("S1", "S1")), ss, w, found)
        b = r.beats[0]  # tail would end at 1.95 inside the tone; silence starts at ~2.0
        assert_(b["snapped_end"] and abs(b["cut_end"] - found[0][0]) < 1e-3, f"{b} {found}")
        assert_(abs(b["cut_end"] - 2.0) < 0.06, f"{b}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
