"""Test 10: Speech-to-text adapter (worker/worker/asr).

Offline: Groq is replaced by a fake HTTP server (httpx.MockTransport) and
ffmpeg generates a tiny test video. No network, no API key, no cost.
"""
import os
import subprocess
import sys
import tempfile

import httpx

from _helpers import test_case, assert_, section

SECRET = "gsk_test_SECRET_do_not_leak"

ENGLISH = {
    "text": " Hello world. This is a test.",
    "language": "English",
    "duration": 3.2,
    "segments": [
        {"start": 0.0, "end": 1.4, "text": " Hello world."},
        {"start": 1.5, "end": 3.2, "text": " This is a test."},
    ],
    "words": [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world.", "start": 0.6, "end": 1.4},
        {"word": "This", "start": 1.5, "end": 1.8},
        {"word": "is", "start": 1.8, "end": 2.0},
        {"word": "a", "start": 2.0, "end": 2.1},
        {"word": "test.", "start": 2.1, "end": 3.2},
    ],
}

HINDI = {
    "text": "नमस्ते दोस्तों। आज हम सीखेंगे।",
    "language": "Hindi",
    "duration": 4.0,
    "segments": [{"start": 0.0, "end": 4.0, "text": "नमस्ते दोस्तों। आज हम सीखेंगे।"}],
    "words": [
        {"word": "नमस्ते", "start": 0.0, "end": 0.7},
        {"word": "दोस्तों।", "start": 0.8, "end": 1.6},
        {"word": "आज", "start": 2.0, "end": 2.3},
        {"word": "हम", "start": 2.3, "end": 2.6},
        {"word": "सीखेंगे।", "start": 2.6, "end": 4.0},
    ],
}


def _make_video(path: str, with_audio: bool) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest"]
    cmd += [path]
    subprocess.run(cmd, check=True)


def _provider(handler, **kw):
    from worker.asr.groq_provider import GroqProvider

    return GroqProvider(
        api_key=SECRET, model="whisper-large-v3-turbo", backoff_s=0.0,
        transport=httpx.MockTransport(handler), **kw,
    )


