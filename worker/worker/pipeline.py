"""Pipeline orchestrator.

State machine: VALIDATING -> TRANSCRIBING_PLANNING -> REVIEWING -> RENDERING -> READY.

Each `transition` writes the new stage to the `jobs` row but uses `update().eq()`
instead of `upsert` so we never clobber `last_error` or `started_at` on later stages.

Phase 7 — stage-level retry
----------------------------
When the worker is asked to process a video whose `current_stage` is something
other than READY (e.g. a previously-failed run), the pipeline **resumes from
that stage** rather than re-running the entire sequence. Each stage is wrapped
in `retry_stage()` which retries the stage body up to `MAX_RETRIES_PER_STAGE`
times before giving up. A give-up sets `status = PERMANENTLY_FAILED` so the
UI can distinguish "we tried, it didn't work" from "we crashed mid-run".
"""
import asyncio
import gc
import logging
import os
import subprocess
import time
import random
import tempfile
from typing import Any, Awaitable, Callable, Optional



from worker.config import settings
from worker.gemini.client import gemini_client
from worker.services.storage import worker_storage
from worker.services.supabase import supabase_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pipeline")

# Ordered stage list — used to detect "skip ahead" scenarios.
# PENDING is the "no work yet" placeholder written by the initial upsert
# when there's no prior job row. Listing it at index -1 means the
# "already past" check in `transition()` correctly allows any real stage
# to advance from PENDING, but also makes the check on a row that's been
# regressed to PENDING (e.g. by a buggy older version of the upsert)
# fire correctly.
_STAGES = ["PENDING", "VALIDATING", "TRANSCRIBING_PLANNING", "REVIEWING", "RENDERING", "READY"]

# Phase 7: max attempts per stage. Counts the *initial* run plus retries.
# So 3 == 1 initial + 2 retries.
MAX_RETRIES_PER_STAGE = 3

# Backoff between retry attempts (seconds). Linear, not exponential, to keep
# retries snappy for transient blips (ffmpeg OOM, Gemini rate-limit, etc).
RETRY_BACKOFF_SECONDS = 5


class NonRetryableError(Exception):
    """Raised when a stage failure is permanent and should not be retried."""
    pass

