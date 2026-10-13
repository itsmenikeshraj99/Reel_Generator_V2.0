"""Smart pipeline, call 1: understand the video.

Prompt version: understand-v1

Gemini watches the video and reads the numbered sentence index, then
returns an outline: the language, a one-line summary, the key points,
ranked hook candidates and notable visual moments. It refers to sentence
ids only, never to seconds, so later cuts always land on whole sentences.
Each list item is validated on its own, so one bad item never discards
the rest.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger("gemini_understand")

MAX_KEY_POINTS = 12
MAX_HOOKS = 8
MAX_VISUAL = 8
HOOK_TYPES = ("question", "bold_claim", "surprising_fact", "story_opening", "emotional", "other")

UNDERSTAND_PROMPT = """You are a senior video editor preparing to cut a short reel from a longer video.
You are given the video and a numbered sentence index of everything said in it.
Each line looks like: S12 [01:05.2-01:09.8] the sentence text

Refer to the video ONLY by sentence id (S1, S2, ...). Never use timestamps in your answer.

Return:
- language: the main spoken language (e.g. "English", "Hindi", "Hinglish").
- summary: ONE sentence saying what the video is about.
- key_points: the 3 to 10 most important or most interesting points. Each has a short title
  and a contiguous range of sentences from_id to to_id that fully covers that point.
  A point must make sense on its own and must not start mid-thought.
- hook_candidates: up to 5 single sentences that would grab a viewer in the first seconds,
  best first. Each has sentence_id, type (question, bold_claim, surprising_fact, story_opening,
  emotional or other) and a short reason.
- visual_moments: for parts that are mostly visual rather than spoken, up to 5 entries with
  near_id (the closest sentence id) and a short description. Use an empty list if the video is
  mostly speech.

Use only sentence ids that appear in the index. Output strictly the requested JSON."""

UNDERSTAND_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                },
                "required": ["title", "from_id", "to_id"],
            },
        },
        "hook_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence_id": {"type": "string"},
                    "type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["sentence_id", "type", "reason"],
            },
        },
        "visual_moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "near_id": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["near_id", "description"],
            },
        },
    },
    "required": ["language", "summary", "key_points", "hook_candidates", "visual_moments"],
}


class UnderstandingRaw(BaseModel):
    """Loose container for the model's answer; items are checked one by one later."""
    language: str = ""
    summary: str = ""
    key_points: List[Any] = Field(default_factory=list)
    hook_candidates: List[Any] = Field(default_factory=list)
    visual_moments: List[Any] = Field(default_factory=list)


class UnderstandingError(Exception):
    """The understanding call produced nothing usable."""


@dataclass
class Understanding:
    language: str
    summary: str
    key_points: List[Dict[str, str]] = field(default_factory=list)       # {title, from_id, to_id}
    hook_candidates: List[Dict[str, str]] = field(default_factory=list)  # {sentence_id, type, reason}
    visual_moments: List[Dict[str, str]] = field(default_factory=list)   # {near_id, description}
    dropped: List[str] = field(default_factory=list)                     # what was discarded and why

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language, "summary": self.summary,
            "key_points": self.key_points, "hook_candidates": self.hook_candidates,
            "visual_moments": self.visual_moments,
        }

    def outline_text(self) -> str:
        """Plain-text outline handed to the story planner."""
        lines = [f"Language: {self.language}", f"Summary: {self.summary}", "Key points:"]
        lines += [f"- {k['title']} ({k['from_id']}-{k['to_id']})" for k in self.key_points]
        lines.append("Hook candidates (best first):")
        lines += [f"- {h['sentence_id']} [{h['type']}] {h['reason']}" for h in self.hook_candidates]
        if self.visual_moments:
            lines.append("Visual moments:")
            lines += [f"- near {v['near_id']}: {v['description']}" for v in self.visual_moments]
        return "\n".join(lines)


def _as_dict(item: Any) -> Dict[str, Any]:
    return item if isinstance(item, dict) else item.to_dict()


