"""Reproduce and verify the 400 INVALID_ARGUMENT fix.
Tests the normalized generate_content payload flow after the client.py fix.
"""
import asyncio
import logging
import sys
from typing import Any

# Add project root to path
sys.path.insert(0, r"C:\Users\itsme\OneDrive\Desktop\Projects\reels-generator")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reproduce")


async def reproduce():
    from worker.gemini.client import gemini_client, genai_types
    from worker.gemini.prompts import TRANSCRIPTION_PLANNING_PROMPT
    from worker.gemini.schemas import TRANSCRIPT_PLAN_SCHEMA_DICT

    logger.info("GEMINI_MODEL: %s", gemini_client.model_id)
    logger.info("Schema keys: %s", list(TRANSCRIPT_PLAN_SCHEMA_DICT.keys()))

    # Test 1: contents with bare Part.from_uri() — the transcribe_plan.py case
    logger.info("\n=== Test 1: bare Part.from_uri() (transcribe_plan.py flow) ===")
    test_uri = "files/ABCDEF"
    contents = [
        genai_types.Part.from_uri(
            file_uri=test_uri,
            mime_type="video/mp4",
        ),
    ]

    # Simulate the normalized client.generate_content flow (new code in client.py)
    user_roles = [
        c for c in contents
        if isinstance(c, genai_types.Content) and c.role == "user"
    ]
    part_objects = [
        c for c in contents
        if isinstance(c, genai_types.Part) and not isinstance(c, genai_types.Content)
    ]

    if user_roles:
        # Case 1: contents already has a user role Content.
        # Merge the prompt part into it so the SDK doesn't add a duplicate.
        existing = user_roles[0]
        full_contents = list(contents)
        existing.parts.append(genai_types.Part(text=TRANSCRIPTION_PLANNING_PROMPT[:50]))
        logger.info("Case 1: existing user role, merged prompt into it")
        logger.info("  full_contents has %d item(s):", len(full_contents))
        for i, c in enumerate(full_contents):
            c_role = getattr(c, 'role', '?')
            c_parts = len(c.parts) if hasattr(c, 'parts') else 0
            logger.info("    [%d] role=%s, parts=%d", i, c_role, c_parts)
        # SDK's user_content append is SKIPPED to avoid duplicate user roles.
    elif part_objects:
        # Case 2: contents has bare Part objects (e.g. Part.from_uri() from
        # transcribe_plan.py). Wrap ALL parts into a single Content(role="user")
        # so the Gemini API receives exactly one user role with all media+prompt.
        full_contents: list = []
        # First, add any existing Content objects from contents
        for c in contents:
            if isinstance(c, genai_types.Content) and c.role != "user":
                full_contents.append(c)
        # Then, wrap all part objects into ONE Content(role="user")
        all_parts: list = list(part_objects)
        full_contents.append(
            genai_types.Content(role="user", parts=all_parts)
        )
        logger.info("Case 2: bare Parts wrapped into single Content(role='user')")
        logger.info("  full_contents has %d Content(s):", len(full_contents))
        for i, c in enumerate(full_contents):
            c_role = getattr(c, 'role', '?')
            c_parts = len(c.parts) if hasattr(c, 'parts') else 0
            logger.info("    [%d] role=%s, parts=%d", i, c_role, c_parts)
        # SDK's user_content append is SKIPPED — we already have one user role.
    else:
        # Case 3: no user role and no bare Parts (or only non-user Content).
        # Normalize any Part objects into Content(role="user"), keep other Content,
        # then append the SDK's user_content with the prompt text.
        normalized: list = []
        for c in contents:
            if isinstance(c, genai_types.Part):
                normalized.append(
                    genai_types.Content(role="user", parts=[c])
                )
            elif isinstance(c, genai_types.Content):
                normalized.append(c)
            else:
                # Unknown type; wrap bare value
                normalized.append(
                    genai_types.Content(role="user", parts=[genai_types.Part(text=str(c))])
                )
        full_contents = normalized

        # Now append the SDK's user_content with the prompt
        user_content = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=TRANSCRIPTION_PLANNING_PROMPT[:50])],
        )
        full_contents = [*full_contents, user_content]
        logger.info("Case 3: normalized + SDK user_content appended")
        logger.info("  full_contents has %d Content(s):", len(full_contents))
        for i, c in enumerate(full_contents):
            c_role = getattr(c, 'role', '?')
            c_parts = len(c.parts) if hasattr(c, 'parts') else 0
            logger.info("    [%d] role=%s, parts=%d", i, c_role, c_parts)

    # Now verify: exactly one user role in full_contents
    user_roles_total = [
        c for c in full_contents
        if getattr(c, 'role', None) == "user"
    ]
    logger.info("\nUser role count in full_contents: %d (expected: 1)", len(user_roles_total))

    # Test 2: actual API call
    logger.info("\n=== Test 2: actual generate_content API call ===")
    try:
        response_text = gemini_client.generate_content(
            contents=[
                genai_types.Part.from_uri(
                    file_uri="files/ABCDEF",
                    mime_type="video/mp4",
                ),
            ],
            prompt="Identify the key scenes in this video and summarize the plot.",
            response_schema=TRANSCRIPT_PLAN_SCHEMA_DICT,
        )
        logger.info("SUCCESS: response_text = %s", response_text[:200])
    except Exception as e:
        logger.error("ERROR type: %s", type(e).__name__)
        logger.error("ERROR message: %s", str(e))
        if hasattr(e, 'response') and e.response is not None:
            logger.error("ERROR response: %s", e.response)
        if hasattr(e, 'code'):
            logger.error("ERROR code: %s", e.code)
        if hasattr(e, 'message'):
            logger.error("ERROR message attr: %s", e.message)
        if hasattr(e, 'details'):
            logger.error("ERROR details: %s", e.details)
        if hasattr(e, 'request') and e.request is not None:
            logger.error("REQUEST BODY: %s", e.request)

    logger.info("\n=== Test done ===")


asyncio.run(reproduce())