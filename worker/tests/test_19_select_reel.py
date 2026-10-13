"""Test 19: Reel selection loop and fallback (worker/worker/edit/).

Offline: planner, draft renderer and verifier are fakes. No network, no cost.
"""
import os
import random
import sys
import tempfile
from types import SimpleNamespace

from _helpers import test_case, assert_, section


def make_words():
    """40 sentences x 4 words; each word 1.2 s long, 0.1 s apart -> each sentence ~5.2 s."""
    words, t = [], 0.0
    for i in range(160):
        words.append({"text": "w", "start": round(t, 3), "end": round(t + 1.2, 3)})
        t += 1.3
    return words


def make_sentences(words):
    out = []
    for k in range(40):
        a, b = k * 4, k * 4 + 3
        out.append({"id": f"S{k + 1}", "start": words[a]["start"], "end": words[b]["end"],
                    "text": f"sentence {k + 1}", "first_word": a, "last_word": b})
    return out


WORDS = make_words()
SENTS = make_sentences(WORDS)


def beat(a, b, label="Point", **kw):
    return dict({"from_id": a, "to_id": b, "label": label, "join": "topic_change", "why": "w"}, **kw)


MONTAGE = {"shape": "montage", "reason": "One argument.",
           "beats": [beat("S2", "S2", "Hook"), beat("S5", "S6"), beat("S12", "S13"), beat("S20", "S20")]}


def outline(**kw):
    from worker.gemini.understand import Understanding

    base = dict(language="English", summary="s",
                key_points=[{"title": "Big idea", "from_id": "S8", "to_id": "S9"}],
                hook_candidates=[{"sentence_id": "S2", "type": "question", "reason": "r"}], visual_moments=[])
    base.update(kw)
    return Understanding(**base)


def verdict(kind="pass", fixes=None):
    from worker.gemini.verify import VerifyResult

    return VerifyResult("A point.", True, [], True, kind, fixes or ([] if kind == "pass" else ["fix it"]))


class Rig:
    """Fake planner/draft/verifier that record what the loop asks of them."""

    def __init__(self, plans, verdicts):
        self.plans, self.verdicts = list(plans), list(verdicts)
        self.plan_feedback, self.drafts, self.verified = [], [], []

    async def plan_fn(self, understanding, sentences, feedback=None):
        from worker.text import plan_rules as pr

        self.plan_feedback.append(feedback)
        p = self.plans.pop(0)
        if p is None:
            return SimpleNamespace(ok=False, plan=None, problems=["bad"])
        res = pr.validate_plan(p, sentences)
        return SimpleNamespace(ok=res.ok, plan=res.plan, problems=res.errors)

    async def make_draft(self, segments):
        fd, path = tempfile.mkstemp(suffix=".mp4", prefix="seltest_")
        os.close(fd)
        self.drafts.append(path)
        return path

    async def verify(self, draft, plan):
        self.verified.append(plan["shape"])
        v = self.verdicts.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    async def run(self, understanding=None, **kw):
        from worker.edit.select_reel import select_reel

        args = dict(understanding=understanding or outline(), sentences=SENTS, words=WORDS, silences=[],
                    media_duration=WORDS[-1]["end"], plan_fn=self.plan_fn, make_draft=self.make_draft,
                    verify=self.verify, max_revisions=2, target_seconds=38.0)
        args.update(kw)
        return await select_reel(**args)


