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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker_main")

app = FastAPI(title="AI Reels Worker")

# Pipeline work is mostly sync (httpx sync, supabase sync, ffmpeg subprocess).
# Running it directly on the event loop blocks the loop for minutes and
# prevents other requests (like /process) from being accepted.
# A small thread pool lets us dispatch pipelines to a background thread while
# the event loop stays responsive to incoming HTTP requests.
_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("WORKER_MAX_PARALLEL", "2")),
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
    # Dispatch to the thread pool so the response returns immediately
    # and the event loop stays free to accept more /process calls.
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
