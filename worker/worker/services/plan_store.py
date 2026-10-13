"""Read/write helpers for the smart reel columns (migration 005).

Every function takes an optional `client` so tests can pass a fake; by
default the shared worker Supabase client is used.
"""
from typing import Any, Dict, List, Optional, Sequence

VALID_SHAPES = ("single_stretch", "montage")


def _client(client: Any) -> Any:
    if client is not None:
        return client
    from worker.services.supabase import supabase_client
    return supabase_client


def _as_dict(item: Any) -> Dict[str, Any]:
    return item if isinstance(item, dict) else item.to_dict()


def save_sentences(video_id: str, sentences: Sequence[Any], client: Any = None) -> None:
    """Store the sentence index on the video's transcript row.

    Raises RuntimeError if there is no transcript row to attach it to.
    """
    res = _client(client).table("transcripts").update(
        {"sentences": [_as_dict(s) for s in sentences]}
    ).eq("video_id", video_id).execute()
    if not res.data:
        raise RuntimeError(f"No transcript row for video {video_id}; cannot save sentences")


def load_sentences(video_id: str, client: Any = None) -> Optional[List[Dict[str, Any]]]:
    """Return the stored sentence dicts, or None if none were saved."""
    res = _client(client).table("transcripts").select("sentences").eq(
        "video_id", video_id
    ).limit(1).execute()
    if not res.data:
        return None
    return res.data[0].get("sentences")


def save_plan(
    video_id: str,
    plan: Dict[str, Any],
    segments: List[Dict[str, Any]],
    verifier: Optional[Dict[str, Any]] = None,
    client: Any = None,
) -> None:
    """Store the smart plan as the video's single accepted edit plan.

    `segments` are the final snapped cuts as {start_time, end_time, ...}
    (the keys stitch.py and caption.py read), so the existing render
    stage can use this row unchanged. A previous smart plan for the
    same video is replaced; old-style candidate rows (plan is null) are
    left alone.
    """
    shape = plan.get("shape")
    if shape not in VALID_SHAPES:
        raise ValueError(f"plan shape must be one of {VALID_SHAPES}, got {shape!r}")

    for seg in segments:
        try:
            ok = float(seg["end_time"]) > float(seg["start_time"]) >= 0
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            raise ValueError(f"segment must have start_time >= 0 and end_time > start_time, got {seg!r}")

    db = _client(client)
    db.table("edit_plans").delete().eq("video_id", video_id).not_.is_("plan", "null").execute()
    db.table("edit_plans").insert({
        "video_id": video_id,
        "candidate_index": 0,
        "segments": segments,
        "status": "accepted",
        "plan": plan,
        "shape": shape,
        "verifier": verifier,
    }).execute()


def update_verifier(video_id: str, verifier: Dict[str, Any], client: Any = None) -> None:
    """Record the verifier's verdict on the video's smart plan row."""
    _client(client).table("edit_plans").update({"verifier": verifier}).eq(
        "video_id", video_id
    ).not_.is_("plan", "null").execute()


def load_plan(video_id: str, client: Any = None) -> Optional[Dict[str, Any]]:
    """Return the smart plan row (plan, shape, verifier, segments) or None."""
    res = _client(client).table("edit_plans").select(
        "plan, shape, verifier, segments"
    ).eq("video_id", video_id).not_.is_("plan", "null").limit(1).execute()
    return res.data[0] if res.data else None
