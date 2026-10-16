"""Smart pipeline, call 3: watch the draft and judge it.

Prompt version: verify-v3

Gemini watches a small draft of the reel together with the plan's reason
and, for every beat, the sentence just before and just after it in the
original video. It says whether the reel states a clear main point,
whether the hook works, which cuts are abrupt, and whether the clips
together keep the speaker's meaning. Code then enforces the hard rules
on top of the model's verdict.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger("gemini_verify")

VERDICTS = ("pass", "revise", "fail")
MAX_FIXES = 4
MAX_TOLERATED_CUTS = 1  # one confusing beat still passes (it is noted); two or more send the plan back
# Suggestions the planner cannot act on (it only chooses sentences, order and length).
NON_ACTIONABLE = (
    "voiceover", "voice-over", "voice over", "transition", "audio level", "volume", "music", "fade",
    "text overlay", "on-screen text", "caption", "pause", "smooth", "soften", "sound effect",
)

VERIFY_PROMPT = """You are a strict editor watching a DRAFT of a short reel assembled from a longer video.
The draft is low resolution and has no music. Where the plan marks a topic or point change it has the same
short crossfade as the final reel; the other joins are plain HARD CUTS. Either way, do NOT judge how rough a
cut sounds or looks. Judge only whether the CONTENT works for a viewer who has never seen the full video.

You also get the plan's stated reason, and for each beat the sentence that came just before and just
after it in the ORIGINAL video, so you can tell whether a clip changes the speaker's meaning.

Answer:
- main_point_stated: in ONE sentence, what is the main point of this reel, in your own words?
  If you cannot tell, say "unclear".
- hook_ok: false if the reel starts mid-sentence, mid-thought or on a filler ("yeah", "so", "and",
  "okay"), or if the first seconds would not make a viewer keep watching. Otherwise true.
- abrupt_cuts: the numbers of beats that are CONFUSING to follow after the previous beat: the beat starts
  mid-thought, refers to something the viewer was never told, or its logic does not follow. Do NOT flag a
  beat just because the picture or sound jumps. Use an empty list when every beat makes sense.
- meaning_preserved: do the clips together keep what the speaker actually meant? Answer false if leaving
  out or reordering parts makes the speaker seem to say something they did not mean.
- verdict: "pass" = ready to publish; "revise" = fixable by choosing different sentences, order or length;
  "fail" = fundamentally does not work.
