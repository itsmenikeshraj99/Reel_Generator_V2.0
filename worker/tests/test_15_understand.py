"""Test 15: Understanding call (worker/worker/gemini/understand.py).

Offline: Gemini is replaced by a fake. No network, no cost.
"""
import random
import sys
from unittest.mock import patch

from _helpers import test_case, assert_, section

SENTS = [{"id": f"S{i}", "start": (i - 1) * 5.5, "end": (i - 1) * 5.5 + 5.0, "text": f"sentence {i}",
          "first_word": i, "last_word": i} for i in range(1, 31)]

GOOD = {
    "language": "English",
    "summary": "A talk about habits.",
    "key_points": [
        {"title": "Why habits stick", "from_id": "S3", "to_id": "S6"},
        {"title": "The cue", "from_id": "S10", "to_id": "S12"},
    ],
    "hook_candidates": [
        {"sentence_id": "S11", "type": "question", "reason": "Opens a loop."},
        {"sentence_id": "S4", "type": "bold_claim", "reason": "Strong claim."},
    ],
    "visual_moments": [{"near_id": "S20", "description": "Chart appears"}],
}


async def main() -> bool:
    section("Test 15: Understanding call")
    from worker.gemini import understand as u

    results = []

    with test_case("sentence index lines carry id, clock time and text") as t:
        text = u.format_sentence_index(SENTS[:2] + [{"id": "S3", "start": 3725.25, "end": 3730.0, "text": "late"}])
        lines = text.splitlines()
        assert_(lines[0] == "S1 [00:00.0-00:05.0] sentence 1", lines[0])
        assert_(lines[2] == "S3 [62:05.2-63:50.0] late" or lines[2].startswith("S3 [62:05"), lines[2])
    results.append(t["passed"])

    with test_case("a good answer is accepted as is") as t:
        r = u.parse_understanding(GOOD, SENTS)
        assert_(r.language == "English" and len(r.key_points) == 2 and len(r.hook_candidates) == 2, f"{r}")
        assert_(r.dropped == [] and r.visual_moments[0]["near_id"] == "S20", f"{r.dropped}")
        assert_(r.to_dict()["summary"] == "A talk about habits.", "to_dict")
        out = r.outline_text()
        assert_("Why habits stick (S3-S6)" in out and "S11 [question]" in out and "near S20" in out, out)
    results.append(t["passed"])

    with test_case("bad items are dropped one by one, the rest survive") as t:
        bad = dict(GOOD, key_points=GOOD["key_points"] + [
            {"title": "ghost", "from_id": "S99", "to_id": "S100"}, "nonsense", None,
            {"title": "Same again", "from_id": "S3", "to_id": "S6"}])
        bad["hook_candidates"] = GOOD["hook_candidates"] + [{"sentence_id": "S404", "type": "x", "reason": ""}, 5]
        bad["visual_moments"] = [{"near_id": "S404", "description": "?"}]
        r = u.parse_understanding(bad, SENTS)
        assert_(len(r.key_points) == 2 and len(r.hook_candidates) == 2 and r.visual_moments == [], f"{r}")
        assert_(len(r.dropped) >= 5, f"{r.dropped}")
    results.append(t["passed"])

    with test_case("reversed ranges are fixed; odd hook types become 'other'; titles filled in") as t:
        r = u.parse_understanding(dict(GOOD, key_points=[{"title": "  ", "from_id": "S8", "to_id": "S5"}],
                                       hook_candidates=[{"sentence_id": "S2", "type": "Weird", "reason": "r"}]), SENTS)
        assert_(r.key_points[0] == {"title": "S5-S8", "from_id": "S5", "to_id": "S8"}, f"{r.key_points}")
        assert_(r.hook_candidates[0]["type"] == "other", f"{r.hook_candidates}")
    results.append(t["passed"])

    with test_case("no usable key point is an error; garbage never crashes") as t:
        for bad in (None, [], "text", 5, {}, dict(GOOD, key_points=[]),
                    dict(GOOD, key_points=[{"title": "x", "from_id": "S99", "to_id": "S98"}])):
            try:
                u.parse_understanding(bad, SENTS)
                raise AssertionError(f"accepted {bad!r}")
            except u.UnderstandingError:
                pass
    results.append(t["passed"])

    with test_case("long lists are capped") as t:
        many = dict(GOOD, key_points=[{"title": f"k{i}", "from_id": f"S{i}", "to_id": f"S{i}"} for i in range(1, 30)],
                    hook_candidates=[{"sentence_id": f"S{i}", "type": "other", "reason": ""} for i in range(1, 30)],
                    visual_moments=[{"near_id": f"S{i}", "description": ""} for i in range(1, 30)])
        r = u.parse_understanding(many, SENTS)
        assert_(len(r.key_points) == u.MAX_KEY_POINTS and len(r.hook_candidates) == u.MAX_HOOKS
                and len(r.visual_moments) == u.MAX_VISUAL, f"{len(r.key_points)} {len(r.hook_candidates)}")
    results.append(t["passed"])

    with test_case("schema asks for every field the parser uses") as t:
        props = u.UNDERSTAND_SCHEMA_DICT["properties"]
        assert_(set(props) == set(u.UNDERSTAND_SCHEMA_DICT["required"]), "required mismatch")
        assert_(set(props["key_points"]["items"]["required"]) == {"title", "from_id", "to_id"}, "key point fields")
        assert_("sentence id" in u.UNDERSTAND_PROMPT.lower() and "never use timestamps" in u.UNDERSTAND_PROMPT.lower(),
                "prompt must demand sentence ids")
        assert_("understand-v1" in u.__doc__, "prompt version missing")
    results.append(t["passed"])

    with test_case("understand() sends the video and the sentence index, and returns the parsed outline") as t:
        from worker.stages import transcribe_plan

        seen = {}

        async def fake_call(**kw):
            seen.update(kw)
            return u.UnderstandingRaw(**GOOD)

        with patch.object(transcribe_plan, "_gemini_call_with_fallback", fake_call):
            r = await u.understand("https://files/abc", SENTS, video_id="v1")
        assert_(seen["video_file_uri"] == "https://files/abc" and seen["response_schema"] is u.UNDERSTAND_SCHEMA_DICT, f"{seen.keys()}")
        assert_("S1 [00:00.0-00:05.0] sentence 1" in seen["context_text"], "index not sent")
        assert_(len(r.key_points) == 2, f"{r}")
    results.append(t["passed"])

    with test_case("a failed Gemini call becomes UnderstandingError") as t:
        from worker.stages import transcribe_plan

        async def boom(**kw):
            raise RuntimeError("503 from model")

        with patch.object(transcribe_plan, "_gemini_call_with_fallback", boom):
            try:
                await u.understand("uri", SENTS)
                raise AssertionError("expected UnderstandingError")
            except u.UnderstandingError as exc:
                assert_("503" in str(exc), str(exc))
    results.append(t["passed"])

    with test_case("random answers: output only ever refers to real sentence ids") as t:
        rng = random.Random(5)
        ids = [s["id"] for s in SENTS]
        junk = ids + ["S99", "", None, 7, "s1"]
        valid = set(ids)
        for _ in range(500):
            data = {
                "language": rng.choice(["English", None, 3]),
                "key_points": [rng.choice([{"title": rng.choice(["t", None]), "from_id": rng.choice(junk), "to_id": rng.choice(junk)}, 1, None])
                               for _ in range(rng.randint(0, 6))],
                "hook_candidates": [{"sentence_id": rng.choice(junk), "type": rng.choice(["question", "zzz", None])}
                                    for _ in range(rng.randint(0, 6))],
                "visual_moments": [{"near_id": rng.choice(junk)} for _ in range(rng.randint(0, 6))],
            }
            try:
                r = u.parse_understanding(data, SENTS)
            except u.UnderstandingError:
                continue
            assert_(r.key_points, "accepted with no key points")
            for k in r.key_points:
                assert_(k["from_id"] in valid and k["to_id"] in valid and int(k["from_id"][1:]) <= int(k["to_id"][1:]), f"{k}")
            assert_(all(h["sentence_id"] in valid and h["type"] in u.HOOK_TYPES for h in r.hook_candidates), "hook")
            assert_(all(v["near_id"] in valid for v in r.visual_moments), "visual")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