async def main() -> bool:
    section("Test 10: Speech-to-text adapter")
    from worker.asr import ASRError, TranscriptResult, get_asr_provider, transcribe_with_fallback
    from worker.asr.audio import build_extract_cmd, extract_audio
    from worker.asr.groq_provider import parse_response

    results = []
    tmp = tempfile.mkdtemp(prefix="test10_")
    video = os.path.join(tmp, "with_audio.mp4")
    silent = os.path.join(tmp, "no_audio.mp4")
    _make_video(video, with_audio=True)
    _make_video(silent, with_audio=False)

    with test_case("English response converts to our shape") as t:
        r = parse_response(ENGLISH)
        assert_(r.provider == "groq" and r.language == "English", f"{r}")
        assert_(r.full_text == "Hello world. This is a test.", r.full_text)
        assert_(len(r.words) == 6 and len(r.segments) == 2, f"{len(r.words)} words")
        assert_(r.words_for_db()[0] == {"text": "Hello", "start": 0.0, "end": 0.5}, f"{r.words_for_db()[0]}")
        assert_(r.duration == 3.2, f"duration {r.duration}")
    results.append(t["passed"])

    with test_case("Hindi (Devanagari) text and words are preserved exactly") as t:
        r = parse_response(HINDI)
        assert_(r.words[1].text == "दोस्तों।", f"{r.words[1].text!r}")
        assert_(r.language == "Hindi" and "नमस्ते" in r.full_text, f"{r.language} {r.full_text}")
    results.append(t["passed"])

    with test_case("blank words are dropped and reversed times are fixed") as t:
        r = parse_response({"text": "hi", "words": [
            {"word": "  ", "start": 0, "end": 1}, {"word": " hi ", "start": 2.0, "end": 1.0}]})
        assert_(len(r.words) == 1 and r.words[0].text == "hi", f"{r.words}")
        assert_(r.words[0].start == 1.0 and r.words[0].end == 2.0, f"{r.words[0]}")
    results.append(t["passed"])

    with test_case("no speech gives an empty result; text without word times is an error") as t:
        r = parse_response({"text": "", "words": [], "segments": []})
        assert_(r.full_text == "" and r.words == [] and r.duration == 0.0, f"{r}")
        try:
            parse_response({"text": "some text", "words": []})
            raise AssertionError("expected ASRError")
        except ASRError:
            pass
    results.append(t["passed"])

    with test_case("request carries model, word+segment timestamps and the key") as t:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = request.read()
            return httpx.Response(200, json=ENGLISH)

        r = await _provider(handler).transcribe(video)
        body = seen["body"]
        assert_(seen["auth"] == f"Bearer {SECRET}", "bad auth header")
        assert_(b"whisper-large-v3-turbo" in body, "model missing")
        assert_(b"verbose_json" in body, "response_format missing")
        assert_(body.count(b'name="timestamp_granularities[]"') == 2, "need word and segment")
        assert_(isinstance(r, TranscriptResult) and len(r.words) == 6, f"{r}")
    results.append(t["passed"])

    with test_case("busy (429) then success is retried and returns the result") as t:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, text="rate limited", headers={"retry-after": "0"})
            return httpx.Response(200, json=ENGLISH)

        r = await _provider(handler).transcribe(video)
        assert_(calls["n"] == 2 and len(r.words) == 6, f"calls={calls['n']}")
    results.append(t["passed"])

    with test_case("bad key (401) fails at once, is not retried, never shows the key") as t:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, json={"error": "invalid api key"})

        try:
            await _provider(handler).transcribe(video)
            raise AssertionError("expected ASRError")
        except ASRError as exc:
            assert_(calls["n"] == 1, f"calls={calls['n']}")
            assert_(SECRET not in str(exc), "key leaked in error")
    results.append(t["passed"])

    with test_case("server errors give up after 3 attempts") as t:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, text="down")

        try:
            await _provider(handler).transcribe(video)
            raise AssertionError("expected ASRError")
        except ASRError as exc:
            assert_(calls["n"] == 3, f"calls={calls['n']}")
            assert_("3 attempts" in str(exc), str(exc))
    results.append(t["passed"])

    with test_case("network failure is retried and never shows the key") as t:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("boom", request=request)

        try:
            await _provider(handler).transcribe(video)
            raise AssertionError("expected ASRError")
        except ASRError as exc:
            assert_(calls["n"] == 3, f"calls={calls['n']}")
            assert_(SECRET not in str(exc), "key leaked in error")
    results.append(t["passed"])

    with test_case("audio extraction makes a small mp3; a video with no audio fails") as t:
        cmd = build_extract_cmd("in.mp4", "out.mp3")
        assert_("-map" in cmd and "libmp3lame" in cmd and "16000" in cmd, f"{cmd}")
        path = await extract_audio(video)
        try:
            assert_(path.endswith(".mp3") and os.path.getsize(path) > 0, "empty mp3")
        finally:
            os.remove(path)
        try:
            await extract_audio(silent)
            raise AssertionError("expected ASRError")
        except ASRError:
            pass
    results.append(t["passed"])

    with test_case("provider selection by name and by missing key") as t:
        from worker.asr.gemini_provider import GeminiProvider
        from worker.asr.groq_provider import GroqProvider
        from worker.config import settings

        assert_(isinstance(get_asr_provider("gemini"), GeminiProvider), "gemini")
        old = settings.GROQ_API_KEY
        try:
            settings.GROQ_API_KEY = ""
            try:
                get_asr_provider("groq")
                raise AssertionError("expected ASRError for missing key")
            except ASRError:
                pass
            settings.GROQ_API_KEY = SECRET
            assert_(isinstance(get_asr_provider("groq"), GroqProvider), "groq")
        finally:
            settings.GROQ_API_KEY = old
        try:
            get_asr_provider("whisperx")
            raise AssertionError("expected ASRError for unknown name")
        except ASRError:
            pass
    results.append(t["passed"])

    with test_case("fallback: primary ok, primary fails, both fail") as t:
        good = parse_response(ENGLISH)

        class Fake:
            def __init__(self, name, fail=False):
                self.name, self.fail = name, fail

            async def transcribe(self, path):
                if self.fail:
                    raise ASRError(f"{self.name} down")
                return good

        r, meta = await transcribe_with_fallback("x", Fake("groq"), Fake("gemini"))
        assert_(meta == {"provider": "groq", "fallback_used": False, "primary_error": None}, f"{meta}")

        r, meta = await transcribe_with_fallback("x", Fake("groq", fail=True), Fake("gemini"))
        assert_(meta["provider"] == "gemini" and meta["fallback_used"], f"{meta}")
        assert_("groq down" in meta["primary_error"], f"{meta}")

        try:
            await transcribe_with_fallback("x", Fake("groq", fail=True), Fake("gemini", fail=True))
            raise AssertionError("expected ASRError")
        except ASRError:
            pass

        try:
            await transcribe_with_fallback("x", Fake("gemini", fail=True), Fake("gemini"))
            raise AssertionError("gemini primary must not fall back to itself")
        except ASRError:
            pass
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
