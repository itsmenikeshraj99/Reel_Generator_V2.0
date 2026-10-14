"""Choose the reel: plan, snap, draft, verify, revise, fall back.

The control loop of the smart pipeline. All the heavy parts (planner,
draft renderer, verifier) are passed in as functions, so the loop itself
is plain, testable logic:

1. Ask the planner for a plan (rules-checked) and snap it to exact cuts.
2. Render a draft and have the verifier watch it.
3. pass -> done. revise -> re-plan with the verifier's fixes, up to
   `max_revisions` times. fail, or out of revisions -> use the best
   single stretch built from the outline (no AI), verified once more.
4. If nothing passes, ship the best available plan flagged needs_review.
   If the verifier itself is unavailable, ship the plan unverified, also
   flagged needs_review.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from worker.edit.fallback import best_single_stretch
from worker.gemini.understand import Understanding
from worker.gemini.verify import VerifyResult
from worker.text import plan_rules as pr
from worker.text.snap import SnapResult, snap_plan

logger = logging.getLogger("select_reel")

PlanFn = Callable[..., Awaitable[Any]]                # plan_story-like: (understanding, sentences, feedback=...) -> StoryPlanResult
DraftFn = Callable[[List[Dict[str, Any]]], Awaitable[str]]   # segments -> draft file path
VerifyFn = Callable[[str, Dict[str, Any]], Awaitable[VerifyResult]]  # (draft path, plan) -> verdict


@dataclass
class Selection:
    ok: bool
    plan: Optional[Dict[str, Any]] = None
    segments: List[Dict[str, Any]] = field(default_factory=list)   # {start_time, end_time, title, reason}
    verifier: Optional[Dict[str, Any]] = None
    needs_review: bool = False
    used_fallback: bool = False
    mode: str = "montage"  # montage | ai_stretch | code_stretch
    revisions: int = 0
    total_seconds: float = 0.0
    history: List[str] = field(default_factory=list)
    # One record per attempt (plan summary, verdict and fixes, problems), for reels.meta.
    attempts: List[Dict[str, Any]] = field(default_factory=list)


def describe_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Compact, readable summary of a plan for logs and reels.meta."""
    beats = plan.get("beats", [])
    return {
        "shape": plan.get("shape"),
        "reason": str(plan.get("reason", ""))[:200],
        "total": round(sum(float(b.get("duration", 0) or 0) for b in beats), 1),
        "beats": [f"{b.get('role', '')} {b.get('from_id')}-{b.get('to_id')} {float(b.get('duration', 0) or 0):.0f}s {b.get('label', '')}".strip()
                  for b in beats],
    }


