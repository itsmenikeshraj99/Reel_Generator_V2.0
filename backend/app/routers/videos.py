import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from app.config import settings
from app.services.auth import CurrentUser, get_current_user
from app.services.logger import get_logger
from app.services.storage import storage_service
from app.services.supabase import supabase
from app.services.tasks import WorkerUnavailable, tasks_service

load_dotenv()

logger = get_logger("videos_router")
router = APIRouter()

# Use the configured model. Fall back to a hardcoded default only if the env var is missing
# (config.Settings already validates the model name at startup).
_gemini_model_name = os.getenv("GEMINI_MODEL") or settings.GEMINI_MODEL
try:
    gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)
except Exception as exc:  # noqa: BLE001
    logger.error("Failed to initialise Gemini client: %s", exc)
    raise


# --- Pydantic request models ---
class UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)


class ProcessRequest(BaseModel):
    video_id: str = Field(..., min_length=1, max_length=64)


# --- API 1: Generate upload info ---
@router.post("/upload-url", status_code=status.HTTP_201_CREATED)
async def get_upload_info(
    request: UploadRequest,
    current: CurrentUser = Depends(get_current_user),
):
    """Issue a storage path AND a one-time signed upload URL. Requires a valid Supabase session.

    Storage path convention: `<user_id>/<video_id>_<safe_filename>`. The leading
    `<user_id>/` segment is what the Supabase storage RLS policy
    `split_part(name, '/', 1) = auth.uid()` matches on.

    The client PUTs the file bytes directly to `signed_upload_url` with the
    `upload_token` in the query string (already embedded in the URL by
    Supabase's signed-upload flow). This unlocks real progress events via
    XHR — see frontend/src/app/upload/page.tsx for the matching client code.
    """
    video_id = str(uuid.uuid4())
    safe_filename = f"{video_id}_{request.filename.replace(' ', '_')}"
    storage_path = f"{current.id}/{safe_filename}"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    try:
        supabase.table("videos").insert({
            "id": video_id,
            "user_id": current.id,
            "filename": request.filename,
            "status": "PENDING_UPLOAD",
            "gcs_uri": storage_path,
            "expires_at": expires_at,
        }).execute()
    except Exception as exc:  # noqa: BLE001
        logger.error("DB insert failed for user %s: %s", current.id, exc)
        raise HTTPException(status_code=500, detail="Could not create video record")

    # Issue the signed upload URL AFTER the DB row exists, so we know the
    # path is real. If this fails, the DB row is orphaned (status PENDING_UPLOAD)
    # and will be cleaned up by the 24h pg_cron sweep.
    signed = storage_service.create_signed_upload_url(storage_path)
    if not signed or not signed.get("signed_url"):
        logger.error("Failed to create signed upload URL for %s", storage_path)
        # Clean up the orphan row
        try:
            supabase.table("videos").delete().eq("id", video_id).eq(
                "user_id", current.id
            ).execute()
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=500, detail="Could not generate upload URL")

    return {
        "video_id": video_id,
        "storage_path": storage_path,
        "bucket": settings.STORAGE_BUCKET,
        "signed_upload_url": signed["signed_url"],
        "upload_token": signed["token"],
        "message": "Upload URL generated. Video will auto-delete in 24 hours.",
        "expires_at": expires_at,
    }


