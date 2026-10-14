"""Test 22: Retry the same Gemini model on temporary errors (worker/gemini/client.py).

Offline: Gemini is replaced by fakes and nothing really sleeps. No network, no cost.
"""
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google.genai import errors as gerr
from pydantic import BaseModel

from _helpers import test_case, assert_, section


def server_error(code=503, status="UNAVAILABLE"):
    return gerr.ServerError(code, {"error": {"code": code, "message": "overloaded", "status": status}})


def client_error(code, status="INVALID_ARGUMENT"):
    return gerr.ClientError(code, {"error": {"code": code, "message": "nope", "status": status}})


class Dummy(BaseModel):
    a: int


async def main() -> bool:
    section("Test 22: Gemini transient-error retry")
    from worker.config import WorkerSettings, settings
    from worker.gemini import client as c
    from worker.stages import transcribe_plan

    results = []
    chain = settings.gemini_fallback_chain
    waits = []

    def fake_sleep(s):
        waits.append(s)

    async def fake_sleep_async(s):
        waits.append(s)

    base = dict(SUPABASE_URL="u", SUPABASE_KEY="k", GEMINI_API_KEY="g")

    with test_case("which errors count as temporary") as t:
        for exc in (server_error(503), server_error(500, "INTERNAL"), client_error(429, "RESOURCE_EXHAUSTED"),
                    Exception("503 UNAVAILABLE: model is overloaded"), httpx.ReadTimeout("slow"),
                    httpx.ConnectError("down"), Exception("Deadline exceeded")):
            assert_(c.is_transient(exc), f"should be temporary: {type(exc).__name__} {exc}")
        for exc in (client_error(400), client_error(403, "PERMISSION_DENIED"), client_error(404, "NOT_FOUND"),
                    ValueError("bad value"), json.JSONDecodeError("x", "y", 0), KeyError("k")):
            assert_(not c.is_transient(exc), f"should NOT be temporary: {type(exc).__name__} {exc}")
    results.append(t["passed"])

    with test_case("waits grow: about 2s, then about 4s (plus under 1s of jitter)") as t:
        assert_(2.0 <= c._retry_delay(1) < 3.0 and 4.0 <= c._retry_delay(2) < 5.0, f"{c._retry_delay(1)} {c._retry_delay(2)}")
    results.append(t["passed"])

    with test_case("a call that fails twice then works is retried and returns the result") as t:
        waits.clear()
        seq = [server_error(), server_error(), "ok"]

        def fn():
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v

        with patch.object(c, "_sleep", fake_sleep):
            assert_(c.call_with_retry(fn, "m") == "ok", "result")
        assert_(len(waits) == 2 and waits[1] > waits[0], f"{waits}")
    results.append(t["passed"])

    with test_case("it gives up after the retries and raises the last error; non-temporary errors are not retried") as t:
        waits.clear()
        calls = {"n": 0}

        def always_down():
            calls["n"] += 1
            raise server_error()

        with patch.object(c, "_sleep", fake_sleep):
            try:
                c.call_with_retry(always_down)
                raise AssertionError("expected an error")
            except gerr.ServerError:
                pass
        assert_(calls["n"] == 1 + settings.GEMINI_TRANSIENT_RETRIES == 3 and len(waits) == 2, f"{calls} {waits}")

        calls["n"] = 0
        waits.clear()

        def bad_request():
            calls["n"] += 1
            raise client_error(400)

        with patch.object(c, "_sleep", fake_sleep):
            try:
                c.call_with_retry(bad_request)
                raise AssertionError("expected an error")
            except gerr.ClientError:
                pass
        assert_(calls["n"] == 1 and waits == [], f"{calls} {waits}")
    results.append(t["passed"])

    with test_case("GEMINI_TRANSIENT_RETRIES=0 turns retrying off") as t:
        waits.clear()
        calls = {"n": 0}

        def down():
            calls["n"] += 1
            raise server_error()

        with patch.object(settings, "GEMINI_TRANSIENT_RETRIES", 0), patch.object(c, "_sleep", fake_sleep):
            try:
                c.call_with_retry(down)
            except gerr.ServerError:
                pass
        assert_(calls["n"] == 1 and waits == [], f"{calls} {waits}")
    results.append(t["passed"])

    def models_fake(script, seen):
        """Fake models.generate_content: `script` maps a call number to an error or a text."""
        def gen(model, contents, config):
            seen.append(model)
            v = script(len(seen), model)
            if isinstance(v, Exception):
                raise v
            return SimpleNamespace(text=v)
        return gen

    with test_case("the text client stays on the strong model when a retry works") as t:
        waits.clear()
        seen = []
        fake = models_fake(lambda n, m: server_error() if n == 1 else json.dumps({"a": 1}), seen)
        with patch.object(c, "_sleep", fake_sleep), patch.object(c.gemini_client.client.models, "generate_content", fake):
            text = c.gemini_client.generate_content(contents=[], prompt="p", response_schema={"type": "object"})
        assert_(json.loads(text) == {"a": 1}, text)
        assert_(seen == [chain[0], chain[0]], f"models used: {seen}")
    results.append(t["passed"])

    with test_case("the text client moves to the next model only after the retries are used up") as t:
        waits.clear()
        seen = []
        fake = models_fake(lambda n, m: server_error() if m == chain[0] else json.dumps({"a": 2}), seen)
        with patch.object(c, "_sleep", fake_sleep), patch.object(c.gemini_client.client.models, "generate_content", fake):
            text = c.gemini_client.generate_content(contents=[], prompt="p", response_schema={"type": "object"})
        assert_(seen == [chain[0]] * 3 + [chain[1]], f"models used: {seen}")
    results.append(t["passed"])

    with test_case("a model that does not exist (404) is skipped at once, without waiting") as t:
        waits.clear()
        seen = []
        fake = models_fake(lambda n, m: client_error(404, "NOT_FOUND") if m == chain[0] else json.dumps({"a": 3}), seen)
        with patch.object(c, "_sleep", fake_sleep), patch.object(c.gemini_client.client.models, "generate_content", fake):
            c.gemini_client.generate_content(contents=[], prompt="p", response_schema={"type": "object"})
        assert_(seen == [chain[0], chain[1]] and waits == [], f"{seen} {waits}")
    results.append(t["passed"])

    with test_case("the video calls (understand, verify) get the same protection") as t:
        waits.clear()
        seen = []
        fake = models_fake(lambda n, m: server_error() if n <= 2 else json.dumps({"a": 5}), seen)
        with patch.object(c, "_sleep_async", fake_sleep_async), \
                patch.object(c.gemini_client.client.models, "generate_content", fake):
            out = await transcribe_plan._gemini_call_with_fallback(
                video_id="v", video_file_uri="https://files/x", prompt="p",
                response_schema={"type": "object"}, response_model=Dummy)
        assert_(out.a == 5 and seen == [chain[0]] * 3, f"{out} {seen}")
        assert_(len(waits) == 2, f"{waits}")
    results.append(t["passed"])

    with test_case("the setting defaults to 2 and only accepts 0 to 5") as t:
        assert_(settings.GEMINI_TRANSIENT_RETRIES == 2, f"{settings.GEMINI_TRANSIENT_RETRIES}")
        assert_(WorkerSettings(**base, GEMINI_TRANSIENT_RETRIES=0).GEMINI_TRANSIENT_RETRIES == 0, "0 allowed")
        for bad in (-1, 6, 50):
            try:
                WorkerSettings(**base, GEMINI_TRANSIENT_RETRIES=bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
    results.append(t["passed"])

    return all(results)


if __name__ == "__main__":
    import asyncio

    sys.exit(0 if asyncio.run(main()) else 1)