- fixes: up to 4 short, concrete changes the planner can make ONLY by choosing different sentences, a
  different order, or a different length (for example: "Start beat 2 at the sentence that introduces the
  study"). Do NOT suggest voiceover, text, transitions, music, audio levels, pauses or any editing effect:
  the planner cannot do those.

Output strictly the requested JSON."""

VERIFY_SCHEMA_DICT: dict = {
    "type": "object",
    "properties": {
        "main_point_stated": {"type": "string"},
        "hook_ok": {"type": "boolean"},
        "abrupt_cuts": {"type": "array", "items": {"type": "integer"}},
        "meaning_preserved": {"type": "boolean"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "fixes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["main_point_stated", "hook_ok", "abrupt_cuts", "meaning_preserved", "verdict", "fixes"],
}


class VerifyRaw(BaseModel):
    main_point_stated: str = ""
    hook_ok: Any = False
    abrupt_cuts: List[Any] = Field(default_factory=list)
    meaning_preserved: Any = True
    verdict: str = "revise"
    fixes: List[Any] = Field(default_factory=list)


class VerifyError(Exception):
    """The verifier could not produce a usable verdict."""


@dataclass
class VerifyResult:
    main_point_stated: str
    hook_ok: bool
    abrupt_cuts: List[int]          # 1-based beat numbers
    meaning_preserved: bool
    verdict: str                    # pass | revise | fail
    fixes: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # adjustments made by code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "main_point_stated": self.main_point_stated, "hook_ok": self.hook_ok,
            "abrupt_cuts": self.abrupt_cuts, "meaning_preserved": self.meaning_preserved,
            "verdict": self.verdict, "fixes": self.fixes, "notes": self.notes,
        }


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return default


def parse_verdict(data: Any, n_beats: int, shape: str) -> VerifyResult:
    """Validate the model's answer and enforce the hard rules in code."""
    if not isinstance(data, dict):
        raise VerifyError("verifier answer is not an object")
    notes: List[str] = []

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        notes.append(f"unknown verdict {data.get('verdict')!r} treated as revise")
        verdict = "revise"
    hook_ok = _bool(data.get("hook_ok"), False)
    meaning = _bool(data.get("meaning_preserved"), True)
    point = data.get("main_point_stated")
    point = point.strip()[:300] if isinstance(point, str) else ""

    cuts: List[int] = []
    for c in data.get("abrupt_cuts") or []:
        if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= n_beats and c not in cuts:
            cuts.append(c)
    cuts.sort()
    if shape == "single_stretch":
        cuts = []  # one continuous stretch has no cuts

    raw_fixes = [f.strip()[:200] for f in (data.get("fixes") or []) if isinstance(f, str) and f.strip()]
    fixes = [f for f in raw_fixes if not any(word in f.lower() for word in NON_ACTIONABLE)][:MAX_FIXES]
    if len(raw_fixes) > len(fixes) and len(raw_fixes) <= MAX_FIXES + len(fixes):
        notes.append(f"ignored {len(raw_fixes) - len(fixes)} suggestion(s) the planner cannot act on")
    point_ok = bool(point) and point.lower() != "unclear"
    cuts_ok = len(cuts) <= MAX_TOLERATED_CUTS

    if shape == "montage" and not meaning and verdict != "fail":
        notes.append("meaning not preserved: hard fail for a montage")
        verdict = "fail"
    if verdict == "pass" and not (hook_ok and cuts_ok and point_ok):
        notes.append("verdict pass downgraded to revise: hook, cuts or main point not satisfied")
        verdict = "revise"
    elif verdict == "revise" and not fixes and hook_ok and meaning and point_ok and cuts_ok:
        notes.append("verdict revise upgraded to pass: nothing the planner could change")
        verdict = "pass"
    if verdict == "pass" and cuts:
        notes.append("passed with one beat flagged as confusing: " + ", ".join(map(str, cuts)))
    if verdict == "revise" and not fixes:
        if not hook_ok:
            fixes.append("Start the reel on a complete, self-contained sentence that grabs attention.")
        if not cuts_ok:
            fixes.append("Choose different sentences so beat " + ", ".join(map(str, cuts))
                         + " follows logically from the one before it.")
        if not point_ok:
            fixes.append("Make the main point clearer.")
        if not fixes:
            fixes.append("Choose different sentences so the reel stands on its own.")

    return VerifyResult(point, hook_ok, cuts, meaning, verdict, fixes, notes)


def build_context(plan: Dict[str, Any], sentences: Sequence[Any]) -> str:
    """The plan's reason plus one sentence of original context around each beat."""
    sents = [s if isinstance(s, dict) else s.to_dict() for s in sentences]
    index = {s["id"]: i for i, s in enumerate(sents)}
    lines = [f"Plan reason: {plan.get('reason', '')}", f"Shape: {plan.get('shape', '')}", ""]
    for n, beat in enumerate(plan.get("beats", []), start=1):
        i0, i1 = index.get(beat.get("from_id")), index.get(beat.get("to_id"))
        label = beat.get("label") or ""
        lines.append(f"Beat {n} ({beat.get('role', '')}{': ' + label if label else ''}): "
                     f"{beat.get('from_id')} to {beat.get('to_id')}")
        if i0 is not None and i0 > 0:
            lines.append(f"  just before in the original: \"{sents[i0 - 1]['text']}\"")
        if i1 is not None and i1 + 1 < len(sents):
            lines.append(f"  just after in the original: \"{sents[i1 + 1]['text']}\"")
    return "\n".join(lines)


async def verify_draft(draft_path: str, plan: Dict[str, Any], sentences: Sequence[Any]) -> VerifyResult:
    """Upload the draft, ask Gemini for a verdict, delete the upload."""
    from worker.gemini.client import gemini_client
    from worker.stages import transcribe_plan  # lazy: avoids a circular import with pipeline

    uploaded = None
    try:
        uploaded = await asyncio.to_thread(gemini_client.upload_video, draft_path)
        uploaded = await gemini_client.wait_for_processing(uploaded)
        uri = uploaded.uri
        if not uri.startswith(("https:", "http:")):
            uri = f"https://generativelanguage.googleapis.com/{uri}"
        raw = await transcribe_plan._gemini_call_with_fallback(
            video_id="verify",
            video_file_uri=uri,
            prompt=VERIFY_PROMPT,
            response_schema=VERIFY_SCHEMA_DICT,
            response_model=VerifyRaw,
            context_text=build_context(plan, sentences),
        )
        return parse_verdict(raw.model_dump(), len(plan.get("beats", [])), plan.get("shape", "montage"))
    except VerifyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VerifyError(f"verification call failed: {exc}") from exc
    finally:
        if uploaded is not None and getattr(uploaded, "name", None):
            try:
                gemini_client.client.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001
                pass
