"""Test 13: Plan rules validator (worker/worker/text/plan_rules.py).

Pure Python, offline: no network, no AI, no cost.
"""
import copy
import random
import sys

from _helpers import test_case, assert_, section


def make_sentences(n=40, dur=5.0, gap=0.5):
    """S1..Sn, each `dur` seconds long, `gap` apart; plus a 2-second S41."""
    out, t = [], 0.0
    for i in range(1, n + 1):
        out.append({"id": f"S{i}", "start": round(t, 3), "end": round(t + dur, 3),
                    "text": f"sentence {i}", "first_word": i, "last_word": i})
        t += dur + gap
    out.append({"id": f"S{n + 1}", "start": round(t, 3), "end": round(t + 2.0, 3),
                "text": "tiny", "first_word": n + 1, "last_word": n + 1})
    return out


SENTS = make_sentences()


def beat(a, b, label="Point", join="topic_change", **kw):
    return dict({"from_id": a, "to_id": b, "label": label, "join": join, "why": "because"}, **kw)


def montage(*beats, **kw):
    return dict({"shape": "montage", "reason": "It makes one clear argument.", "beats": list(beats)}, **kw)


# 5 + 10.5 + 10.5 + 5 = 31 s, ascending source order
GOOD = montage(beat("S2", "S2", "Hook"), beat("S4", "S5"), beat("S20", "S21"), beat("S30", "S30"),
               title="Why Phones Drain Your Focus")


def check_invariants(res, sents):
    """Independent re-statement of the rules, applied to an accepted plan."""
    from worker.text import plan_rules as pr

    plan = res.plan
    ids = {s["id"]: s for s in sents}
    beats = plan["beats"]
    assert_(plan["shape"] in pr.SHAPES and plan["reason"].strip(), "shape/reason")
    assert_(isinstance(plan["title"], str) and len(plan["title"]) <= 50 and "{" not in plan["title"], "title")
    seen = set()
    total = 0.0
    for b in beats:
        a, z = ids[b["from_id"]], ids[b["to_id"]]
        assert_(a["start"] <= z["start"], "reversed beat")
        dur = z["end"] - a["start"]
        total += dur
        assert_(abs(dur - b["duration"]) < 1e-3, "duration field")
        assert_(len(b["label"]) <= 24, "label length")
        assert_(b["join"] in pr.JOINS, "join")
        covered = {s["id"] for s in sents if a["start"] <= s["start"] and s["end"] <= z["end"]}
        assert_(not (covered & seen), "overlap")
        seen |= covered
        if plan["shape"] == "montage":
            assert_(3.0 <= dur <= 20.0, f"beat length {dur}")
    if plan["shape"] == "montage":
        assert_(3 <= len(beats) <= 8, f"{len(beats)} beats")
        assert_(beats[0]["role"] == "hook", "first beat must be the hook")
        assert_(20.0 <= total <= 60.0, f"total {total}")
    else:
        assert_(len(beats) == 1 and 20.0 <= total <= 45.0, f"single {total}")
    starts = [ids[b["from_id"]]["start"] for b in beats]
    assert_(starts == sorted(starts) or str(plan.get("reorder_reason", "")).strip(), "order without reason")
    assert_(abs(res.total_seconds - total) < 1e-2, "total_seconds")


