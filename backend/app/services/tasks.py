import asyncio
import logging
import os
import subprocess
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("tasks_service")


class WorkerUnavailable(Exception):
    """Raised when the worker cannot be reached after retries."""


def cut_video_clip(input_path: str, start_time: float, end_time: float, output_path: str) -> None:
    """Cuts a video clip using FFmpeg. Re-encodes with libx264 so output is web-playable
    and cuts are keyframe-accurate (the previous `-c copy` path was off by several seconds
    on non-aligned sources)."""
    if end_time <= start_time:
        raise ValueError(f"end_time ({end_time}) must be > start_time ({start_time})")
    if start_time < 0:
        raise ValueError(f"start_time must be >= 0, got {start_time}")

    duration = end_time - start_time
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out after {exc.timeout}s") from exc

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={result.returncode}): {result.stderr[-500:]}")


class TasksService:
    def __init__(self) -> None:
        self.worker_url = settings.WORKER_URL
        self.shared_secret = settings.WORKER_SHARED_SECRET

    async def enqueue_pipeline_task(self, video_id: str) -> str:
        """POST to the worker with retry. Raises WorkerUnavailable on hard failure.

        Returns the worker-side task_id on success. The previous implementation
        returned the literal string "failed-to-enqueue" on failure, which the router
        treated as success — that bug is fixed here.
        """
        if not self.shared_secret:
            raise WorkerUnavailable("WORKER_SHARED_SECRET is not configured on the backend")

        headers = {"X-Worker-Secret": self.shared_secret, "Content-Type": "application/json"}
        payload = {"video_id": video_id}

        last_exc: Optional[Exception] = None
        backoffs = [1, 2, 4]
        for attempt, backoff in enumerate([0] + backoffs, start=1):
            if backoff:
                await asyncio.sleep(backoff)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(self.worker_url, json=payload, headers=headers)
                if response.status_code == 401:
                    # Auth will never recover with retry — fail fast
                    raise WorkerUnavailable(
                        "Worker rejected the shared secret. Check WORKER_SHARED_SECRET on both sides."
                    )
                response.raise_for_status()
                data = response.json()
                task_id = data.get("task_id") or data.get("video_id") or "unknown"
                logger.info("Worker accepted video_id=%s task_id=%s (attempt %d)", video_id, task_id, attempt)
                return str(task_id)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                logger.warning("Worker returned %s on attempt %d: %s", exc.response.status_code, attempt, exc)
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                last_exc = exc
                logger.warning("Worker request failed on attempt %d: %s", attempt, exc)

        raise WorkerUnavailable(f"Worker unreachable after retries: {last_exc}")


tasks_service = TasksService()
