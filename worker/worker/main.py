"""Worker HTTP entry point.

Auth: requires `X-Worker-Secret` header to match `WORKER_SHARED_SECRET` from the backend.
Bind: defaults to 127.0.0.1; set WORKER_HOST=0.0.0.0 only when behind a trusted proxy.
"""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from worker.config import settings
from worker.pipeline import Pipeline
from worker.services.supabase import supabase_client


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker_main")

app = FastAPI(title="AI Reels Worker")

# Pipeline work is mostly sync (httpx sync, supabase sync, ffmpeg subprocess).
# Running it directly on the event loop blocks the loop for minutes and
# prevents other requests (like /process) from being accepted.
# A small thread pool lets us dispatch pipelines to a background thread while
# the event loop stays responsive to incoming HTTP requests.
#
# Memory: default 1 (was 2) because Railway free-tier workers ship
# with 1GB RAM. Two parallel ffmpeg pipelines at 720p would still
# peak around 1.2GB combined, which OOMs. One-at-a-time is the
# safe default; paid plans can override via WORKER_MAX_PARALLEL env.
_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("WORKER_MAX_PARALLEL", "1")),
    thread_name_prefix="pipeline",
)


class ProcessRequest(BaseModel):
    video_id: str


def require_worker_secret(x_worker_secret: str | None = Header(default=None)) -> None:
    """Reject any caller that doesn't present the shared secret."""
    if not settings.WORKER_SHARED_SECRET:
        # Worker mis-configured — fail closed.
        raise HTTPException(status_code=503, detail="Worker not configured")
    if x_worker_secret != settings.WORKER_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Worker-Secret",
        )


def _run_pipeline_sync(video_id: str) -> None:
    """Synchronously run the pipeline in a worker thread."""
    try:
        # Build a fresh event loop so the (mostly-async) Pipeline can use
        # `await` on sync-friendly code. We don't reuse the loop because we
        # are in a thread that has no loop of its own.
        loop = asyncio.new_event_loop()
        try:
            pipeline = Pipeline(video_id)
            loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline execution failed for %s: %s", video_id, exc)


@app.post("/process")
async def process_video(
    request: ProcessRequest,
    _auth: None = Depends(require_worker_secret),
):
    logger.info("Accepted process request for video %s", request.video_id)

    # --- Fix: Ghost Job Race Condition ---
    # Durably initialize job state BEFORE acknowledging success to the backend.
    try:
        # 1. Determine if we are resuming a previously failed run
        resume_stage = "PENDING"
        try:
            cur = supabase_client.table("jobs").select("current_stage, status").eq(
                "video_id", request.video_id
            ).order("started_at", desc=True).limit(1).execute()

            if cur.data:
                prior_stage = cur.data[0].get("current_stage")
                prior_status = cur.data[0].get("status")
                _RESUMABLE = {"VALIDATING", "TRANSCRIBING_PLANNING", "REVIEWING", "RENDERING"}
                if prior_stage and prior_stage in _RESUMABLE and prior_status in ("FAILED", "RUNNING"):
                    resume_stage = prior_stage
        except Exception as exc:
            logger.warning("Could not read prior job state for %s: %s", request.video_id, exc)
            # We proceed with "PENDING" as a fallback

        # 2. Sync upsert to ensure the job exists before response
        supabase_client.table("jobs").upsert({
            "video_id": request.video_id,
            "current_stage": resume_stage,
            "status": "RUNNING",
            "last_error": None,
        }, on_conflict="video_id").execute()
    except Exception as exc:
        logger.exception("Failed to initialize job state for %s: %s", request.video_id, exc)
        raise HTTPException(status_code=500, detail="Internal worker error initializing job state")

    # Dispatch to the thread pool so the response returns immediately
    try:
        _EXECUTOR.submit(_run_pipeline_sync, request.video_id)
    except RuntimeError as exc:
        # Executor shut down (e.g. process is exiting). Surface the failure.
        logger.error("Could not submit pipeline task: %s", exc)
        raise HTTPException(status_code=503, detail="Worker shutting down")
    return {
        "status": "queued",
        "video_id": request.video_id,
        "task_id": f"worker-{request.video_id[:8]}",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "reels-generator-worker",
        "active_threads": len(_EXECUTOR._threads),  # type: ignore[attr-defined]
    }


if __name__ == "__main__":
    host = os.getenv("WORKER_HOST", settings.WORKER_HOST)
    port = int(os.getenv("WORKER_PORT", settings.WORKER_PORT))
    logger.info("Starting worker on %s:%s (parallel=%s)", host, port, _EXECUTOR._max_workers)
    uvicorn.run(app, host=host, port=port, log_level="info")