class Pipeline:
    def __init__(self, video_id: str) -> None:
        self.video_id = video_id
        self.state = "UPLOADED"
        self.gemini_file_uri: Optional[str] = None
        # Stage where the pipeline should resume on retry. If a previous
        # run left the job at RENDERING, the new run starts at RENDERING
        # rather than at VALIDATING. `None` means "from the top".
        self._resume_from: Optional[str] = None

    async def _heartbeat(self) -> None:
        """Update the `updated_at` timestamp to signal liveness to the backend."""
        try:
            supabase_client.table("jobs").update({
                "updated_at": "now()",
            }).eq("video_id", self.video_id).eq("status", "RUNNING").execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Heartbeat failed for %s: %s", self.video_id, exc)

    async def _run_with_heartbeat(self, cmd: list[str], timeout: int = 600) -> None:
        """Run a subprocess and update the heartbeat periodically.

        Replaces subprocess.run to prevent long-running processes from being
        marked as stale by the backend watchdog.
        """
        # Note: subprocess.Popen is synchronous. We run it in the executor
        # via _run_pipeline_sync, so this is acceptable.
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        start_time = time.time()
        try:
            while process.poll() is None:
                # Wait for a bit before checking again and sending heartbeat
                time.sleep(60)
                await self._heartbeat()

                if time.time() - start_time > timeout:
                    process.kill()
                    raise RuntimeError(f"Subprocess timed out after {timeout}s")

            stdout, stderr = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"Subprocess failed (rc={process.returncode}): {stderr[-500:]}")
        except Exception:
            process.kill()
            process.wait()
            raise

    async def run(self) -> None:
        logger.info("Starting pipeline for video %s", self.video_id)
        temp_src_path: Optional[str] = None
        reframed_path: Optional[str] = None
        output_path: Optional[str] = None
        captioned_path: Optional[str] = None
        reel_storage_path = f"reels/{self.video_id}.mp4"

        try:
            # 0b. Initial download
            res = supabase_client.table("videos").select("gcs_uri").eq(
                "id", self.video_id
            ).single().execute()
            gcs_uri = res.data["gcs_uri"]
            file_bytes = worker_storage.download_file(gcs_uri)
            temp_src_path = os.path.join(tempfile.gettempdir(), f"src_{self.video_id}.mp4")
            with open(temp_src_path, "wb") as f:
                f.write(file_bytes)

            # Helper: should we run a given stage at all? On a fresh run
            # we run every stage. On a resume we skip anything *before*
            # the resume point.
            def _should_run(stage: str) -> bool:
                if self._resume_from is None:
                    return True
                try:
                    return _STAGES.index(stage) >= _STAGES.index(self._resume_from)
                except ValueError:
                    return True

            # 1. Validate
            if _should_run("VALIDATING"):
                if not await self.transition("VALIDATING"):
                    return await self.fail("Skipped VALIDATING")
                if not await self.retry_stage(
                    "VALIDATING",
                    lambda: self._stage_validate(temp_src_path),
                ):
                    return  # retry_stage already called fail() on exhaustion
            else:
                logger.info("Skipping VALIDATING (resume from %s)", self._resume_from)

            # 2. Transcribe & plan
            if _should_run("TRANSCRIBING_PLANNING"):
                if not await self.transition("TRANSCRIBING_PLANNING"):
                    return await self.fail("Skipped TRANSCRIBING_PLANNING")
                if not await self.retry_stage(
                    "TRANSCRIBING_PLANNING",
                    lambda: self._stage_transcribe_plan(temp_src_path),
                ):
                    return
            else:
                logger.info("Skipping TRANSCRIBING_PLANNING (resume from %s)", self._resume_from)

            # 3. Review
            if _should_run("REVIEWING"):
                if not await self.transition("REVIEWING"):
                    return await self.fail("Skipped REVIEWING")
                if not await self.retry_stage(
                    "REVIEWING",
                    lambda: self._stage_review(),
                ):
                    return
            else:
                logger.info("Skipping REVIEWING (resume from %s)", self._resume_from)

            # 4. Render
            if _should_run("RENDERING"):
                if not await self.transition("RENDERING"):
                    return await self.fail("Skipped RENDERING")
                reframed_path = os.path.join(tempfile.gettempdir(), f"reframed_{self.video_id}.mp4")
                output_path = os.path.join(tempfile.gettempdir(), f"reel_{self.video_id}.mp4")
                # Same filename _stage_render builds internally for the
                # caption-burned file. Assigning it here (not just inside
                # _stage_render's own local variable) lets the `finally`
                # cleanup below actually find and delete it — previously
                # this outer captioned_path was never reassigned, so the
                # captioned temp file was silently left on disk after
                # every successful render.
                captioned_path = os.path.join(tempfile.gettempdir(), f"reel_{self.video_id}_captioned.mp4")
                if not await self.retry_stage(
                    "RENDERING",
                    lambda: self._stage_render(temp_src_path, reframed_path, output_path, reel_storage_path),
                ):
                    return
            else:
                logger.info("Skipping RENDERING (resume from %s)", self._resume_from)

            # 5. Done
            await self.transition("READY")
            # Clear retry_count and last_error on success so the status
            # page shows a clean record. A new failure will start
            # counting from 0 again.
            try:
                supabase_client.table("jobs").update({
                    "retry_count": 0,
                    "last_error": None,
                }).eq("video_id", self.video_id).execute()
            except Exception:  # noqa: BLE001
                pass
            # Also mark the parent videos row READY so the status endpoint
            # (which falls back to videos.status when no job row exists) reports done.
            try:
                supabase_client.table("videos").update({"status": "READY"}).eq(
                    "id", self.video_id
                ).execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not mark videos.status=READY for %s: %s", self.video_id, exc)
            logger.info("Pipeline completed successfully for %s", self.video_id)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline crashed for %s: %s", self.video_id, exc)
            await self.fail(str(exc))
        finally:
            # Cleanup all temporary files
            # We use a glob search for compressed versions to avoid tracking every attempt path
            try:
                import glob
                compressed_pattern = os.path.join(tempfile.gettempdir(), f"reel_{self.video_id}_compressed_*.mp4")
                for p in glob.glob(compressed_pattern):
                    os.remove(p)
            except Exception:
                pass

            for p in (temp_src_path, reframed_path, output_path, captioned_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass


    async def retry_stage(
        self,
        stage: str,
        body: Callable[[], Awaitable[bool]],
    ) -> bool:
        """Run a stage up to MAX_RETRIES_PER_STAGE times. On success, reset
        retry_count and return True. On final failure, mark the job
        PERMANENTLY_FAILED and return False.

        Between attempts we use exponential backoff with jitter to let
        transient issues (Gemini 503, OOM, etc) clear. Exceptions are caught
        and treated as a failed attempt.
        """
        last_err: str = ""
        for attempt in range(1, MAX_RETRIES_PER_STAGE + 1):
            try:
                ok = await body()
                if ok:
                    # Reset retry count for this stage on success.
                    try:
                        supabase_client.table("jobs").update({
                            "retry_count": 0,
                        }).eq("video_id", self.video_id).execute()
                    except Exception:  # noqa: BLE001
                        pass
                    if attempt > 1:
                        logger.info(
                            "Stage %s for %s succeeded on attempt %d/%d",
                            stage, self.video_id, attempt, MAX_RETRIES_PER_STAGE,
                        )
                    return True
                last_err = f"stage body returned False (attempt {attempt}/{MAX_RETRIES_PER_STAGE})"
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, NonRetryableError):
                    last_err = f"Non-retryable error: {exc}"[:500]
                    logger.error("Stage %s for %s failed with non-retryable error: %s", stage, self.video_id, exc)
                    await self.permanent_fail(last_err)
                    return False

                last_err = f"{type(exc).__name__}: {exc}"[:500]
                logger.warning(
                    "Stage %s for %s raised on attempt %d/%d: %s",
                    stage, self.video_id, attempt, MAX_RETRIES_PER_STAGE, last_err,
                )

            # Bump the retry counter in Supabase so the UI can see how
            # many attempts have been burned on this stage.
            try:
                supabase_client.table("jobs").update({
                    "retry_count": attempt,
                    "last_error": last_err[:1000],
                    "status": "RUNNING",
                }).eq("video_id", self.video_id).execute()
            except Exception:  # noqa: BLE001
                pass

            # If we have attempts left, back off and try again.
            if attempt < MAX_RETRIES_PER_STAGE:
                # Exponential backoff: 5 * (2 ^ (attempt-1)) + jitter
                # Attempt 1 -> 2: 5 * 1 + [0, 2) = 5-7s
                # Attempt 2 -> 3: 5 * 2 + [0, 2) = 10-12s
                sleep_time = (RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))) + random.uniform(0, 2)
                logger.info(
                    "Retrying stage %s for %s in %.2fs (attempt %d → %d)",
                    stage, self.video_id, sleep_time, attempt, attempt + 1,
                )
                await asyncio.sleep(sleep_time)

        # Exhausted. Permanent fail.
        await self.permanent_fail(
            f"Stage {stage} failed after {MAX_RETRIES_PER_STAGE} attempts: {last_err}"
        )
        return False

    async def transition(self, new_stage: str) -> bool:
        """Update the `jobs` row to the new stage. Returns True if we should proceed,
        False if the job is already past this stage (idempotent restart)."""
        logger.info("Transition %s -> %s", self.video_id, new_stage)

        # Read current state to allow idempotent restarts
        try:
            cur = supabase_client.table("jobs").select("status, current_stage").eq(
                "video_id", self.video_id
            ).order("started_at", desc=True).limit(1).execute()
            if cur.data:
                cur_stage = cur.data[0].get("current_stage")
                if cur_stage in _STAGES and _STAGES.index(cur_stage) > _STAGES.index(new_stage):
                    logger.info(
                        "Job %s already past %s (current=%s); skipping",
                        self.video_id, new_stage, cur_stage,
                    )
                    return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read current job state for %s: %s", self.video_id, exc)

        # update() not upsert() so we don't clobber last_error / started_at
        # When transitioning to READY, flip status to READY too so the
        # status endpoint sees a terminal success without waiting for
        # the videos row to update.
        next_status = "READY" if new_stage == "READY" else "RUNNING"
        supabase_client.table("jobs").update({
            "current_stage": new_stage,
            "status": next_status,
        }).eq("video_id", self.video_id).execute()

        self.state = new_stage
        return True

    async def fail(self, error_msg: str) -> None:
        """Mark a transient failure. Caller is expected to either re-run
        the pipeline (which will hit the retry budget) or surface the
        error to the user. The job can be re-submitted by re-hitting
        `/process` — the resume logic in `run()` will pick the stage back up."""
        logger.error("Job %s failed at %s: %s", self.video_id, self.state, error_msg)
        supabase_client.table("jobs").update({
            "status": "FAILED",
            "current_stage": self.state,
            "last_error": error_msg[:1000],
        }).eq("video_id", self.video_id).execute()
        # Mirror the failure onto the videos row so the status endpoint sees it
        # even before the jobs row is read.
        try:
            supabase_client.table("videos").update({"status": "FAILED"}).eq(
                "id", self.video_id
            ).execute()
        except Exception:  # noqa: BLE001
            pass

    async def permanent_fail(self, error_msg: str) -> None:
        """Phase 7: a stage hit its retry budget. Mark the job so dead
        that no amount of re-hitting /process will pick it up without a
        human lifting the retry_count in the DB."""
        logger.error(
            "Job %s PERMANENTLY FAILED at %s: %s",
            self.video_id, self.state, error_msg,
        )
        supabase_client.table("jobs").update({
            "status": "PERMANENTLY_FAILED",
            "current_stage": self.state,
            "last_error": error_msg[:1000],
        }).eq("video_id", self.video_id).execute()
        try:
            supabase_client.table("videos").update({"status": "FAILED"}).eq(
                "id", self.video_id
            ).execute()
        except Exception:  # noqa: BLE001
            pass

    async def _get_video_duration(self, video_path: str) -> float:
        """Helper to get video duration using ffprobe."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                stderr=subprocess.STDOUT, timeout=30,
            ).decode().strip()
            return float(out)
        except Exception as exc:
            logger.warning("Could not get duration for %s: %s", video_path, exc)
            return 0.0

    async def _stage_validate(self, video_path: str) -> bool:
        from worker.stages.validate import validate_video
        return await validate_video(video_path)

    async def _stage_transcribe_plan(self, video_path: str) -> bool:
        from worker.stages.transcribe_plan import transcribe_and_plan
        # Pass the shared gemini_file_uri to avoid re-uploading on stage retries.
        # The stage will update self.gemini_file_uri once the upload is successful.
        result = await transcribe_and_plan(
            self.video_id, video_path,
            existing_uri=self.gemini_file_uri,
            update_uri=lambda uri: setattr(self, 'gemini_file_uri', uri)
        )
        return result

    async def _stage_review(self) -> bool:
        from worker.stages.review import review_candidates
        return await review_candidates(self.video_id)

    async def _stage_render(
        self,
        video_path: str,
        reframed_path: str,
        output_path: str,
        storage_target: str,
    ) -> bool:
        from worker.stages.reframe import reframe_video
        from worker.stages.stitch import stitch_and_caption
        from worker.stages.caption import burn_captions

        # Quality flag — drives the `meta` jsonb on the reels row so the
        # frontend can label a degraded reel accordingly. The default is
        # `full` (subject-aware crop). If reframe fails we degrade to
        # `raw_cut` (no subject tracking, plain 9:16 letterbox from the
        # source). User still gets a usable reel rather than nothing.
        quality = "full"

        try:
            # 1. Reframe (subject-aware crop). On any failure we log and
            #    fall back to stitching from the raw source — the stitch
            #    step already runs a 9:16 letterbox pad so the output is
            #    still valid 1080x1920.
            reframe_ok = False
            try:
                # Pass self to enable heartbeats during long render
                reframe_ok = await reframe_video(self, video_path, reframed_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reframe_video crashed for %s (%s); falling back to raw cut",
                    self.video_id, exc,
                )

            if reframe_ok:
                stitch_input = reframed_path
            else:
                quality = "raw_cut"
                logger.warning(
                    "Reframe unavailable for %s; shipping raw-cut 9:16 letterbox",
                    self.video_id,
                )
                stitch_input = video_path

            # 2. Stitch. Failure here is fatal (no further fallback).
            # Pass self to enable heartbeats
            if not await stitch_and_caption(self, self.video_id, stitch_input, output_path):
                return False

            # Free the reframed temp file before the caption step — it's
            # 50-150MB on disk and no longer referenced. Keeping it
            # around just increases the chance that /tmp fills up on
            # long-running workers or that we OOM at peak. The outer
            # `finally` block would clean it up anyway, but doing it
            # here shaves the peak memory footprint during caption.
            if reframe_ok and stitch_input == reframed_path and os.path.exists(reframed_path):
                try:
                    os.remove(reframed_path)
                except OSError:
                    pass

            # Force a Python GC pass between the heavy ffmpeg calls.
            # ffmpeg subprocesses release their memory on exit, but
            # Python holds onto the Process handles and any frame
            # buffers pip/the supabase client cached during the
            # previous stage. On a 1GB worker this is the difference
            # between fitting and OOM during the next stage.
            gc.collect()

            # 3. Caption overlay: best-effort. On failure we still ship the
            #    caption-stripped video rather than nothing.
            final_path = output_path
            captioned_path = os.path.join(
                tempfile.gettempdir(), f"reel_{self.video_id}_captioned.mp4"
            )
            # Pass self to enable heartbeats
            if await burn_captions(self, self.video_id, output_path, captioned_path):
                final_path = captioned_path

            # --- Adaptive Size Control ---
            # Supabase Free plan has a 50 MiB upload limit. We target 45 MiB.
            TARGET_SIZE_BYTES = 45 * 1024 * 1024
            AUDIO_BITRATE_BPS = 128000
            MIN_VIDEO_BITRATE_BPS = 800000 # 800 kbps floor for 720p quality

            final_size = os.path.getsize(final_path)
            if final_size > TARGET_SIZE_BYTES:
                logger.info("Final reel size (%d MB) exceeds target (45 MB). Starting adaptive compression...", final_size // (1024*1024))

                duration = await self._get_video_duration(final_path)
                if duration <= 0:
                    raise NonRetryableError(f"Could not determine duration for {final_path}; cannot compress")

                compressed_path = None
                for attempt in range(1, 4):
                    # Calculate target bitrate. For retries, be slightly more aggressive (reduce target size by 10% per attempt)
                    attempt_target_bytes = TARGET_SIZE_BYTES * (0.9 ** (attempt - 1))
                    target_video_bitrate = ((attempt_target_bytes * 8) - (AUDIO_BITRATE_BPS * duration)) / duration

                    if target_video_bitrate < MIN_VIDEO_BITRATE_BPS:
                        logger.error("Calculated bitrate (%.2f kbps) below quality threshold (800 kbps)", target_video_bitrate / 1000)
                        raise NonRetryableError(
                            f"Reel is too long ({duration:.1f}s) to fit in 45MB without destroying quality. "
                            f"Required bitrate: {target_video_bitrate/1000:.1f} kbps"
                        )

                    current_compressed_path = os.path.join(tempfile.gettempdir(), f"reel_{self.video_id}_compressed_{attempt}.mp4")

                    logger.info(
                        "Adaptive encode attempt %d/3: target_bitrate=%.2f kbps, duration=%.1fs",
                        attempt, target_video_bitrate / 1000, duration
                    )

                    compress_cmd = [
                        "ffmpeg", "-y",
                        "-threads", "1",
                        "-filter_threads", "1",
                        "-i", final_path,
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-b:v", str(int(target_video_bitrate)),
                        "-maxrate", str(int(target_video_bitrate * 1.2)),
                        "-bufsize", str(int(target_video_bitrate * 2)),
                        "-c:a", "aac", "-b:a", str(AUDIO_BITRATE_BPS // 1000),
                        "-movflags", "+faststart",
                        current_compressed_path,
                    ]

                    await self._run_with_heartbeat(compress_cmd)

                    compressed_size = os.path.getsize(current_compressed_path)
                    logger.info("Attempt %d result: %d MB", attempt, compressed_size // (1024*1024))

                    if compressed_size <= TARGET_SIZE_BYTES:
                        compressed_path = current_compressed_path
                        break

                if not compressed_path or os.path.getsize(compressed_path) > TARGET_SIZE_BYTES:
                    final_measured_size = os.path.getsize(final_path) if not compressed_path else os.path.getsize(compressed_path)
                    raise NonRetryableError(
                        f"Could not compress reel below 45 MiB after 3 attempts. Final size: {final_measured_size // (1024*1024)} MB, Duration: {duration:.1f}s"
                    )

                # Update final_path to the compressed version and track it for cleanup
                # We can't easily add to the run() finally block from here without a class attribute,
                # so we'll rely on the fact that the run() finally block cleans all paths.
                # However, the run() finally block currently only knows about 4 specific paths.
                # We should ensure this new path is cleaned up.
                final_path = compressed_path

            with open(final_path, "rb") as f:
                worker_storage.upload_file(storage_target, f.read(), content_type="video/mp4")


            # Insert the reels row. The `meta` column is the Phase 6
            # addition; if the live DB hasn't been migrated yet, fall
            # back to a no-meta insert so the pipeline still ships a
            # reel.
            reels_row = {
                "video_id": self.video_id,
                "storage_path": storage_target,
                "title": "AI Generated Reel",
            }
            try:
                supabase_client.table("reels").insert({**reels_row, "meta": {
                    "quality": quality,
                    "captioned": final_path == captioned_path,
                }}).execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reels insert with meta failed (%s); retrying without meta", exc,
                )
                supabase_client.table("reels").insert(reels_row).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            # Clean up the partial upload so storage doesn't leak
            try:
                worker_storage.delete_file(storage_target)
            except Exception:  # noqa: BLE001
                pass
            logger.exception("stage_render failed: %s", exc)
            return False
