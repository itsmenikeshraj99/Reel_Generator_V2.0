"""Optional background music for a finished reel.

Runs after captions and before the size check. It only does anything when the user ticked "add music"
for this video (`videos.add_music`), the kill switch `MUSIC_ENABLED` is on, and the music library has a
suitable track. In every other case, and on any error, the reel is left exactly as it was: music must
never cost a user their reel.
"""
import asyncio
import logging
import os
from typing import Any, Dict, Optional

from worker.config import settings
from worker.edit import mix, music, transitions
from worker.services.supabase import supabase_client

logger = logging.getLogger("music_stage")


def user_wants_music(video_id: str) -> bool:
    """The upload-time checkbox. Missing column (migration 006 not applied) or any DB trouble means no."""
    try:
        res = supabase_client.table("videos").select("add_music").eq("id", video_id).single().execute()
        return bool((res.data or {}).get("add_music"))
    except Exception as exc:  # noqa: BLE001
        logger.info("add_music not readable for %s (%s); no music", video_id, exc)
        return False


def _accepted_plan(video_id: str) -> Dict[str, Any]:
    res = supabase_client.table("edit_plans").select("*").eq("video_id", video_id).eq(
        "status", "accepted").order("candidate_index").limit(1).execute()
    return (res.data or [{}])[0]


async def add_music(pipeline, video_id: str, reel_path: str) -> bool:
    """Mix music into `reel_path` in place. Returns True only if the file now has music."""
    extra = getattr(pipeline, "reel_meta_extra", None)

    def note(info: Dict[str, Any]) -> None:
        if extra is not None:
            extra["music"] = info

    if not settings.MUSIC_ENABLED or not user_wants_music(video_id):
        return False
    out_path = reel_path + ".music.mp4"
    try:
        plan_row = _accepted_plan(video_id)
        plan = plan_row.get("plan") or {}
        segments = plan_row.get("segments") or []
        mood = plan.get("mood")
        durations = transitions.segment_durations(segments)
        overlaps = list((getattr(pipeline, "transition_info", None) or {}).get("overlaps", []))
        reel_s = mix.probe_duration(reel_path)

        if music.wants_no_music(mood):
            note({"requested": True, "applied": False, "reason": "the planner judged this reel better without music",
                  "mood": mood})
            return False
        track = music.pick_track(video_id, mood, reel_s)
        if track is None:
            note({"requested": True, "applied": False, "reason": "no music tracks in the library", "mood": mood})
            return False
        sfx = music.pick_sfx(video_id)
        join_times = mix.sfx_times(transitions.beat_starts(durations, overlaps), overlaps) if overlaps else []

        result = await asyncio.to_thread(
            mix.mix_reel, reel_path, out_path, track.path,
            join_times=join_times, sfx_path=sfx.path if sfx else None,
            target_lufs=settings.LOUDNESS_TARGET_LUFS, below_db=settings.MUSIC_BELOW_SPEECH_DB,
        )
        os.replace(out_path, reel_path)
        qa = dict(result.metrics)
        qa["loudness_ok"] = abs(qa["final_lufs"] - settings.LOUDNESS_TARGET_LUFS) <= 1.0
        qa["drift_ok"] = qa["av_drift_s"] < 0.08
        note({"requested": True, "applied": True, "mood": mood, "track": track.name, "track_mood": track.mood,
              "credit": track.credit[:300], "sfx": sfx.name if sfx else None, "qa": qa})
        logger.info("Added music '%s' (%s) to %s: %s", track.name, track.mood, video_id, result.metrics)
        return True
    except Exception as exc:  # noqa: BLE001 - never lose the reel over music
        logger.exception("Music failed for %s (%s); shipping the reel without it", video_id, exc)
        note({"requested": True, "applied": False, "reason": f"error: {str(exc)[:200]}"})
        return False
    finally:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