# --- API 1.5: List the caller's videos (Phase 11 — for the dashboard) ---
@router.get("")
async def list_my_videos(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
):
    """List the caller's videos, newest first. Joins reel count and latest
    job stage in a single round trip per data source.

    Why not a SQL view: schema churn is avoided and the per-request join is
    bounded by `limit` (default 50, max 200). For larger datasets we'd
    revisit — but with a 24h expiry the working set stays small.
    """
    try:
        # 1. Get the user's videos, newest first
        videos_res = (
            supabase.table("videos")
            .select("id, filename, status, created_at, expires_at")
            .eq("user_id", current.id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        videos_data = videos_res.data or []
        if not videos_data:
            return {"videos": [], "total": 0}

        # 2. Total count (cheap because user_id is indexed)
        total_res = (
            supabase.table("videos")
            .select("id", count="exact")
            .eq("user_id", current.id)
            .execute()
        )
        total = total_res.count or len(videos_data)

        # 3. Batch-load reel counts for these video_ids
        video_ids = [v["id"] for v in videos_data]
        reels_res = (
            supabase.table("reels")
            .select("video_id")
            .in_("video_id", video_ids)
            .execute()
        )
        reel_counts: dict = {}
        for r in (reels_res.data or []):
            vid = r.get("video_id")
            if vid:
                reel_counts[vid] = reel_counts.get(vid, 0) + 1

        # 4. Batch-load latest job stage per video_id (newest first per video)
        # Supabase doesn't support DISTINCT ON via the JS SDK, so we fetch
        # all jobs for these videos and keep the newest by started_at.
        # Bounded: at most `limit` videos × few jobs each.
        jobs_res = (
            supabase.table("jobs")
            .select("video_id, current_stage, started_at")
            .in_("video_id", video_ids)
            .order("started_at", desc=True)
            .execute()
        )
        latest_stage: dict = {}
        for j in (jobs_res.data or []):
            vid = j.get("video_id")
            if vid and vid not in latest_stage:
                latest_stage[vid] = j.get("current_stage")

        # 5. Stitch it together
        items = []
        for v in videos_data:
            items.append({
                "id": v["id"],
                "filename": v.get("filename", ""),
                "status": v.get("status", "PENDING_UPLOAD"),
                "created_at": v.get("created_at", ""),
                "expires_at": v.get("expires_at", ""),
                "reel_count": reel_counts.get(v["id"], 0),
                "last_stage": latest_stage.get(v["id"]),
            })

        return {"videos": items, "total": total}

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("list_my_videos crashed for user %s", current.id)
        raise HTTPException(status_code=500, detail="Could not list videos")


# --- API 2: Confirm upload + enqueue pipeline ---
@router.post("/{video_id}/process")
async def process_video(
    video_id: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
):
    try:
        # Ownership check + Tier-3 expiry check
        own = supabase.table("videos").select("id, status, expires_at").eq("id", video_id).eq(
            "user_id", current.id
        ).execute()
        if not own.data:
            raise HTTPException(status_code=404, detail="Video not found")
        expires_at = own.data[0].get("expires_at")
        if expires_at and datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
            raise HTTPException(status_code=403, detail="Link Expired / Session Ended")
        current_status = own.data[0].get("status")
        # Don't allow re-enqueue while a pipeline is already running.
        if current_status == "PROCESSING":
            raise HTTPException(
                status_code=409,
                detail="This video is already being processed. Please wait.",
            )

        supabase.table("videos").update({"status": "UPLOADED"}).eq("id", video_id).eq(
            "user_id", current.id
        ).execute()

        try:
            task_name = await tasks_service.enqueue_pipeline_task(video_id)
        except WorkerUnavailable as exc:
            logger.error("Worker enqueue failed for %s: %s", video_id, exc)
            raise HTTPException(status_code=502, detail="Processing service unavailable. Please try again shortly.")

        return {
            "message": "Video upload confirmed. Processing started!",
            "video_id": video_id,
            "task_id": task_name,
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_video crashed for %s", video_id)
        raise HTTPException(status_code=500, detail="Could not start processing")


# --- API 2.5: Status ---
@router.get("/{video_id}/status")
async def get_video_status(
    video_id: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
):
    # Verify the caller owns the video before exposing any job state
    own = supabase.table("videos").select("id, status, expires_at").eq("id", video_id).eq(
        "user_id", current.id
    ).execute()
    if not own.data:
        raise HTTPException(status_code=404, detail="Video not found")
    # Tier-3 failsafe
    expires_at = own.data[0].get("expires_at")
    if expires_at and datetime.now(timezone.utc) > datetime.fromisoformat(expires_at):
        raise HTTPException(status_code=403, detail="Link Expired / Session Ended")

    try:
        job_res = supabase.table("jobs").select("current_stage, status, last_error").eq(
            "video_id", video_id
        ).order("started_at", desc=True).limit(1).execute()

        if not job_res.data:
            return {"status": own.data[0]["status"], "stage": None, "error": None}

        job = job_res.data[0]
        return {
            "status": job["current_stage"] if job["status"] == "RUNNING" else job["status"],
            "stage": job["current_stage"],
            "error": job.get("last_error"),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_video_status crashed for %s", video_id)
        raise HTTPException(status_code=500, detail="Could not fetch status")


# --- API 3: AI analysis (Gemini proxy) ---
@router.post("/analyze-video")
async def analyze_video(
    request: ProcessRequest,
    current: CurrentUser = Depends(get_current_user),
):
    try:
        db_res = supabase.table("videos").select("*").eq("id", request.video_id).eq(
            "user_id", current.id
        ).execute()
        if not db_res.data:
            raise HTTPException(status_code=404, detail="Video not found")

        # Tier-3 failsafe
        expires_at = datetime.fromisoformat(db_res.data[0]["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=403, detail="Link Expired / Session Ended")

        storage_path = db_res.data[0]["gcs_uri"]
        file_bytes = storage_service.download_file(storage_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
            temp_video_path = temp_video.name
            temp_video.write(file_bytes)

        try:
            video_file = gemini_client.files.upload(file=temp_video_path)

            # Poll for ACTIVE state with a hard timeout
            deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
            while "PROCESSING" in str(video_file.state):
                if datetime.now(timezone.utc) > deadline:
                    raise HTTPException(status_code=504, detail="AI processing timed out")
                import asyncio
                await asyncio.sleep(2)
                video_file = gemini_client.files.get(name=video_file.name)

            if "FAILED" in str(video_file.state):
                raise HTTPException(status_code=500, detail="AI processing failed")

            prompt = (
                "Find 3 viral segments. Format: JSON {segments: [{start, end, title, reason}]}"
            )
            response = gemini_client.models.generate_content(
                model=_gemini_model_name,
                contents=[video_file, prompt],
            )
        finally:
            if os.path.exists(temp_video_path):
                try:
                    os.remove(temp_video_path)
                except OSError:
                    pass

        return {
            "message": "AI Analysis successful!",
            "reels_ideas": response.text,
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze_video crashed for %s", request.video_id)
        raise HTTPException(status_code=500, detail="AI analysis failed")


# --- API 4: Delete (user-initiated Tier-1 "instant kill") ---
# Hard-deletes a video session: original upload + all reels + DB row.
# Frontend "Finish & Clear Session" button calls this. The pg_cron job
# calls the same function for the Tier-2 auto-cleanup, so behavior is
# identical regardless of who triggers it.
@router.delete("/{video_id}")
async def delete_video(
    video_id: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
):
    try:
        # Ownership check — only the row's owner can delete it
        own = supabase.table("videos").select("id, gcs_uri").eq("id", video_id).eq(
            "user_id", current.id
        ).execute()
        if not own.data:
            raise HTTPException(status_code=404, detail="Video not found")
        original_storage_path = own.data[0]["gcs_uri"]

        # Collect every storage path we need to remove: the original upload
        # plus every reel's output. Both are in the same bucket.
        reels_res = supabase.table("reels").select("storage_path").eq(
            "video_id", video_id
        ).execute()
        paths_to_delete = [original_storage_path]
        for r in (reels_res.data or []):
            sp = r.get("storage_path")
            if sp and sp not in paths_to_delete:
                paths_to_delete.append(sp)

        # Tier-1: best-effort storage cleanup. Don't fail the request if
        # storage is unavailable — the DB row deletion is what actually
        # protects the user. A 24h pg_cron pass will sweep up any orphans.
        try:
            storage_service.delete_files(paths_to_delete)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Storage delete failed for video %s: %s", video_id, exc)

        # DB row delete. ON DELETE CASCADE on the FKs (transcripts,
        # edit_plans, reels, jobs) takes care of the rest. RLS makes
        # sure we can only delete our own row.
        supabase.table("videos").delete().eq("id", video_id).eq(
            "user_id", current.id
        ).execute()

        return {
            "message": "Session cleared. All videos and reels have been deleted.",
            "video_id": video_id,
            "deleted_storage_paths": len(paths_to_delete),
        }

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("delete_video crashed for %s", video_id)
        raise HTTPException(status_code=500, detail="Could not clear session")
