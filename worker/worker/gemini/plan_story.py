"""Smart pipeline, call 2: plan the story of the reel.

Prompt version: plan-story-v3

A text-only Gemini call turns the outline and the numbered sentence index
into a plan: one strong stretch or a montage of beats, each a range of
whole sentences. Every answer is checked and repaired by
`validate_plan`; a rejected answer is retried once with the exact rule
errors as feedback. The verifier's notes can be passed in as feedback to
revise an earlier plan.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from worker.gemini.understand import Understanding, format_sentence_index
from worker.text import plan_rules as pr

logger = logging.getLogger("gemini_plan_story")

MAX_PLAN_ATTEMPTS = 2

PLAN_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": ["single_stretch", "montage"]},
        "reason": {"type": "string"},
        "reorder_reason": {"type": "string"},
        "mood": {"type": "string"},
        "title": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                    "label": {"type": "string"},
                    "join": {"type": "string", "enum": list(pr.JOINS)},
                    "why": {"type": "string"},
                    "role": {"type": "string"},
                    "needs_context": {"type": "boolean"},
                },
                "required": ["from_id", "to_id", "label", "join", "why"],
            },
        },
    },
    "required": ["shape", "reason", "beats"],
}


SINGLE_STRETCH_ONLY = f"""THIS TIME the shape MUST be "single_stretch" with exactly ONE beat: one contiguous stretch of
{pr.SINGLE_MIN_S:.0f}-{pr.SINGLE_MAX_S:.0f} seconds that begins at the start of a thought, makes a complete point on its
own, and ends at a natural stopping point. A montage was already tried and did not work."""


def build_prompt(
    understanding: Understanding,
    sentences: Sequence[Any],
    target_seconds: int,
    feedback: Optional[Sequence[str]] = None,
    force_shape: Optional[str] = None,
) -> str:
    rules = f"""You are a senior editor planning ONE short vertical reel from a longer video.
The reel must make sense to someone who never saw the full video: a viewer should be able to
state its main point after watching it.

You are given an outline of the video and the numbered sentence index. Refer to the video ONLY by
sentence id (S1, S2, ...). Every beat is a range of WHOLE sentences from from_id to to_id.

Choose the shape:
- "single_stretch": use this when ONE contiguous stretch of {pr.SINGLE_MIN_S:.0f}-{pr.SINGLE_MAX_S:.0f} seconds
  already works on its own, with its own setup and payoff. Give exactly one beat.
- "montage": otherwise. A hook beat FIRST, then {pr.MIN_BEATS - 1}-{pr.MAX_BEATS - 2} beats that build the point,
  and optionally a closing beat (role "close"). Beats come from different parts of the video.

Rules:
- Each montage beat is {pr.BEAT_MIN_S:.0f}-{pr.BEAT_MAX_S:.0f} seconds (use the times in the index).
- Total length {pr.TOTAL_MIN_S:.0f}-{pr.TOTAL_MAX_S:.0f} seconds; aim for about {target_seconds} seconds.
- Beats must not overlap. Keep beats in the order of the original video unless a different order is
  clearly better; if you reorder (for example to open with the hook), explain it in reorder_reason.
- A beat must be understandable on its own. If its first sentence depends on earlier speech, set
  needs_context to true, or start a sentence earlier.
- label: at most {pr.LABEL_MAX} characters, shown on screen for montage beats.
- join: how this beat connects to the NEXT one: continuous, topic_change or point_change.
- Start every beat on a sentence that stands on its own. Never start on a filler or a joining word
  ("yeah", "so", "and", "okay", "because") or in the middle of a thought; begin one sentence earlier instead.
- The cuts between beats are plain hard cuts (transitions are added later), so plan the flow of ideas: each
  beat should follow logically from the one before, and no beat may refer to something the viewer was not told.
- The video may be a conversation between people. Prefer passages where one speaker finishes a complete point.
- reason: ONE sentence explaining why the reel makes sense as a whole.
- mood: one word for the music (for example calm, upbeat, serious, inspiring).
- title: a short headline for the reel, at most 50 characters, that makes a viewer want to keep watching.
  No emoji and no quotation marks.
- Write the title and every label in the language of the video (see the outline), in the script the speaker
  uses: Devanagari for Hindi, Latin letters for English or Hinglish.
Output strictly the requested JSON."""



    parts = [rules] + ([SINGLE_STRETCH_ONLY] if force_shape == "single_stretch" else []) + ["OUTLINE:\n" + understanding.outline_text(),
             "SENTENCE INDEX:\n" + format_sentence_index(sentences)]
    if feedback:
        parts.append(
            "YOUR PREVIOUS ANSWER WAS NOT ACCEPTED. Fix these problems and answer again:\n"
            + "\n".join(f"- {f}" for f in feedback)
        )
    return "\n\n".join(parts)


@dataclass
class StoryPlanResult:
    ok: bool
    plan: Optional[Dict[str, Any]] = None           # validated and repaired plan
    rule_result: Optional[pr.RuleResult] = None
    attempts: int = 0
    problems: List[str] = field(default_factory=list)  # one entry per failed attempt


def _default_call(prompt: str, schema: dict) -> str:
    from worker.gemini.client import gemini_client

    return gemini_client.generate_content(contents=[], prompt=prompt, response_schema=schema)


def _parse_json(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        end = text.rfind("```")
        text = text[3:end].strip() if end > 3 else text[3:]
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


async def plan_story(
    understanding: Understanding,
    sentences: Sequence[Any],
    feedback: Optional[Sequence[str]] = None,
    target_seconds: Optional[int] = None,
    call: Optional[Callable[[str, dict], str]] = None,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
    force_shape: Optional[str] = None,
) -> StoryPlanResult:
    """Ask for a plan, check it with the rules, retry once with the errors."""
    if target_seconds is None:
        from worker.config import settings

        target_seconds = settings.REEL_TARGET_SECONDS
    call = call or _default_call

    result = StoryPlanResult(ok=False)
    notes: List[str] = list(feedback or [])
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        prompt = build_prompt(understanding, sentences, target_seconds, notes, force_shape)
        try:
            text = await asyncio.to_thread(call, prompt, PLAN_SCHEMA_DICT)
            data = _parse_json(text)
        except Exception as exc:  # noqa: BLE001
            problem = f"the answer was not valid JSON or the call failed ({type(exc).__name__}: {str(exc)[:120]})"
            result.problems.append(problem)
            notes = list(feedback or []) + [problem]
            logger.warning("Plan attempt %d failed: %s", attempt, problem)
            continue

        checked = pr.validate_plan(data, sentences)
        result.rule_result = checked
        if checked.ok and force_shape and checked.plan["shape"] != force_shape:
            wrong = f"the shape must be {force_shape}, but the answer was {checked.plan['shape']}"
            result.problems.append(wrong)
            notes = list(feedback or []) + [wrong]
            logger.warning("Plan attempt %d rejected: %s", attempt, wrong)
            continue
        if checked.ok:
            result.ok, result.plan = True, checked.plan
            return result
        result.problems.extend(checked.errors)
        notes = list(feedback or []) + checked.errors + checked.repairs
        logger.warning("Plan attempt %d rejected: %s", attempt, checked.errors)
    return result
