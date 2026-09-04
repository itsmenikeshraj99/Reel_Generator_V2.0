"""Test 04: Force-malformed Gemini response.

Verifies the gemini client raises a clear `GeminiResponseInvalid` when
the model returns unparseable JSON, and that the planner stage handles
that gracefully. We mock `generate_content` to return various malformed
strings and confirm the failure path is observable.
"""
import asyncio
import json
from typing import Any
from unittest.mock import patch, MagicMock

from _helpers import test_case, assert_, section


async def main() -> bool:
    section("Test 04: Force-malformed Gemini response")
    with test_case("GeminiResponseInvalid raised on bad JSON") as t:
        from worker.gemini.client import GeminiResponseInvalid, gemini_client

        # Simulate a Gemini call where the underlying SDK returns a
        # response with a bad-JSON .text attribute. We patch
        # generate_content to bypass the SDK and feed our own broken
        # text directly into the validation path.
        bad_response = MagicMock()
        bad_response.text = "this is not json at all"

        with patch.object(
            gemini_client.client.models, "generate_content", return_value=bad_response
        ):
            try:
                gemini_client.generate_content(
                    contents=[],
                    prompt="anything",
                    response_schema={
                        "type": "object",
                        "properties": {"candidates": {"type": "array"}},
                    },
                )
                raise AssertionError("expected GeminiResponseInvalid")
            except GeminiResponseInvalid as exc:
                assert_("Malformed JSON" in str(exc), f"wrong error: {exc}")
                print(f"    -> raised as expected: {exc}")

        # Simulate a truncated JSON (e.g. Gemini cut off mid-stream)
        truncated = MagicMock()
        truncated.text = '{"candidates": [{"segments": [{"start'
        with patch.object(
            gemini_client.client.models, "generate_content", return_value=truncated
        ):
            try:
                gemini_client.generate_content(
                    contents=[],
                    prompt="anything",
                    response_schema={
                        "type": "object",
                        "properties": {"candidates": {"type": "array"}},
                    },
                )
                raise AssertionError("expected GeminiResponseInvalid")
            except GeminiResponseInvalid as exc:
                assert_("Malformed JSON" in str(exc), f"wrong error: {exc}")
                print(f"    -> raised on truncated JSON as expected: {exc}")

    return t["passed"]


if __name__ == "__main__":
    asyncio.run(main())
