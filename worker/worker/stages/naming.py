"""The title saved with a finished reel: the planner's hook title when there is one, never an empty string."""
import logging

from worker.services.supabase import supabase_client
from worker.text.plan_rules import clean_title

logger = logging.getLogger("naming")

DEFAULT_TITLE = "AI Generated Reel"


def reel_title(video_id: str) -> str:
    try:
        res = supabase_client.table("edit_plans").select("plan").eq("video_id", video_id).eq(
            "status", "accepted").order("candidate_index").limit(1).execute()
        plan = ((res.data or [{}])[0].get("plan")) or {}
        return clean_title(plan.get("title")) or DEFAULT_TITLE
    except Exception as exc:  # noqa: BLE001 - a missing title must never stop a reel being saved
        logger.info("No plan title for %s (%s)", video_id, exc)
        return DEFAULT_TITLE
