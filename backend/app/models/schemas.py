from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ReelTimestamp(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    start_time: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time: float = Field(..., gt=0.0, description="End time in seconds")

    @field_validator("end_time")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start_time")
        if start is not None and v <= start:
            raise ValueError("end_time must be greater than start_time")
        # Cap individual clip at 5 minutes to prevent runaway ffmpeg jobs
        if start is not None and (v - start) > 300:
            raise ValueError("Individual reel clips cannot exceed 5 minutes")
        return v


class GenerateReelsRequest(BaseModel):
    video_id: str = Field(..., min_length=1, max_length=64)
    timestamps: List[ReelTimestamp] = Field(..., min_length=1, max_length=10)


class VideoListItem(BaseModel):
    """One row in the dashboard's video list (Phase 11)."""
    id: str
    filename: str
    status: str  # PENDING_UPLOAD | UPLOADED | PROCESSING | READY | FAILED
    created_at: str
    expires_at: str
    reel_count: int = 0
    last_stage: Optional[str] = None  # from jobs.current_stage, null if no job yet


class VideoListResponse(BaseModel):
    videos: List[VideoListItem]
    total: int


class SignedUploadInfo(BaseModel):
    """Response shape for POST /api/videos/upload-url (Phase 11 — for progress bar)."""
    video_id: str
    storage_path: str
    bucket: str
    signed_upload_url: str
    upload_token: str
    expires_at: str
    message: str