async def main() -> bool:
    section("Test 13: Plan rules validator")
    from worker.text import plan_rules as pr
    from worker.text.plan_rules import validate_plan

    results = []

    with test_case("a good montage passes with roles, times and no repairs") as t:
        before = copy.deepcopy(GOOD)
        r = validate_plan(GOOD, SENTS)
        assert_(r.ok and not r.errors and not r.repairs, f"{r.errors} {r.repairs}")
        assert_([b["role"] for b in r.plan["beats"]] == ["hook", "point", "point", "point"], "roles")
        assert_(r.plan["beats"][0]["start"] == SENTS[1]["start"], "start time")
        assert_(r.plan["beats"][-1]["join"] == "continuous", "last join")
        assert_(abs(r.total_seconds - 31.0) < 1e-6 and not r.warnings, f"{r.total_seconds} {r.warnings}")
        assert_(GOOD == before, "input was modified")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("the title is cleaned (emoji, braces, quotes, line breaks) and carried on the plan") as t:
        r = validate_plan(dict(GOOD, title='  "Phones \U0001F4F1 {make}\nyou \\ dumber!"  '), SENTS)
        assert_(r.ok and r.plan["title"] == "Phones make you dumber!", repr(r.plan["title"]))
        assert_(validate_plan(GOOD, SENTS).plan["title"] == "Why Phones Drain Your Focus", "kept as given")
    results.append(t["passed"])

    with test_case("Devanagari titles keep their vowel signs and joiners") as t:
        hindi = "\u092b\u094b\u0928 \u0906\u092a\u0915\u094b \u0915\u0948\u0938\u0947 \u0915\u092e\u091c\u093c\u094b\u0930 \u092c\u0928\u093e\u0924\u093e \u0939\u0948"
        assert_(pr.clean_title(hindi) == hindi, repr(pr.clean_title(hindi)))
        assert_(pr.clean_title("\u0915\u094d\u200d\u0937") == "\u0915\u094d\u200d\u0937", "ZWJ must survive")
    results.append(t["passed"])

    with test_case("long titles are cut at a word boundary within 50 characters; junk gives an empty title") as t:
        long = "Why a dead phone on your desk still lowers your focus and your IQ"
        cut = pr.clean_title(long)
        assert_(len(cut) <= 50 and long.startswith(cut) and cut.endswith("your"), repr(cut))  # "...your focus" would be 53
        assert_(pr.clean_title("x" * 80) == "x" * 50, "no spaces: hard cut")
        for junk in (None, 5, [], "", "   ", "\U0001F4F1\U0001F4F1", "{}\\"):
            assert_(pr.clean_title(junk) == "", f"{junk!r}")
    results.append(t["passed"])

    with test_case("script detection: Devanagari transcripts are told apart from English and romanised Hindi") as t:
        hi = [{"text": "\u092b\u094b\u0928 \u0906\u092a\u0915\u094b \u0915\u0948\u0938\u0947 \u0915\u092e\u091c\u093c\u094b\u0930 \u092c\u0928\u093e\u0924\u093e \u0939\u0948"}]
        assert_(pr.dominant_script(hi) == "dev", "Hindi in Devanagari")
        assert_(pr.dominant_script([{"text": "Phones make you dumber"}]) == "lat", "English")
        assert_(pr.dominant_script([{"text": "Yeh phone aapko kamzor banata hai"}]) == "lat", "romanised Hindi (Hinglish)")
        assert_(pr.dominant_script(hi + [{"text": "Phones make you dumber again and again"}] * 4) == "lat", "mostly English with a little Hindi")
        assert_(pr.dominant_script([]) == "lat" and pr.dominant_script([{"text": "123 ... !!!"}]) == "lat", "empty / no letters")
        assert_(pr.has_devanagari("Phone \u092b\u094b\u0928") and not pr.has_devanagari("Phone") and not pr.has_devanagari(None), "has_devanagari")
    results.append(t["passed"])

    with test_case("a missing title falls back to the first beat's label, with a warning") as t:
        plan = dict(GOOD)
        plan.pop("title")
        r = validate_plan(plan, SENTS)
        assert_(r.plan["title"] == "Hook" and any("no title" in w for w in r.warnings), f"{r.plan['title']} {r.warnings}")
        blank = dict(plan, beats=[dict(b, label="") for b in plan["beats"]])
        assert_(validate_plan(blank, SENTS).plan["title"] == "", "no title and no label")
    results.append(t["passed"])

    with test_case("a good single stretch passes") as t:
        plan = {"shape": "single_stretch", "reason": "One strong argument.",
                "beats": [beat("S5", "S9", "Whole idea")]}  # 5 sentences = 27.5 s
        r = validate_plan(plan, SENTS)
        assert_(r.ok and r.plan["beats"][0]["role"] == "stretch", f"{r.errors}")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("sentence objects are accepted as well as dicts") as t:
        from worker.text.sentences import Sentence

        objs = [Sentence(s["id"], s["start"], s["end"], s["text"], s["first_word"], s["last_word"]) for s in SENTS]
        assert_(validate_plan(GOOD, objs).ok, "objects rejected")
    results.append(t["passed"])

    with test_case("a beat with an unknown sentence id is dropped, the plan survives") as t:
        plan = montage(*GOOD["beats"], beat("S99", "S99"))
        r = validate_plan(plan, SENTS)
        assert_(r.ok and len(r.plan["beats"]) == 4, f"{r.errors}")
        assert_(any("unknown sentence id" in x for x in r.repairs), f"{r.repairs}")
    results.append(t["passed"])

    with test_case("losing the hook rejects the plan") as t:
        plan = montage(beat("S99", "S99", "Hook"), *GOOD["beats"][1:])
        r = validate_plan(plan, SENTS)
        assert_(not r.ok and "hook" in r.errors[0], f"{r.errors}")
    results.append(t["passed"])

    with test_case("an overlapping beat is dropped") as t:
        plan = montage(*GOOD["beats"], beat("S20", "S22"))
        r = validate_plan(plan, SENTS)
        assert_(r.ok and len(r.plan["beats"]) == 4, f"{r.errors}")
        assert_(any("overlaps" in x for x in r.repairs), f"{r.repairs}")
    results.append(t["passed"])

    with test_case("an over-long beat is trimmed back to whole sentences") as t:
        plan = montage(beat("S2", "S2", "Hook"), beat("S4", "S8"), beat("S20", "S21"), beat("S30", "S30"))
        r = validate_plan(plan, SENTS)  # S4-S8 = 5 sentences = 27.5 s -> trimmed to S4-S6 = 16 s
        assert_(r.ok and r.plan["beats"][1]["to_id"] == "S6", f"{r.errors} {r.plan and r.plan['beats'][1]}")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("a too-short beat is dropped") as t:
        plan = montage(*GOOD["beats"], beat("S41", "S41"))
        r = validate_plan(plan, SENTS)
        assert_(r.ok and len(r.plan["beats"]) == 4, f"{r.errors}")
        assert_(any("too short" in x for x in r.repairs), f"{r.repairs}")
    results.append(t["passed"])

    with test_case("long labels are shortened; bad joins fixed; reversed ids swapped") as t:
        plan = montage(
            beat("S2", "S2", "H" * 40),
            beat("S5", "S4", "Reversed", join="teleport"),
            beat("S20", "S21"), beat("S30", "S30", join="topic_change"))
        r = validate_plan(plan, SENTS)
        assert_(r.ok, f"{r.errors}")
        assert_(len(r.plan["beats"][0]["label"]) == 24, "label")
        assert_(r.plan["beats"][1]["from_id"] == "S4" and r.plan["beats"][1]["join"] == "topic_change", f"{r.plan['beats'][1]}")
        assert_(r.plan["beats"][-1]["join"] == "continuous", "last join")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("needs_context starts the beat one sentence earlier") as t:
        plan = montage(beat("S2", "S2", "Hook"), beat("S5", "S5", needs_context=True),
                       beat("S20", "S21"), beat("S30", "S30"))
        r = validate_plan(plan, SENTS)
        assert_(r.ok and r.plan["beats"][1]["from_id"] == "S4", f"{r.errors}")
        assert_(any("needs context" in x for x in r.repairs), f"{r.repairs}")
    results.append(t["passed"])

    with test_case("too many beats are cut to 8, keeping the close") as t:
        beats = [beat(f"S{i}", f"S{i}") for i in range(2, 22, 2)]  # 10 beats of 5 s
        beats[-1]["role"] = "close"
        r = validate_plan(montage(*beats), SENTS)
        assert_(r.ok and len(r.plan["beats"]) == 8, f"{r.errors}")
        assert_(r.plan["beats"][-1]["role"] == "close" and r.plan["beats"][-1]["from_id"] == "S20", "close lost")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("a total over 60s drops the last points but keeps at least 3 beats") as t:
        beats = [beat(f"S{i}", f"S{i + 2}") for i in range(1, 30, 4)]  # 7 beats of 16 s = 112 s
        r = validate_plan(montage(*beats), SENTS)
        assert_(r.ok and 3 <= len(r.plan["beats"]) and r.total_seconds <= 60.0, f"{r.errors} {r.total_seconds}")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("too short overall, or fewer than 3 beats, is rejected") as t:
        r = validate_plan(montage(beat("S2", "S2"), beat("S4", "S4"), beat("S6", "S6")), SENTS)  # 15 s
        assert_(not r.ok and "minimum" in r.errors[0], f"{r.errors}")
        r = validate_plan(montage(beat("S2", "S3"), beat("S5", "S7")), SENTS)
        assert_(not r.ok and "at least 3" in r.errors[0], f"{r.errors}")
    results.append(t["passed"])

    with test_case("out-of-order beats need a reorder_reason") as t:
        plan = montage(beat("S30", "S30", "Hook"), beat("S4", "S5"), beat("S20", "S21"), beat("S33", "S33"))
        r = validate_plan(plan, SENTS)
        assert_(not r.ok and "reorder_reason" in r.errors[0], f"{r.errors}")
        r = validate_plan(dict(plan, reorder_reason="The strongest line works as the opener."), SENTS)
        assert_(r.ok, f"{r.errors}")
        check_invariants(r, SENTS)
    results.append(t["passed"])

    with test_case("single stretch: extra beats ignored, over 45s trimmed, under 20s rejected") as t:
        plan = {"shape": "single_stretch", "reason": "x", "beats": [beat("S5", "S9"), beat("S20", "S22")]}
        r = validate_plan(plan, SENTS)
        assert_(r.ok and len(r.plan["beats"]) == 1 and any("keeps one beat" in x for x in r.repairs), f"{r.errors}")
        r = validate_plan({"shape": "single_stretch", "reason": "x", "beats": [beat("S1", "S12")]}, SENTS)  # 12 sentences = 66 s
        assert_(r.ok and r.total_seconds <= 45.0, f"{r.errors} {r.total_seconds}")
        check_invariants(r, SENTS)
        r = validate_plan({"shape": "single_stretch", "reason": "x", "beats": [beat("S1", "S2")]}, SENTS)
        assert_(not r.ok, "10 s single stretch should be rejected")
    results.append(t["passed"])

    with test_case("missing reason, bad shape and garbage are rejected without crashing") as t:
        for bad in (None, [], "plan", 7, {}, {"shape": "carousel", "reason": "x", "beats": [beat("S1", "S2")]},
                    dict(GOOD, reason="  "), dict(GOOD, beats=[]), dict(GOOD, beats="nope"),
                    montage(None, 5, "x", {"from_id": 1}), montage({}), montage(beat(None, None))):
            r = validate_plan(bad, SENTS)
            assert_(not r.ok and r.errors and r.plan is None, f"accepted garbage: {bad!r}")
    results.append(t["passed"])

    with test_case("random plans: never crashes, accepted plans always satisfy every rule") as t:
        rng = random.Random(99)
        ids = [s["id"] for s in SENTS] + ["S99", None, 3]
        accepted = 0
        for trial in range(1500):
            shape = rng.choice(["montage"] * 4 + ["single_stretch", "weird"])
            beats = []
            for _ in range(rng.randint(0, 11)):
                b = beat(rng.choice(ids), rng.choice(ids), label=rng.choice(["ok", "x" * 50, None, 5]),
                         join=rng.choice(list(pr.JOINS) + ["bad", None]))
                if rng.random() < 0.15:
                    b["needs_context"] = True
                if rng.random() < 0.1:
                    b["role"] = "close"
                beats.append(b)
            plan = {"shape": shape, "reason": rng.choice(["fine", "", None]), "beats": beats}
            if rng.random() < 0.3:
                plan["reorder_reason"] = "allowed"
            before = copy.deepcopy(plan)
            r = validate_plan(plan, SENTS)
            assert_(plan == before, f"trial {trial}: input modified")
            if r.ok:
                accepted += 1
                check_invariants(r, SENTS)
        assert_(accepted >= 20, f"only {accepted} accepted; test is not exercising the rules")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
