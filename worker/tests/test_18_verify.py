"""Test 18: Verifier (worker/worker/gemini/verify.py).

Offline: Gemini and the file upload are replaced by fakes. No network, no cost.
"""
import random
import sys
from types import SimpleNamespace
from unittest.mock import patch

from _helpers import test_case, assert_, section

SENTS = [{"id": f"S{i}", "start": (i - 1) * 5.5, "end": (i - 1) * 5.5 + 5.0, "text": f"sentence {i}",
          "first_word": i, "last_word": i} for i in range(1, 21)]

PLAN = {"shape": "montage", "reason": "One clear argument.", "beats": [
    {"from_id": "S1", "to_id": "S1", "label": "Hook", "role": "hook"},
    {"from_id": "S5", "to_id": "S6", "label": "Point", "role": "point"},
    {"from_id": "S20", "to_id": "S20", "label": "", "role": "close"}]}

PASS = {"main_point_stated": "Habits need cues.", "hook_ok": True, "abrupt_cuts": [],
        "meaning_preserved": True, "verdict": "pass", "fixes": []}


async def main() -> bool:
    section("Test 18: Verifier")
    from worker.gemini import verify as v
    from worker.stages import transcribe_plan

    results = []

    with test_case("a clean pass stays a pass") as t:
        r = v.parse_verdict(PASS, 3, "montage")
        assert_(r.verdict == "pass" and r.notes == [] and r.fixes == [], f"{r}")
        assert_(r.to_dict()["main_point_stated"] == "Habits need cues.", "to_dict")
    results.append(t["passed"])

    with test_case("meaning not preserved is a hard fail for a montage, not for a single stretch") as t:
        data = dict(PASS, meaning_preserved=False)
        assert_(v.parse_verdict(data, 3, "montage").verdict == "fail", "montage")
        r = v.parse_verdict(dict(data, verdict="revise", fixes=["x"]), 1, "single_stretch")
        assert_(r.verdict == "revise", f"{r.verdict}")
    results.append(t["passed"])

    with test_case("a pass with a weak hook, two confusing beats or an unclear point is downgraded to revise") as t:
        for change in ({"hook_ok": False}, {"abrupt_cuts": [2, 3]}, {"main_point_stated": "unclear"}, {"main_point_stated": ""}):
            r = v.parse_verdict(dict(PASS, **change), 3, "montage")
            assert_(r.verdict == "revise" and r.fixes and r.notes, f"{change}: {r}")
    results.append(t["passed"])

    with test_case("one beat flagged as confusing still passes, and is noted") as t:
        r = v.parse_verdict(dict(PASS, abrupt_cuts=[2]), 3, "montage")
        assert_(r.verdict == "pass" and r.abrupt_cuts == [2], f"{r}")
        assert_(any("flagged as confusing" in n for n in r.notes), f"{r.notes}")
    results.append(t["passed"])

    with test_case("suggestions the planner cannot act on (voiceover, transitions, audio...) are dropped") as t:
        data = dict(PASS, verdict="revise", fixes=[
            "Add a brief transition text or voiceover to link the beats.", "Increase the pause length between beats.",
            "Ensure audio levels are consistent.", "Smooth the transition between beat 2 and 3.",
            "Start beat 2 at the sentence that introduces the study."], hook_ok=False)
        r = v.parse_verdict(data, 3, "montage")
        assert_(r.fixes == ["Start beat 2 at the sentence that introduces the study."], f"{r.fixes}")
    results.append(t["passed"])

    with test_case("a revise with nothing the planner can change becomes a pass; with a real fix it stays revise") as t:
        only_noise = dict(PASS, verdict="revise", abrupt_cuts=[2, 3], fixes=["Add a voiceover.", "Smooth the transitions."])
        r = v.parse_verdict(only_noise, 3, "montage")
        assert_(r.verdict == "revise" and r.fixes, f"two confusing beats must still be revised: {r}")
        r = v.parse_verdict(dict(only_noise, abrupt_cuts=[2]), 3, "montage")
        assert_(r.verdict == "pass" and any("upgraded" in n for n in r.notes), f"{r}")
        r = v.parse_verdict(dict(PASS, verdict="revise", fixes=["Use S7 instead of S5."]), 3, "montage")
        assert_(r.verdict == "revise" and r.fixes == ["Use S7 instead of S5."], f"{r}")
        r = v.parse_verdict(dict(only_noise, abrupt_cuts=[], hook_ok=False), 3, "montage")
        assert_(r.verdict == "revise", "a weak hook is never upgraded")
        r = v.parse_verdict(dict(only_noise, abrupt_cuts=[], meaning_preserved=False), 3, "montage")
        assert_(r.verdict == "fail", "lost meaning is still a hard fail")
    results.append(t["passed"])

    with test_case("revise always comes with usable fixes") as t:
        r = v.parse_verdict(dict(PASS, verdict="revise", hook_ok=False, abrupt_cuts=[2, 3]), 3, "montage")
        assert_(any("self-contained" in f.lower() for f in r.fixes) and any("2, 3" in f for f in r.fixes), f"{r.fixes}")
        r = v.parse_verdict(dict(PASS, verdict="revise", fixes=["Use S7 instead"]), 3, "montage")
        assert_(r.fixes == ["Use S7 instead"], f"{r.fixes}")
    results.append(t["passed"])

    with test_case("beat numbers are range-checked, de-duplicated and ignored for a single stretch") as t:
        r = v.parse_verdict(dict(PASS, verdict="revise", abrupt_cuts=[3, 0, 9, 2, 2, "x", True, -1]), 3, "montage")
        assert_(r.abrupt_cuts == [2, 3], f"{r.abrupt_cuts}")
        r = v.parse_verdict(dict(PASS, abrupt_cuts=[1]), 1, "single_stretch")
        assert_(r.abrupt_cuts == [] and r.verdict == "pass", f"{r}")
    results.append(t["passed"])

    with test_case("unknown verdicts become revise; string booleans are understood; fixes capped") as t:
        r = v.parse_verdict(dict(PASS, verdict="maybe", hook_ok="true", fixes=[f"f{i}" for i in range(9)]), 3, "montage")
        assert_(r.verdict == "revise" and r.hook_ok is True and len(r.fixes) == v.MAX_FIXES, f"{r}")
    results.append(t["passed"])

    with test_case("garbage answers raise VerifyError or are handled, never crash") as t:
        for bad in (None, [], "pass", 5):
            try:
                v.parse_verdict(bad, 3, "montage")
                raise AssertionError(f"accepted {bad!r}")
            except v.VerifyError:
                pass
        r = v.parse_verdict({}, 3, "montage")
        assert_(r.verdict == "revise" and r.fixes, f"{r}")
    results.append(t["passed"])

    with test_case("context shows the reason and one original sentence before and after each beat") as t:
        ctx = v.build_context(PLAN, SENTS)
        assert_("Plan reason: One clear argument." in ctx, "reason")
        assert_('just before in the original: "sentence 4"' in ctx and 'just after in the original: "sentence 7"' in ctx, ctx)
        assert_("Beat 1 (hook: Hook): S1 to S1" in ctx and "just before" not in ctx.split("Beat 2")[0], "first beat has no 'before'")
        assert_(ctx.rstrip().endswith('"sentence 19"') or "just after" not in ctx.split("Beat 3")[1], "last sentence has no 'after'")
    results.append(t["passed"])

    with test_case("schema and prompt cover every field the parser reads") as t:
        s = v.VERIFY_SCHEMA_DICT
        assert_(set(s["required"]) == set(s["properties"]), "required")
        assert_(s["properties"]["verdict"]["enum"] == list(v.VERDICTS), "verdict enum")
        assert_("verify-v3" in v.__doc__ and "abrupt_cuts" in v.VERIFY_PROMPT, "prompt")
        low = v.VERIFY_PROMPT.lower()
        assert_("hard cuts" in low and "do not judge" in low and "planner cannot do those" in low, "v2 guidance missing")
        flat = " ".join(low.split())  # ignore line breaks
        assert_("same short crossfade as the final reel" in flat, "the draft's crossfades must be explained")
    results.append(t["passed"])

    class FakeFiles:
        def __init__(self):
            self.deleted = []

        def delete(self, name):
            self.deleted.append(name)

    with test_case("verify_draft uploads, sends the context, parses, and deletes the upload") as t:
        from worker.gemini.client import gemini_client

        seen, files = {}, FakeFiles()
        up = SimpleNamespace(uri="files/abc", name="files/abc")

        async def fake_wait(f):
            return f

        async def fake_call(**kw):
            seen.update(kw)
            return v.VerifyRaw(**PASS)

        with patch.object(gemini_client, "upload_video", lambda p: up), \
                patch.object(gemini_client, "wait_for_processing", fake_wait), \
                patch.object(gemini_client, "client", SimpleNamespace(files=files)), \
                patch.object(transcribe_plan, "_gemini_call_with_fallback", fake_call):
            r = await v.verify_draft("draft.mp4", PLAN, SENTS)
        assert_(r.verdict == "pass", f"{r}")
        assert_(seen["video_file_uri"].endswith("files/abc") and "Beat 2" in seen["context_text"], f"{seen.keys()}")
        assert_(files.deleted == ["files/abc"], f"{files.deleted}")
    results.append(t["passed"])

    with test_case("a failing call becomes VerifyError and the upload is still deleted") as t:
        from worker.gemini.client import gemini_client

        files = FakeFiles()
        up = SimpleNamespace(uri="files/zzz", name="files/zzz")

        async def fake_wait(f):
            return f

        async def boom(**kw):
            raise RuntimeError("model unavailable")

        with patch.object(gemini_client, "upload_video", lambda p: up), \
                patch.object(gemini_client, "wait_for_processing", fake_wait), \
                patch.object(gemini_client, "client", SimpleNamespace(files=files)), \
                patch.object(transcribe_plan, "_gemini_call_with_fallback", boom):
            try:
                await v.verify_draft("draft.mp4", PLAN, SENTS)
                raise AssertionError("expected VerifyError")
            except v.VerifyError as exc:
                assert_("model unavailable" in str(exc), str(exc))
        assert_(files.deleted == ["files/zzz"], f"{files.deleted}")
    results.append(t["passed"])

    with test_case("random answers: a pass is only ever returned when every condition holds") as t:
        rng = random.Random(11)
        for _ in range(1000):
            data = {
                "main_point_stated": rng.choice(["A point.", "", "unclear", None, 3]),
                "hook_ok": rng.choice([True, False, "true", "no", None]),
                "abrupt_cuts": [rng.choice([0, 1, 2, 3, 9, "x"]) for _ in range(rng.randint(0, 3))],
                "meaning_preserved": rng.choice([True, False, "false", None]),
                "verdict": rng.choice(["pass", "revise", "fail", "PASS", "meh", None]),
                "fixes": [rng.choice(["fix", "", 5]) for _ in range(rng.randint(0, 6))],
            }
            shape = rng.choice(["montage", "single_stretch"])
            r = v.parse_verdict(data, rng.randint(1, 5), shape)
            assert_(r.verdict in v.VERDICTS, "verdict")
            if r.verdict == "pass":
                assert_(r.hook_ok and len(r.abrupt_cuts) <= v.MAX_TOLERATED_CUTS and r.main_point_stated
                        and r.main_point_stated.lower() != "unclear", f"{r}")
                assert_(shape == "single_stretch" or r.meaning_preserved, f"montage pass with lost meaning: {r}")
            if r.verdict == "revise":
                assert_(r.fixes and len(r.fixes) <= v.MAX_FIXES + 3, f"{r.fixes}")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
