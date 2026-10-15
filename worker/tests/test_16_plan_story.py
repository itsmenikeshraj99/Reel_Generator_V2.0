"""Test 16: Story planner (worker/worker/gemini/plan_story.py).

Offline: the Gemini call is a fake function. No network, no cost.
"""
import json
import sys

from _helpers import test_case, assert_, section

SENTS = [{"id": f"S{i}", "start": round((i - 1) * 5.5, 3), "end": round((i - 1) * 5.5 + 5.0, 3),
          "text": f"sentence {i}", "first_word": i, "last_word": i} for i in range(1, 41)]


def beat(a, b, label="Point", join="topic_change", **kw):
    return dict({"from_id": a, "to_id": b, "label": label, "join": join, "why": "because"}, **kw)


GOOD_PLAN = {"shape": "montage", "reason": "One clear argument.", "mood": "calm",
             "beats": [beat("S2", "S2", "Hook"), beat("S4", "S5"), beat("S20", "S21"), beat("S30", "S30")]}
BAD_PLAN = {"shape": "montage", "reason": "Too thin.", "beats": [beat("S2", "S3"), beat("S5", "S6")]}  # 2 beats


class Fake:
    """Returns queued answers in order; records every prompt it was given."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt, schema):
        self.prompts.append(prompt)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


async def main() -> bool:
    section("Test 16: Story planner")
    from worker.config import settings
    from worker.gemini import plan_story as ps
    from worker.gemini.understand import Understanding, parse_understanding
    from worker.text import plan_rules as pr

    outline = parse_understanding({
        "language": "English", "summary": "A talk about habits.",
        "key_points": [{"title": "Why habits stick", "from_id": "S3", "to_id": "S6"}],
        "hook_candidates": [{"sentence_id": "S2", "type": "question", "reason": "Opens a loop."}],
        "visual_moments": []}, SENTS)
    assert_(isinstance(outline, Understanding), "fixture")

    results = []

    with test_case("prompt carries outline, sentence index, the rule numbers and the target") as t:
        p = ps.build_prompt(outline, SENTS, 38)
        assert_("Why habits stick (S3-S6)" in p and "S1 [00:00.0-00:05.0] sentence 1" in p, "outline or index missing")
        for needle in ("single_stretch", "montage", "reorder_reason", "needs_context", "about 38 seconds",
                       f"{pr.LABEL_MAX} characters", "sentence id", "ONE sentence"):
            assert_(needle in p, f"prompt lacks {needle!r}")
        assert_("NOT ACCEPTED" not in p, "feedback block must be absent without feedback")
        assert_("NOT ACCEPTED" in ps.build_prompt(outline, SENTS, 38, ["be shorter"]) and "be shorter" in
                ps.build_prompt(outline, SENTS, 38, ["be shorter"]), "feedback missing")
        assert_("plan-story-v3" in ps.__doc__, "prompt version")
        for needle in ("title: a short headline", "at most 50 characters", "devanagari for hindi"):
            assert_(needle in p.lower(), f"title guidance missing: {needle!r}")
        low = p.lower()
        for needle in ("never start on a filler", "hard cuts", "conversation", "stands on its own"):
            assert_(needle in low, f"v2 guidance missing: {needle!r}")
        assert_("MUST be \"single_stretch\"" not in p, "the one-stretch block must only appear on request")
    results.append(t["passed"])

    with test_case("force_shape asks for exactly one stretch and rejects a montage answer") as t:
        single = {"shape": "single_stretch", "reason": "One clear idea.", "beats": [beat("S5", "S9", "Whole idea")]}
        p = ps.build_prompt(outline, SENTS, 38, None, "single_stretch")
        assert_("MUST be \"single_stretch\"" in p and "A montage was already tried" in p, "instruction missing")
        fake = Fake(GOOD_PLAN, single)  # first answer is a montage: not allowed this time
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake, force_shape="single_stretch")
        assert_(r.ok and r.plan["shape"] == "single_stretch" and r.attempts == 2, f"{r.problems}")
        assert_("the shape must be single_stretch" in fake.prompts[1], "rejection reason not fed back")
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=Fake(GOOD_PLAN, GOOD_PLAN), force_shape="single_stretch")
        assert_(not r.ok and len(r.problems) == 2, f"{r}")
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=Fake(single), force_shape="single_stretch")
        assert_(r.ok and r.attempts == 1, "a valid single stretch passes first time")
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=Fake(single))
        assert_(r.ok, "without force_shape either shape is fine")
    results.append(t["passed"])

    with test_case("schema fields match what the rules read") as t:
        sch = ps.PLAN_SCHEMA_DICT
        assert_(sch["properties"]["shape"]["enum"] == list(pr.SHAPES), "shape enum")
        assert_(sch["properties"]["beats"]["items"]["properties"]["join"]["enum"] == list(pr.JOINS), "join enum")
        assert_(set(sch["required"]) == {"shape", "reason", "beats"}, "required")
        assert_(sch["properties"]["title"] == {"type": "string"}, "the schema must offer a title")
    results.append(t["passed"])

    with test_case("a good first answer is accepted after one call") as t:
        fake = Fake(GOOD_PLAN)
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake)
        assert_(r.ok and r.attempts == 1 and len(fake.prompts) == 1, f"{r}")
        assert_(r.plan["beats"][0]["role"] == "hook" and r.plan["mood"] == "calm", f"{r.plan}")
    results.append(t["passed"])

    with test_case("a plan the rules reject is retried with the exact errors") as t:
        fake = Fake(BAD_PLAN, GOOD_PLAN)
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake)
        assert_(r.ok and r.attempts == 2, f"{r.problems}")
        assert_("NOT ACCEPTED" in fake.prompts[1] and "montage needs at least" in fake.prompts[1], "errors not fed back")
        assert_("NOT ACCEPTED" not in fake.prompts[0], "first prompt must not have feedback")
    results.append(t["passed"])

    with test_case("invalid JSON or a failed call is retried too") as t:
        fake = Fake("this is not json {", GOOD_PLAN)
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake)
        assert_(r.ok and r.attempts == 2 and "not valid JSON" in fake.prompts[1], f"{r.problems}")
        fake = Fake(RuntimeError("503"), GOOD_PLAN)
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake)
        assert_(r.ok and r.attempts == 2, f"{r.problems}")
    results.append(t["passed"])

    with test_case("fenced JSON answers are understood") as t:
        r = await ps.plan_story(outline, SENTS, target_seconds=38,
                                call=Fake("```json\n" + json.dumps(GOOD_PLAN) + "\n```"))
        assert_(r.ok, f"{r.problems}")
    results.append(t["passed"])

    with test_case("two bad answers end in a clean failure with the reasons") as t:
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=Fake(BAD_PLAN, "nope"))
        assert_(not r.ok and r.plan is None and r.attempts == 2 and len(r.problems) >= 2, f"{r}")
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=Fake(RuntimeError("a"), RuntimeError("b")))
        assert_(not r.ok and r.attempts == 2, f"{r}")
    results.append(t["passed"])

    with test_case("a plan with one bad beat is repaired, not rejected") as t:
        plan = dict(GOOD_PLAN, beats=GOOD_PLAN["beats"] + [beat("S99", "S99")])
        fake = Fake(plan)
        r = await ps.plan_story(outline, SENTS, target_seconds=38, call=fake)
        assert_(r.ok and r.attempts == 1 and len(r.plan["beats"]) == 4, f"{r.problems}")
        assert_(any("unknown sentence id" in x for x in r.rule_result.repairs), "repair not reported")
    results.append(t["passed"])

    with test_case("verifier feedback is passed into the first prompt of a revision") as t:
        fake = Fake(GOOD_PLAN)
        await ps.plan_story(outline, SENTS, feedback=["The cut after beat 2 is abrupt"], target_seconds=38, call=fake)
        assert_("The cut after beat 2 is abrupt" in fake.prompts[0], "feedback missing")
        fake = Fake(BAD_PLAN, GOOD_PLAN)
        await ps.plan_story(outline, SENTS, feedback=["keep the hook"], target_seconds=38, call=fake)
        assert_("keep the hook" in fake.prompts[1] and "montage needs at least" in fake.prompts[1], "both kinds expected")
    results.append(t["passed"])

    with test_case("target length comes from settings by default and is range-checked") as t:
        assert_(settings.REEL_TARGET_SECONDS == 38, f"{settings.REEL_TARGET_SECONDS}")
        fake = Fake(GOOD_PLAN)
        await ps.plan_story(outline, SENTS, call=fake)
        assert_("about 38 seconds" in fake.prompts[0], "default target not used")
        from worker.config import WorkerSettings

        for bad in (5, 99):
            try:
                WorkerSettings(SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g", REEL_TARGET_SECONDS=bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
