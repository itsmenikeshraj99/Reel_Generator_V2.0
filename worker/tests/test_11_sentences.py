"""Test 11: Sentence index (worker/worker/text/sentences.py).

Pure Python, offline: no network, no AI, no cost.
"""
import random
import sys

from _helpers import test_case, assert_, section


def mk(spec, dur=0.3, gap=0.1, start=0.0):
    """Build word dicts from a space-separated string or a list of
    (text, gap_before) tuples. Times run on from `start`."""
    items = [(t, gap) for t in spec.split()] if isinstance(spec, str) else spec
    words, t = [], start
    for text, g in items:
        t += g
        words.append({"text": text, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    return words


def texts(sentences):
    return [s.text for s in sentences]


async def main() -> bool:
    section("Test 11: Sentence index")
    from worker.text.sentences import MAX_SENTENCE_S, build_sentences

    results = []

    with test_case("English: splits on . ? and numbers S1, S2 ...") as t:
        s = build_sentences(mk("Hello there my friend. How are you today? I am doing fine!"))
        assert_(texts(s) == ["Hello there my friend.", "How are you today?", "I am doing fine!"], texts(s))
        assert_([x.id for x in s] == ["S1", "S2", "S3"], [x.id for x in s])
    results.append(t["passed"])

    with test_case("times and word indexes come from the first and last word") as t:
        words = mk("Hello there my friend. How are you today?")
        s = build_sentences(words)
        assert_(s[0].start == words[0]["start"] and s[0].end == words[3]["end"], f"{s[0]}")
        assert_((s[1].first_word, s[1].last_word) == (4, 7), f"{s[1]}")
        assert_(s[1].to_dict()["id"] == "S2", "to_dict")
    results.append(t["passed"])

    with test_case("Hindi: danda and question mark end sentences") as t:
        s = build_sentences(mk("नमस्ते दोस्तों आज हम सीखेंगे। क्या आप तैयार हैं? यह बहुत बढ़िया रहेगा।"))
        assert_(len(s) == 3, texts(s))
        assert_(s[0].text.endswith("सीखेंगे।") and s[1].text.endswith("हैं?"), texts(s))
    results.append(t["passed"])

    with test_case("no punctuation: pauses of 0.7s or more split") as t:
        spec = []
        for g in range(3):
            spec += [(w, 1.0 if i == 0 else 0.1) for i, w in enumerate("one two three four five".split())]
        s = build_sentences(mk(spec))
        assert_(len(s) == 3 and all(len(x.text.split()) == 5 for x in s), texts(s))
    results.append(t["passed"])

    with test_case("short pauses (under 0.7s) do not split") as t:
        s = build_sentences(mk("this is one long flowing thought", gap=0.5))
        assert_(len(s) == 1, texts(s))
    results.append(t["passed"])

    with test_case("5-minute monologue with no punctuation or pauses is cut into usable pieces") as t:
        words = mk([("w%d" % i, 0.1) for i in range(750)])
        s = build_sentences(words)
        assert_(len(s) >= 15, f"only {len(s)} sentences")
        assert_(all(x.end - x.start <= MAX_SENTENCE_S + 1e-6 for x in s), "a sentence is too long")
    results.append(t["passed"])

    with test_case("a long run is split at its longest pause") as t:
        spec = [("a%d" % i, 0.1) for i in range(40)] + [("b0", 0.6)] + [("b%d" % i, 0.1) for i in range(1, 40)]
        s = build_sentences(mk(spec))
        assert_(len(s) == 2 and s[0].last_word == 39, [(x.first_word, x.last_word) for x in s])
    results.append(t["passed"])

    with test_case("abbreviations and decimals do not end a sentence") as t:
        s = build_sentences(mk("Dr. Smith met Mr. Jones yesterday. They talked a lot."))
        assert_(texts(s) == ["Dr. Smith met Mr. Jones yesterday.", "They talked a lot."], texts(s))
        s = build_sentences(mk("It costs 3.5 dollars today. Really good value indeed."))
        assert_(len(s) == 2 and s[0].text.startswith("It costs 3.5"), texts(s))
    results.append(t["passed"])

    with test_case("quotes after the full stop and ellipsis still end a sentence") as t:
        s = build_sentences(mk('"We are leaving now." He said so quietly.'))
        assert_(len(s) == 2 and s[0].text == '"We are leaving now."', texts(s))
        s = build_sentences(mk("I was thinking about it… Then we decided to leave."))
        assert_(len(s) == 2, texts(s))
    results.append(t["passed"])

    with test_case("a tiny fragment merges into its neighbour") as t:
        s = build_sentences(mk("Okay. This is the main point of the talk."))
        assert_(len(s) == 1, texts(s))
        s = build_sentences(mk("Is that really the right answer here? Yes."))
        assert_(len(s) == 1, texts(s))
    results.append(t["passed"])

    with test_case("a fragment is not merged across a long silence") as t:
        s = build_sentences(mk([("Okay.", 0.0)] + [(w, 3.0 if i == 0 else 0.1) for i, w in enumerate("This is the main point.".split())]))
        assert_(len(s) == 2, texts(s))
    results.append(t["passed"])

    with test_case("a merge that would exceed the length cap is skipped") as t:
        long_run = [("x%d" % i, 0.1) for i in range(49)] + [("end.", 0.1)]  # ~20s, ends sentence
        s = build_sentences(mk(long_run + [("Yes.", 0.2)]))
        assert_(len(s) == 2, texts(s)[-1])
    results.append(t["passed"])

    with test_case("empty input gives no sentences; one word gives one") as t:
        assert_(build_sentences([]) == [], "empty")
        assert_(len(build_sentences(mk("Hi."))) == 1, "single word")
    results.append(t["passed"])

    with test_case("objects and dicts give the same result; output is repeatable") as t:
        from worker.asr.base import Word

        d = mk("Hello there my friend. How are you today? I am doing fine!")
        objs = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in d]
        a, b, c = build_sentences(d), build_sentences(objs), build_sentences(d)
        assert_([x.to_dict() for x in a] == [x.to_dict() for x in b] == [x.to_dict() for x in c], "differs")
    results.append(t["passed"])

    with test_case("random transcripts: every word in exactly one sentence, in order") as t:
        rng = random.Random(1234)
        vocab = ["alpha", "beta", "gamma.", "delta?", "eps!", "zeta", "eta", "theta,", "iota", "kappa।"]
        for trial in range(300):
            n = rng.randint(1, 120)
            spec = [(rng.choice(vocab), rng.choice([0.0, 0.05, 0.1, 0.4, 0.8, 2.5])) for _ in range(n)]
            words = mk(spec, dur=rng.choice([0.1, 0.3, 0.5]))
            s = build_sentences(words)
            assert_(s[0].first_word == 0 and s[-1].last_word == n - 1, f"trial {trial}: coverage ends")
            for a, b in zip(s, s[1:]):
                assert_(b.first_word == a.last_word + 1, f"trial {trial}: gap or overlap")
            for x in s:
                assert_(x.start == words[x.first_word]["start"] and x.end == words[x.last_word]["end"], "times")
                assert_(x.first_word == x.last_word or x.end - x.start <= MAX_SENTENCE_S + 1e-6,
                        f"trial {trial}: {x.id} too long")
            assert_([x.id for x in s] == [f"S{i}" for i in range(1, len(s) + 1)], "ids")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