def _clock(seconds: float) -> str:
    m, s = divmod(max(0.0, float(seconds)), 60)
    return f"{int(m):02d}:{s:04.1f}"


def format_sentence_index(sentences: Sequence[Any]) -> str:
    """One line per sentence: 'S12 [01:05.2-01:09.8] text'."""
    lines = []
    for s in map(_as_dict, sentences):
        lines.append(f"{s['id']} [{_clock(s['start'])}-{_clock(s['end'])}] {s['text']}")
    return "\n".join(lines)


def _str(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_understanding(data: Any, sentences: Sequence[Any]) -> Understanding:
    """Validate the model's answer item by item against the sentence index."""
    if not isinstance(data, dict):
        raise UnderstandingError("answer is not an object")
    order = {s["id"]: i for i, s in enumerate(map(_as_dict, sentences))}
    dropped: List[str] = []

    key_points, seen = [], set()
    for n, kp in enumerate(data.get("key_points") or [], start=1):
        if not isinstance(kp, dict):
            dropped.append(f"key point {n}: not an object")
            continue
        a, b = kp.get("from_id"), kp.get("to_id")
        if a not in order or b not in order:
            dropped.append(f"key point {n}: unknown sentence id ({a!r}-{b!r})")
            continue
        if order[a] > order[b]:
            a, b = b, a
        if (a, b) in seen:
            dropped.append(f"key point {n}: duplicate range {a}-{b}")
            continue
        seen.add((a, b))
        key_points.append({"title": _str(kp.get("title"), 80) or f"{a}-{b}", "from_id": a, "to_id": b})
    if len(key_points) > MAX_KEY_POINTS:
        dropped.append(f"{len(key_points) - MAX_KEY_POINTS} extra key points ignored")
        key_points = key_points[:MAX_KEY_POINTS]

    hooks = []
    for n, h in enumerate(data.get("hook_candidates") or [], start=1):
        sid = h.get("sentence_id") if isinstance(h, dict) else None
        if sid not in order:
            dropped.append(f"hook {n}: unknown sentence id ({sid!r})")
            continue
        htype = _str(h.get("type"), 40).lower()
        hooks.append({
            "sentence_id": sid,
            "type": htype if htype in HOOK_TYPES else "other",
            "reason": _str(h.get("reason"), 300),
        })
    hooks = hooks[:MAX_HOOKS]

    visual = []
    for n, v in enumerate(data.get("visual_moments") or [], start=1):
        near = v.get("near_id") if isinstance(v, dict) else None
        if near not in order:
            dropped.append(f"visual moment {n}: unknown sentence id ({near!r})")
            continue
        visual.append({"near_id": near, "description": _str(v.get("description"), 300)})
    visual = visual[:MAX_VISUAL]

    if not key_points:
        raise UnderstandingError("no usable key points: " + "; ".join(dropped))
    return Understanding(
        language=_str(data.get("language"), 40),
        summary=_str(data.get("summary"), 400),
        key_points=key_points,
        hook_candidates=hooks,
        visual_moments=visual,
        dropped=dropped,
    )


async def understand(
    video_file_uri: str,
    sentences: Sequence[Any],
    video_id: str = "understand",
) -> Understanding:
    """Run the understanding call on an already-uploaded Gemini video file."""
    from worker.stages import transcribe_plan  # lazy: avoids a circular import with pipeline

    try:
        raw = await transcribe_plan._gemini_call_with_fallback(
            video_id=video_id,
            video_file_uri=video_file_uri,
            prompt=UNDERSTAND_PROMPT,
            response_schema=UNDERSTAND_SCHEMA_DICT,
            response_model=UnderstandingRaw,
            context_text=format_sentence_index(sentences),
        )
        result = parse_understanding(raw.model_dump(), sentences)
    except UnderstandingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UnderstandingError(f"understanding call failed: {exc}") from exc
    if result.dropped:
        logger.warning("Understanding for %s dropped items: %s", video_id, result.dropped)
    return result
