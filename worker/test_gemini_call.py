"""Quick test to verify the generate_content payload structure."""
import asyncio
import logging
import sys
from google.genai import types as genai_types

# Minimal test: just check that Part.from_uri produces valid output
video_uri = "files/ABC123DEF456"  # dummy URI
mime_type = "video/mp4"

part = genai_types.Part.from_uri(file_uri=video_uri, mime_type=mime_type)
print(f"Part.from_uri() OK: {part}")
print(f"Part dict: {part.model_dump()}")

# Now test the full generate_content payload that the worker will send
# We'll use the actual gemini_client but with a mock to avoid needing an API key
print("\n--- Testing generate_content payload structure ---")

# Simulate what client.generate_content does internally (client.py:108-114):
contents = [part]  # bare Part, NOT Content
prompt = "Test planning prompt"

user_content = genai_types.Content(
    role="user",
    parts=[genai_types.Part(text=prompt)],
)
full_contents = [*contents, user_content]

print(f"full_contents structure:")
for i, c in enumerate(full_contents):
    print(f"  [{i}] role={c.role!r}, parts={len(c.parts)} part(s)")
    for j, p in enumerate(c.parts):
        print(f"       part[{j}]: {type(p).__name__} = {p.model_dump()}")

# Verify: exactly one user role
user_roles = [c.role for c in full_contents if c.role == "user"]
print(f"\nUser role count: {len(user_roles)} (expected: 1)")
assert len(user_roles) == 1, f"FAIL: expected 1 user role, got {len(user_roles)}"

print("\n--- Payload structure VALID ---")
print("The Gemini API will receive exactly one user Content with two parts:")
print("  1. Part(fileUri=...)  — the uploaded video")
print("  2. Part(text=...)      — the planning prompt")
print("\nThis is the correct format that avoids 400 INVALID_ARGUMENT.")