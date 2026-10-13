"""Build a safe single-stretch plan from the outline, with no AI call.

Used when the montage cannot be made to pass verification. Candidates
are the outline's key points and the hook candidates; each is grown (or
trimmed) by whole sentences towards the target length, and the one
closest to the target wins.
"""
from typing import Any, Dict, List, Optional, Sequence

from worker.gemini.understand import Understanding
from worker.text import plan_rules as pr


def _fit(sents: List[Dict[str, Any]], i0: int, i1: int, target: float) -> Optional[tuple]:
    """Whole-sentence range around (i0, i1) whose length is closest to `target`."""

    def dur(a: int, b: int) -> float:
        return sents[b]["end"] - sents[a]["start"]

    options: List[tuple] = []

    def note(a: int, b: int) -> None:
        d = dur(a, b)
        if pr.SINGLE_MIN_S <= d <= pr.SINGLE_MAX_S:
            options.append((round(abs(d - target), 3), a, b))

    note(i0, i1)
    a, b = i0, i1  # grow: forward first, then backward
    while dur(a, b) < target:
        if b + 1 < len(sents):
            b += 1
        elif a > 0:
            a -= 1
        else:
            break
        note(a, b)
    a, b = i0, i1  # shrink from the end while too long
    while dur(a, b) > target and b > a:
        b -= 1
        note(a, b)

    if not options:
        return None
    _, a, b = min(options, key=lambda o: o[0])  # earliest option wins ties
    return a, b


def best_single_stretch(
    understanding: Understanding,
    sentences: Sequence[Any],
    target_seconds: float = 38.0,
) -> Optional[Dict[str, Any]]:
    """Return a validated single_stretch plan, or None if the video has no usable stretch."""
    sents = [s if isinstance(s, dict) else s.to_dict() for s in sentences]
    if not sents:
        return None
    index = {s["id"]: i for i, s in enumerate(sents)}

    candidates = []  # (title, i0, i1)
    for kp in understanding.key_points:
        if kp["from_id"] in index and kp["to_id"] in index:
            candidates.append((kp["title"], index[kp["from_id"]], index[kp["to_id"]]))
    for h in understanding.hook_candidates:
        if h["sentence_id"] in index:
            candidates.append(("Best stretch", index[h["sentence_id"]], index[h["sentence_id"]]))

    best = None
    for title, i0, i1 in candidates:
        fitted = _fit(sents, min(i0, i1), max(i0, i1), target_seconds)
        if fitted is None:
            continue
        gap = abs((sents[fitted[1]]["end"] - sents[fitted[0]]["start"]) - target_seconds)
        if best is None or gap < best[0]:  # strict: earlier candidates win ties
            best = (gap, title, fitted)
    if best is None:
        return None

    _, title, (i0, i1) = best
    plan = {
        "shape": "single_stretch",
        "reason": f"Fallback: the most self-contained stretch of the video ({title}).",
        "beats": [{
            "from_id": sents[i0]["id"], "to_id": sents[i1]["id"],
            "label": title[:pr.LABEL_MAX], "join": "continuous", "why": "best single stretch",
        }],
    }
    checked = pr.validate_plan(plan, sents)
    return checked.plan if checked.ok else None
