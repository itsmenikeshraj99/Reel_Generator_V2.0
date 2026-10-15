"""Check and repair a reel plan against the editing rules (pure Python).

The planner (an AI call) proposes a plan; this is the safety net. One bad
beat is repaired or dropped without discarding the whole plan; a plan that
cannot be repaired is rejected with reasons the planner can act on.

Plan shape (the planner's output):
    {
      "shape": "single_stretch" | "montage",
      "reason": "one sentence on why the reel makes sense",
      "reorder_reason": "optional; needed if beats are not in source order",
      "mood": "optional, passed through",
      "beats": [{"from_id": "S3", "to_id": "S5", "label": "...",
                 "join": "continuous|topic_change|point_change",
                 "why": "...", "role": "optional: close", "needs_context": false}]
    }
Beats refer to sentence ids from the sentence index, never to seconds.
"""
import copy
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

SHAPES = ("single_stretch", "montage")
JOINS = ("continuous", "topic_change", "point_change")

BEAT_MIN_S = 3.0
BEAT_MAX_S = 20.0
SINGLE_MIN_S = 20.0
SINGLE_MAX_S = 45.0
TOTAL_MIN_S = 20.0
TOTAL_MAX_S = 60.0
TARGET_MIN_S = 30.0
TARGET_MAX_S = 45.0
MIN_BEATS = 3   # montage: hook + at least 2 more
MAX_BEATS = 8   # montage: hook + up to 6 + optional close
LABEL_MAX = 24
TITLE_MAX = 50


@dataclass
class RuleResult:
    ok: bool
    plan: Optional[Dict[str, Any]] = None       # repaired plan, only when ok
    errors: List[str] = field(default_factory=list)    # why it was rejected
    repairs: List[str] = field(default_factory=list)   # what was changed or dropped
    warnings: List[str] = field(default_factory=list)  # accepted, but not ideal
    total_seconds: float = 0.0


def has_devanagari(text: Any) -> bool:
    return isinstance(text, str) and any("\u0900" <= ch <= "\u097f" for ch in text)


def dominant_script(sentences: Sequence[Any]) -> str:
    """'dev' if most of the spoken text is Devanagari, else 'lat'."""
    dev = other = 0
    for s in sentences:
        text = (s if isinstance(s, dict) else s.to_dict()).get("text", "")
        for ch in text:
            if ch.isalpha():
                if "\u0900" <= ch <= "\u097f":
                    dev += 1
                else:
                    other += 1
    return "dev" if dev > 0.3 * (dev + other) and dev > 0 else "lat"


def clean_title(value: Any, limit: int = TITLE_MAX) -> str:
    """A safe on-screen headline: letters, marks, numbers and punctuation only (no emoji, symbols or
    subtitle control characters), one line, at most `limit` characters, cut at a word boundary."""
    if not isinstance(value, str):
        return ""
    keep = []
    for ch in value:
        if ch in "\u200c\u200d" or unicodedata.category(ch)[0] in "LMNPZ":  # ZWJ/ZWNJ shape Devanagari
            keep.append(ch)
        else:
            keep.append(" ")
    text = re.sub(r"[{}\\]", " ", "".join(keep))
    text = re.sub(r"\s+", " ", text).strip().strip("\"'\u201c\u201d\u2018\u2019").strip()
    if len(text) > limit:
        cut = text[:limit]
        if text[limit] != " " and " " in cut:
            cut = cut[:cut.rfind(" ")]
        text = cut.rstrip(" ,;:-")
    return text


