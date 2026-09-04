import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Path, status
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
    """Issue a storage path the client can upload to. Requires a valid Supabase session.

    Storage path convention: `<user_id>/<video_id>_<safe_filename>`. The leading
    `<user_id>/` segment is what the Supabase storage RLS policy
    `split_part(name, '/', 1) = auth.uid()` matches on.
    """
    video_id = str(uuid.uuid4())
    safe_filename = f"{video_id}_{request.filename.replace(' ', '_')}"
    # The bucket stores objects at <user_id>/<video_filename> so the storage
    # RLS policy `auth.uid() = split_part(name, '/', 1)` passes for the owner.
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

    return {
        "video_id": video_id,
        "storage_path": storage_path,
        "bucket": settings.STORAGE_BUCKET,
        "message": "Upload details generated. Video will auto-delete in 24 hours.",
        "expires_at": expires_at,
    }


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
