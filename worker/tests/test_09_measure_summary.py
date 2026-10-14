"""Test 09: measurement tool summary logic.

Offline check of the pure functions in worker/tools/measure_run.py that
turn raw stage marks and Gemini call records into the printed summary.
"""
import os
import sys

from _helpers import test_case, assert_, section

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)


def _call(model, ok=True, p=0, o=0, th=0, secs=1.0):
    return {
        "model": model, "seconds": secs, "ok": ok,
        "prompt_tokens": p, "output_tokens": o, "thought_tokens": th,
        "total_tokens": p + o + th,
    }


async def main() -> bool:
    section("Test 09: Measurement tool summary")
    from measure_run import summarize, format_summary

    results = []

    with test_case("stage durations run mark-to-mark, last stage to end") as t:
        s = summarize([("A", 100.0), ("B", 110.0), ("C", 135.0)], 140.0, [])
        got = {x["stage"]: x["seconds"] for x in s["stages"]}
        assert_(got == {"A": 10.0, "B": 25.0, "C": 5.0}, f"got {got}")
        assert_(s["stage_seconds_total"] == 40.0, f"total {s['stage_seconds_total']}")
    results.append(t["passed"])

    with test_case("tokens add up and failed attempts are counted apart") as t:
        calls = [
            _call("m1", ok=False, secs=2.0),
            _call("m2", p=1000, o=200, th=50, secs=3.0),
            _call("m2", p=500, o=100, secs=1.5),
        ]
        g = summarize([("A", 0.0)], 1.0, calls, upload_seconds=4.0)["gemini"]
        assert_(g["ok_calls"] == 2 and g["failed_calls"] == 1, f"calls {g}")
        assert_(g["prompt_tokens"] == 1500 and g["output_tokens"] == 300, f"tokens {g}")
        assert_(g["thought_tokens"] == 50 and g["total_tokens"] == 1850, f"tokens {g}")
        assert_(g["call_seconds"] == 6.5 and g["file_upload_seconds"] == 4.0, f"secs {g}")
        assert_(g["by_model"]["m2"]["calls"] == 2, f"by_model {g['by_model']}")
        assert_("m1" not in g["by_model"], "failed model should not appear in usage")
    results.append(t["passed"])

    with test_case("empty input does not crash and formats") as t:
        s = summarize([], 0.0, [])
        assert_(s["stages"] == [] and s["gemini"]["ok_calls"] == 0, f"{s}")
        text = format_summary(s, "READY at READY")
        assert_("Outcome: READY at READY" in text and "TOTAL" in text, text)
    results.append(t["passed"])

    with test_case("missing usage fields are treated as zero") as t:
        s = summarize([("A", 0.0)], 1.0, [{"model": "m", "seconds": 1, "ok": True}])
        assert_(s["gemini"]["total_tokens"] == 0, f"{s['gemini']}")
    results.append(t["passed"])

    with test_case("attempt records are printed readably, including fixes and failures") as t:
        from measure_run import format_attempts

        text = format_attempts([
            {"attempt": 1, "plan": {"shape": "montage", "total": 31.0, "reason": "R", "beats": ["hook S2-S2 5s Hook"]},
             "verdict": {"verdict": "revise", "hook_ok": False, "meaning_preserved": True, "abrupt_cuts": [2],
                         "main_point_stated": "unclear", "fixes": ["Open with a stronger hook."], "notes": ["n1"]}},
            {"attempt": 2, "feedback_given": ["Open with a stronger hook."], "planner_problems": ["bad"]},
            {"attempt": "fallback", "verifier_unavailable": True},
        ])
        for needle in ("Attempt 1: verdict revise", "hook S2-S2 5s Hook", "fix: Open with a stronger hook.",
                       "abrupt_cuts=[2]", "told the planner: Open with a stronger hook.", "planner_problems: ['bad']",
                       "Attempt fallback", "verifier unavailable", "note: n1"):
            assert_(needle in text, f"missing {needle!r} in:\n{text}")
        assert_(format_attempts([]) == "", "empty")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