def _as_dict(item: Any) -> Dict[str, Any]:
    return item if isinstance(item, dict) else item.to_dict()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_plan(plan: Any, sentences: Sequence[Any]) -> RuleResult:
    """Return a RuleResult; never raises on malformed planner output."""
    repairs: List[str] = []
    warnings: List[str] = []

    def reject(msg: str) -> RuleResult:
        return RuleResult(False, None, [msg], repairs, warnings, 0.0)

    if not isinstance(plan, dict):
        return reject("plan is not an object")
    shape = plan.get("shape")
    if shape not in SHAPES:
        return reject(f"shape must be one of {SHAPES}, got {shape!r}")
    if not _text(plan.get("reason")):
        return reject("plan has no reason (one sentence on why the reel makes sense)")
    raw_beats = plan.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats:
        return reject("plan has no beats")

    sents = [_as_dict(s) for s in sentences]
    index = {s["id"]: i for i, s in enumerate(sents)}
    single = shape == "single_stretch"
    max_beat = SINGLE_MAX_S if single else BEAT_MAX_S

    def span(i0: int, i1: int) -> float:
        return sents[i1]["end"] - sents[i0]["start"]

    kept: List[Dict[str, Any]] = []
    used: set = set()
    first_beat_problem: Optional[str] = None

    for n, raw in enumerate(raw_beats, start=1):
        def drop(why: str) -> None:
            nonlocal first_beat_problem
            repairs.append(f"beat {n} dropped: {why}")
            if n == 1:
                first_beat_problem = why

        if not isinstance(raw, dict):
            drop("not an object")
            continue
        if single and kept:
            repairs.append(f"beat {n} dropped: single_stretch keeps one beat")
            continue
        a, b = raw.get("from_id"), raw.get("to_id")
        if a not in index or b not in index:
            drop(f"unknown sentence id ({a!r} to {b!r})")
            continue
        i0, i1 = index[a], index[b]
        if i0 > i1:
            i0, i1 = i1, i0
            repairs.append(f"beat {n}: from_id/to_id were reversed, swapped")

        if raw.get("needs_context") is True and i0 > 0 and (i0 - 1) not in used:
            i0 -= 1
            repairs.append(f"beat {n}: needs context, started one sentence earlier")

        while i1 > i0 and span(i0, i1) > max_beat:
            i1 -= 1
            repairs.append(f"beat {n}: too long, trimmed to end at {sents[i1]['id']}")

        duration = span(i0, i1)
        if not single and duration < BEAT_MIN_S:
            drop(f"too short ({duration:.1f}s, minimum {BEAT_MIN_S:.0f}s)")
            continue
        if duration > max_beat:
            drop(f"a single sentence is longer than {max_beat:.0f}s")
            continue
        if any(i in used for i in range(i0, i1 + 1)):
            drop("overlaps an earlier beat")
            continue

        label = _text(raw.get("label"))
        if len(label) > LABEL_MAX:
            label = label[:LABEL_MAX].rstrip()
            repairs.append(f"beat {n}: label shortened to {LABEL_MAX} characters")
        join = raw.get("join")
        if join not in JOINS:
            join = "continuous" if single else "topic_change"
            repairs.append(f"beat {n}: invalid join replaced with {join}")

        used.update(range(i0, i1 + 1))
        kept.append({
            "i0": i0, "i1": i1, "label": label, "join": join,
            "why": _text(raw.get("why")),
            "is_close": _text(raw.get("role")) == "close",
        })

    if not kept:
        return reject("no usable beats: " + "; ".join(repairs))
    if not single and first_beat_problem is not None:
        return reject(f"the hook (first beat) was unusable: {first_beat_problem}")

    def total() -> float:
        return sum(span(k["i0"], k["i1"]) for k in kept)

    def drop_last_removable() -> bool:
        for pos in range(len(kept) - 1, 0, -1):
            if not kept[pos]["is_close"]:
                gone = kept.pop(pos)
                repairs.append(
                    f"beat at {sents[gone['i0']]['id']}-{sents[gone['i1']]['id']} dropped to fit the limits")
                return True
        return False

    if not single:
        while len(kept) > MAX_BEATS and drop_last_removable():
            pass
        if len(kept) < MIN_BEATS:
            return reject(f"montage needs at least {MIN_BEATS} usable beats, got {len(kept)}")
        while total() > TOTAL_MAX_S and len(kept) > MIN_BEATS and drop_last_removable():
            pass

    seconds = total()
    if seconds > TOTAL_MAX_S:
        return reject(f"total length {seconds:.1f}s exceeds {TOTAL_MAX_S:.0f}s")
    min_total = SINGLE_MIN_S if single else TOTAL_MIN_S
    if seconds < min_total:
        return reject(f"total length {seconds:.1f}s is under the {min_total:.0f}s minimum")
    if not (TARGET_MIN_S <= seconds <= TARGET_MAX_S):
        warnings.append(f"total length {seconds:.1f}s is outside the {TARGET_MIN_S:.0f}-{TARGET_MAX_S:.0f}s target")

    starts = [k["i0"] for k in kept]
    if starts != sorted(starts) and not _text(plan.get("reorder_reason")):
        return reject("beats are not in source order and the plan gives no reorder_reason")

    beats_out = []
    for pos, k in enumerate(kept):
        last = pos == len(kept) - 1
        if single:
            role = "stretch"
        elif pos == 0:
            role = "hook"
        elif last and k["is_close"]:
            role = "close"
        else:
            role = "point"
        join = "continuous" if (single or last) else k["join"]
        beats_out.append({
            "from_id": sents[k["i0"]]["id"],
            "to_id": sents[k["i1"]]["id"],
            "label": k["label"],
            "join": join,
            "why": k["why"],
            "role": role,
            "start": round(sents[k["i0"]]["start"], 3),
            "end": round(sents[k["i1"]]["end"], 3),
            "duration": round(span(k["i0"], k["i1"]), 3),
        })

    out = {key: copy.deepcopy(val) for key, val in plan.items() if key != "beats"}
    out["reason"] = _text(plan.get("reason"))
    out["beats"] = beats_out
    title = clean_title(plan.get("title"))
    if not title:
        title = clean_title(beats_out[0]["label"])
        if title:
            warnings.append("no title given; the first beat's label is used")
    out["title"] = title
    return RuleResult(True, out, [], repairs, warnings, round(seconds, 3))