def _remove(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


async def select_reel(
    *,
    understanding: Understanding,
    sentences: Sequence[Any],
    words: Sequence[Any],
    silences: Sequence[Any],
    media_duration: Optional[float],
    plan_fn: PlanFn,
    make_draft: DraftFn,
    verify: VerifyFn,
    max_revisions: int = 2,
    target_seconds: float = 38.0,
) -> Selection:
    out = Selection(ok=False)
    feedback: Optional[List[str]] = None
    last_candidate: Optional[tuple] = None  # (plan, snapped, verifier dict) of the latest verified montage

    async def check(plan: Dict[str, Any], snapped: SnapResult) -> Optional[VerifyResult]:
        """Draft + verify one snapped plan. None means the verifier was unavailable."""
        draft = None
        try:
            draft = await make_draft(snapped.segments)
            return await verify(draft, plan)
        except Exception as exc:  # noqa: BLE001
            out.history.append(f"verification unavailable: {type(exc).__name__}: {str(exc)[:120]}")
            logger.warning("Verification unavailable: %s", exc)
            return None
        finally:
            _remove(draft)

    def finish(plan, snapped, verdict: Optional[VerifyResult], *, fallback: bool, review: bool,
               mode: str = "montage") -> Selection:
        out.mode = mode
        out.ok, out.plan, out.segments = True, plan, snapped.segments
        out.verifier = verdict.to_dict() if verdict else None
        out.used_fallback, out.needs_review = fallback, review
        out.total_seconds = snapped.total_seconds
        return out

    for attempt in range(max_revisions + 1):
        out.revisions = attempt
        rec: Dict[str, Any] = {"attempt": attempt + 1}
        if feedback:
            rec["feedback_given"] = list(feedback)[:5]
        out.attempts.append(rec)
        try:
            story = await plan_fn(understanding, sentences, feedback=feedback)
        except Exception as exc:  # noqa: BLE001
            rec["planner_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            out.history.append(f"attempt {attempt + 1}: planner crashed ({type(exc).__name__}: {str(exc)[:120]})")
            break
        if not story.ok:
            rec["planner_problems"] = list(story.problems)[:3]
            out.history.append(f"attempt {attempt + 1}: planner gave no valid plan ({'; '.join(story.problems)[:200]})")
            break
        rec["plan"] = describe_plan(story.plan)
        repairs = getattr(getattr(story, "rule_result", None), "repairs", None)
        if repairs:
            rec["rule_repairs"] = list(repairs)[:5]
        snapped = snap_plan(story.plan, sentences, words, silences, media_duration)
        if not snapped.ok:
            rec["snap_errors"] = list(snapped.errors)[:3]
            out.history.append(f"attempt {attempt + 1}: snapping failed ({'; '.join(snapped.errors)})")
            feedback = list(snapped.errors)
            continue

        verdict = await check(story.plan, snapped)
        if verdict is None:  # cannot verify: ship unverified, flagged
            rec["verifier_unavailable"] = True
            return finish(story.plan, snapped, None, fallback=False, review=True)
        rec["verdict"] = verdict.to_dict()
        logger.info("Attempt %d verdict %s; fixes: %s", attempt + 1, verdict.verdict, verdict.fixes)
        out.history.append(f"attempt {attempt + 1}: verdict {verdict.verdict}")
        last_candidate = (story.plan, snapped, verdict)
        if verdict.verdict == "pass":
            return finish(story.plan, snapped, verdict, fallback=False, review=False)
        if verdict.verdict == "fail":
            break
        feedback = list(verdict.fixes)

    # Second chance: ask the planner for ONE coherent stretch (only if a montage was actually judged).
    ai_candidate: Optional[tuple] = None
    if last_candidate is not None:
        montage_fixes = list(last_candidate[2].fixes)
        rec: Dict[str, Any] = {"attempt": "ai_stretch"}
        out.attempts.append(rec)
        try:
            story = await plan_fn(
                understanding, sentences,
                feedback=["A montage was tried and did not pass."] + montage_fixes,
                force_shape="single_stretch",
            )
        except Exception as exc:  # noqa: BLE001
            story = None
            rec["planner_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        if story is not None and not story.ok:
            rec["planner_problems"] = list(story.problems)[:3]
        if story is not None and story.ok:
            rec["plan"] = describe_plan(story.plan)
            ai_snapped = snap_plan(story.plan, sentences, words, silences, media_duration)
            if not ai_snapped.ok:
                rec["snap_errors"] = list(ai_snapped.errors)[:3]
            else:
                verdict = await check(story.plan, ai_snapped)
                if verdict is None:
                    rec["verifier_unavailable"] = True
                    out.history.append("ai single stretch: verifier unavailable, shipped flagged")
                    return finish(story.plan, ai_snapped, None, fallback=True, review=True, mode="ai_stretch")
                rec["verdict"] = verdict.to_dict()
                out.history.append(f"ai single stretch: verdict {verdict.verdict}")
                if verdict.verdict == "pass":
                    return finish(story.plan, ai_snapped, verdict, fallback=True, review=False, mode="ai_stretch")
                ai_candidate = (story.plan, ai_snapped, verdict)

    # Last resort: the best single stretch picked by code, verified once more.
    fb_plan = best_single_stretch(understanding, sentences, target_seconds)
    fb_snapped = snap_plan(fb_plan, sentences, words, silences, media_duration) if fb_plan else None
    if fb_plan and fb_snapped and fb_snapped.ok:
        verdict = await check(fb_plan, fb_snapped)
        out.attempts.append({
            "attempt": "fallback", "plan": describe_plan(fb_plan),
            **({"verdict": verdict.to_dict()} if verdict else {"verifier_unavailable": True}),
        })
        passed = verdict is not None and verdict.verdict == "pass"
        out.history.append("fallback single stretch: " + ("pass" if passed else "not passed, shipped flagged"))
        if not passed and ai_candidate:  # an AI-chosen stretch beats a code-chosen one that also failed
            plan, snapped, ai_verdict = ai_candidate
            return finish(plan, snapped, ai_verdict, fallback=True, review=True, mode="ai_stretch")
        return finish(fb_plan, fb_snapped, verdict, fallback=True, review=not passed, mode="code_stretch")

    out.history.append("fallback single stretch could not be built")
    if ai_candidate:
        plan, snapped, verdict = ai_candidate
        return finish(plan, snapped, verdict, fallback=True, review=True, mode="ai_stretch")
    if last_candidate:
        plan, snapped, verdict = last_candidate
        return finish(plan, snapped, verdict, fallback=False, review=True)
    return out
