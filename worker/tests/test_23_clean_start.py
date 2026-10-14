"""Test 23: Clean starts (worker/worker/text/starts.py).

Pure Python, offline: no network, no AI, no cost.
"""
import sys

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 23: Clean starts")
    from worker.text.starts import starts_cleanly

    results = []

    with test_case("English: fillers, joiners and lowercase continuations are not clean starts") as t:
        for bad in ("yeah like right now it's not here", "Yeah, exactly.", "So what happens next?", "And then he left.",
                    "but that is not true", "Okay, here we go.", "Well, I think so.", "Because it matters.",
                    "um I was saying", "  ...and so on", "\"so he said\"", "Right.", "which is why"):
            assert_(not starts_cleanly(bad), f"should not be clean: {bad!r}")
    results.append(t["passed"])

    with test_case("English: complete sentences are clean starts") as t:
        for good in ("Here is the experiment they ran.", "What can an average 25-year-old follow in their life?",
                     "The first and most pervasive issue is notifications.", "\"We are leaving now,\" he said.",
                     "iPhone sales doubled.".capitalize(), "Notifications are the problem.", "Your phone makes you dumber.",
                     "12 people took part in the study."):
            assert_(starts_cleanly(good), f"should be clean: {good!r}")
    results.append(t["passed"])

    with test_case("Hindi: joiners and fillers are not clean starts; full sentences are") as t:
        for bad in ("तो फिर हम क्या करें", "और उसके बाद क्या हुआ", "हाँ बिल्कुल सही", "अच्छा ठीक है", "लेकिन यह सच नहीं है"):
            assert_(not starts_cleanly(bad), f"should not be clean: {bad!r}")
        for good in ("आज हम सीखेंगे कि फोन कैसे ध्यान भटकाता है।", "यह अध्ययन टेक्सास में किया गया था।"):
            assert_(starts_cleanly(good), f"should be clean: {good!r}")
    results.append(t["passed"])

    with test_case("empty or odd input never crashes") as t:
        for odd in ("", "   ", None, "...", "—", "123", "…"):
            assert_(isinstance(starts_cleanly(odd), bool), f"{odd!r}")
        assert_(starts_cleanly("") is True and starts_cleanly(None) is True, "empty counts as clean")
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