async def main() -> bool:
    section("Test 19: Reel selection loop and fallback")
    from worker.edit.fallback import best_single_stretch
    from worker.text import plan_rules as pr

    results = []

    with test_case("a plan that passes first time is used as is") as t:
        rig = Rig([MONTAGE], [verdict("pass")])
        s = await rig.run()
        assert_(s.ok and not s.used_fallback and not s.needs_review and s.revisions == 0, f"{s}")
        assert_(s.plan["shape"] == "montage" and len(s.segments) == 4 and s.verifier["verdict"] == "pass", f"{s}")
        assert_(all({"start_time", "end_time"} <= set(x) for x in s.segments), "segment keys")
        assert_(len(rig.plan_feedback) == 1 and rig.plan_feedback[0] is None, "first call has no feedback")
    results.append(t["passed"])

    with test_case("revise sends the verifier's fixes to the planner, then passes") as t:
        rig = Rig([MONTAGE, MONTAGE], [verdict("revise", ["Open with S2"]), verdict("pass")])
        s = await rig.run()
        assert_(s.ok and s.revisions == 1 and not s.used_fallback and not s.needs_review, f"{s}")
        assert_(rig.plan_feedback[1] == ["Open with S2"], f"{rig.plan_feedback}")
    results.append(t["passed"])

    with test_case("out of revisions falls back to the best single stretch (verified, passes)") as t:
        rig = Rig([MONTAGE] * 3, [verdict("revise")] * 3 + [verdict("pass")])
        s = await rig.run()
        assert_(s.ok and s.used_fallback and not s.needs_review and s.plan["shape"] == "single_stretch", f"{s}")
        assert_(len(rig.plan_feedback) == 3 and rig.verified == ["montage"] * 3 + ["single_stretch"], f"{rig.verified}")
    results.append(t["passed"])

    with test_case("a fail verdict goes straight to the fallback") as t:
        rig = Rig([MONTAGE, MONTAGE], [verdict("fail", ["x"]), verdict("pass")])
        s = await rig.run()
        assert_(s.used_fallback and len(rig.plan_feedback) == 1, f"{len(rig.plan_feedback)} planner calls")
    results.append(t["passed"])

    with test_case("if the fallback also fails verification it ships flagged needs_review") as t:
        rig = Rig([MONTAGE], [verdict("fail"), verdict("revise")])
        s = await rig.run()
        assert_(s.ok and s.used_fallback and s.needs_review, f"{s}")
        assert_(s.verifier["verdict"] == "revise", f"{s.verifier}")
    results.append(t["passed"])

    with test_case("max_revisions=0 means one attempt, then the fallback") as t:
        rig = Rig([MONTAGE], [verdict("revise"), verdict("pass")])
        s = await rig.run(max_revisions=0)
        assert_(s.used_fallback and len(rig.plan_feedback) == 1, f"{len(rig.plan_feedback)}")
    results.append(t["passed"])

    with test_case("a planner that gives nothing, or crashes, falls back without verifying a montage") as t:
        rig = Rig([None], [verdict("pass")])
        s = await rig.run()
        assert_(s.ok and s.used_fallback and rig.verified == ["single_stretch"], f"{rig.verified}")
        rig = Rig([], [verdict("pass")])

        async def crash(*a, **k):
            raise RuntimeError("planner exploded")

        s = await rig.run(plan_fn=crash)
        assert_(s.ok and s.used_fallback and any("crashed" in h for h in s.history), f"{s.history}")
    results.append(t["passed"])

    with test_case("a snapping failure is fed back and counts as a revision") as t:
        too_long = {"shape": "montage", "reason": "r", "beats": [beat("S1", "S3"), beat("S10", "S12"), beat("S20", "S22")]}
        rig = Rig([too_long, MONTAGE], [verdict("pass")])
        # make snapping fail for the first plan only: pad the plan so the rules pass but 60s is exceeded after snapping
        calls = {"n": 0}
        import worker.edit.select_reel as sr
        real = sr.snap_plan

        def flaky(plan, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return sr.SnapResult(ok=False, errors=["total length 61.0s is over 60s after snapping"])
            return real(plan, *a, **k)

        sr.snap_plan = flaky
        try:
            s = await rig.run()
        finally:
            sr.snap_plan = real
        assert_(s.ok and s.revisions == 1 and not s.used_fallback, f"{s}")
        assert_(rig.plan_feedback[1] == ["total length 61.0s is over 60s after snapping"], f"{rig.plan_feedback}")
    results.append(t["passed"])

    with test_case("when the verifier is unavailable the plan ships unverified and flagged") as t:
        rig = Rig([MONTAGE], [RuntimeError("503")])
        s = await rig.run()
        assert_(s.ok and s.needs_review and s.verifier is None and not s.used_fallback, f"{s}")
        assert_(any("verification unavailable" in h for h in s.history), f"{s.history}")
    results.append(t["passed"])

    with test_case("draft files are always deleted, even when verification fails") as t:
        rig = Rig([MONTAGE] * 3, [verdict("revise"), RuntimeError("x")])
        await rig.run()
        rig2 = Rig([MONTAGE] * 3, [verdict("revise")] * 3 + [verdict("pass")])
        await rig2.run()
        leftovers = [p for p in rig.drafts + rig2.drafts if os.path.exists(p)]
        assert_(rig.drafts and rig2.drafts and not leftovers, f"left behind: {leftovers}")
    results.append(t["passed"])

    with test_case("nothing usable at all returns ok=False instead of crashing") as t:
        short = [s for s in SENTS[:2]]  # ~11 s of video: no stretch of 20 s exists
        rig = Rig([None], [])
        s = await rig.run(sentences=short, words=WORDS[:8], media_duration=11.0)
        assert_(not s.ok and s.plan is None, f"{s}")
    results.append(t["passed"])

    with test_case("best_single_stretch: grows a short point to the length closest to the target") as t:
        p = best_single_stretch(outline(), SENTS, 38.0)  # S8-S9 is ~10 s; 7 sentences (36 s) is closer than 8 (41 s)
        beat0 = p["beats"][0]
        assert_(p["shape"] == "single_stretch" and beat0["from_id"] == "S8" and beat0["to_id"] == "S14", f"{beat0}")
        res = pr.validate_plan(p, SENTS)
        assert_(res.ok and abs(res.total_seconds - 38.0) < 3.0, f"{res.errors} {res.total_seconds}")
    results.append(t["passed"])

    with test_case("best_single_stretch: trims a long point towards the target") as t:
        big = best_single_stretch(outline(key_points=[{"title": "Huge", "from_id": "S1", "to_id": "S30"}]), SENTS, 38.0)
        res = pr.validate_plan(big, SENTS)
        assert_(res.ok and abs(res.total_seconds - 38.0) < 3.0, f"{res.errors} {res.total_seconds}")
        assert_(big["beats"][0]["from_id"] == "S1", "should keep the start")
    results.append(t["passed"])

    with test_case("best_single_stretch: earlier candidates win ties; the end of the video grows backwards") as t:
        two = outline(key_points=[{"title": "First", "from_id": "S3", "to_id": "S4"},
                                  {"title": "Second", "from_id": "S20", "to_id": "S21"}])
        assert_(best_single_stretch(two, SENTS, 38.0)["beats"][0]["label"] == "First", "tie should go to the first")
        tail = outline(key_points=[{"title": "Ending", "from_id": "S39", "to_id": "S40"}], hook_candidates=[])
        p = best_single_stretch(tail, SENTS, 38.0)
        assert_(p and p["beats"][0]["to_id"] == "S40" and p["beats"][0]["from_id"] != "S39", f"{p}")
        assert_(pr.validate_plan(p, SENTS).ok, "tail plan must pass the rules")
    results.append(t["passed"])

    with test_case("best_single_stretch: hook candidates are a second source; nothing usable gives None") as t:
        hook_only = outline(key_points=[], hook_candidates=[{"sentence_id": "S5", "type": "other", "reason": ""}])
        p = best_single_stretch(hook_only, SENTS, 38.0)
        assert_(p and p["beats"][0]["from_id"] == "S5", f"{p}")
        assert_(best_single_stretch(outline(key_points=[], hook_candidates=[]), SENTS) is None, "no candidates")
        assert_(best_single_stretch(outline(), SENTS[:2]) is None, "video too short")
        assert_(best_single_stretch(outline(), []) is None, "empty")
    results.append(t["passed"])

    with test_case("random verdict sequences: the loop always ends, with a valid result or ok=False") as t:
        rng = random.Random(3)
        for trial in range(300):
            plans = [rng.choice([MONTAGE, None, MONTAGE]) for _ in range(4)]
            verdicts = [rng.choice([verdict("pass"), verdict("revise"), verdict("fail"), RuntimeError("v")]) for _ in range(6)]
            rig = Rig(plans, verdicts)
            s = await rig.run(max_revisions=rng.randint(0, 3))
            if s.ok:
                assert_(s.segments and s.total_seconds <= 60.5, f"trial {trial}: {s}")
                assert_(s.needs_review or (s.verifier and s.verifier["verdict"] == "pass"), f"trial {trial}: unflagged non-pass: {s}")
            assert_(len(rig.plan_feedback) <= 4, f"trial {trial}: too many planner calls")
            assert_(not [p for p in rig.drafts if os.path.exists(p)], f"trial {trial}: draft left behind")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
