import os
import tempfile
import uuid

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.config import settings
from app.models.schemas import GenerateReelsRequest
from app.services.auth import CurrentUser, get_current_user
from app.services.logger import get_logger
from app.services.storage import storage_service
from app.services.supabase import supabase
from app.services.tasks import cut_video_clip

load_dotenv()

logger = get_logger("reels_router")
router = APIRouter()


def _require_owned_video(video_id: str, user_id: str) -> str:
    """Look up the video and verify the caller owns it. Returns the storage path."""
    res = supabase.table("videos").select("id, gcs_uri").eq("id", video_id).eq(
        "user_id", user_id
    ).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Video not found")
    return res.data[0]["gcs_uri"]


@router.get("/{video_id}")
async def get_reels(
    video_id: str = Path(..., min_length=1, max_length=64),
    current: CurrentUser = Depends(get_current_user),
):
    # Confirm the caller owns the video
    _require_owned_video(video_id, current.id)

    try:
        res = supabase.table("reels").select("*").eq("video_id", video_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("DB fetch failed for reels video_id=%s", video_id)
        raise HTTPException(status_code=500, detail="Could not load reels")

    if not res.data:
        return {"reels": []}

    reels_out = []
    for reel in res.data:
        try:
            # Prefer signed URL for production. Falls back to public URL.
            url = (
                storage_service.create_signed_url(reel["storage_path"], expires_in=3600)
                or storage_service.get_public_url(reel["storage_path"])
            )
        except Exception:  # noqa: BLE001
            url = ""
        reels_out.append({
            "id": reel.get("id"),
            "title": reel.get("title"),
            "url": url,
            "storage_path": reel.get("storage_path"),
        })

    return {"reels": reels_out}


@router.post("/generate-reels")
async def generate_reels(
    request: GenerateReelsRequest,
    current: CurrentUser = Depends(get_current_user),
):
    storage_path = _require_owned_video(request.video_id, current.id)

    original_video_path = None
    generated_reels = []
    failed_clips = []

    try:
        logger.info("Downloading original video %s for clipping", request.video_id)
        file_bytes = storage_service.download_file(storage_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_original:
            temp_original.write(file_bytes)
            original_video_path = temp_original.name

        for clip in request.timestamps:
            reel_filename = f"reel_{uuid.uuid4().hex[:8]}.mp4"
            storage_target = f"{current.id}/{reel_filename}"
            temp_output_path = os.path.join(tempfile.gettempdir(), reel_filename)

            try:
                cut_video_clip(
                    original_video_path,
                    clip.start_time,
                    clip.end_time,
                    temp_output_path,
                )
                with open(temp_output_path, "rb") as f:
                    storage_service.upload_file(
                        path=storage_target,
                        file_bytes=f.read(),
                        content_type="video/mp4",
                    )

                public_url = storage_service.get_public_url(storage_target)
                signed_url = storage_service.create_signed_url(storage_target, expires_in=3600)

                # Persist the reel record
                insert = supabase.table("reels").insert({
                    "video_id": request.video_id,
                    "storage_path": storage_target,
                    "public_url": public_url or None,
                    "title": clip.title,
                }).execute()

                reels_table_id = None
                if insert.data and isinstance(insert.data, list):
                    reels_table_id = insert.data[0].get("id")

                generated_reels.append({
                    "id": reels_table_id,
                    "title": clip.title,
                    "url": signed_url or public_url,
                })
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to generate clip %s: %s", clip.title, exc)
                failed_clips.append({"title": clip.title, "error": "Clip generation failed"})
            finally:
                if os.path.exists(temp_output_path):
                    try:
                        os.remove(temp_output_path)
                    except OSError:
                        pass

        # Honest success message — the previous version always said "saari reels" even on total failure.
        if generated_reels and not failed_clips:
            msg = f"Generated {len(generated_reels)} reel(s) successfully."
        elif generated_reels and failed_clips:
            msg = f"Generated {len(generated_reels)} reel(s); {len(failed_clips)} failed."
        else:
            msg = "No reels could be generated."

        return {
            "message": msg,
            "reels": generated_reels,
            "failed": failed_clips,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_reels crashed for %s", request.video_id)
        raise HTTPException(status_code=500, detail="Could not generate reels")
    finally:
        if original_video_path and os.path.exists(original_video_path):
            try:
                os.remove(original_video_path)
            except OSError:
                pass
