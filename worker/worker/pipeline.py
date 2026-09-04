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
import logging
import os
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


class Pipeline:
    def __init__(self, video_id: str) -> None:
        self.video_id = video_id
        self.state = "UPLOADED"
        # Stage where the pipeline should resume on retry. If a previous
        # run left the job at RENDERING, the new run starts at RENDERING
        # rather than at VALIDATING. `None` means "from the top".
        self._resume_from: Optional[str] = None

    async def run(self) -> None:
        logger.info("Starting pipeline for video %s", self.video_id)
        temp_src_path: Optional[str] = None
        reframed_path: Optional[str] = None
        output_path: Optional[str] = None
        captioned_path: Optional[str] = None
        reel_storage_path = f"reels/{self.video_id}.mp4"

        try:
            # 0a. Create the jobs row up-front so the status page can poll progress.
            #     `transition()` only UPDATES; if the row was never INSERTed the
            #     update is a no-op and the frontend never sees the stage advance.
            #     `uq_jobs_video_id` makes this safe to re-run (idempotent).
            #     Phase 7: detect a previous failed run and set `_resume_from`
            #     so we skip already-completed stages.
            try:
                # Try the full Phase 7 read first; fall back to a slimmer
                # select if `retry_count` hasn't been migrated to the live
                # DB yet. The `except` block below handles the 42703 error
                # from PostgREST.
                prior_stage: Optional[str] = None
                prior_status: Optional[str] = None
                prior_retries: int = 0
                try:
                    cur = supabase_client.table("jobs").select(
                        "current_stage, status, retry_count"
                    ).eq("video_id", self.video_id).order("started_at", desc=True).limit(1).execute()
                except Exception:
                    # Column missing — fall back to the pre-Phase-7 shape
                    cur = supabase_client.table("jobs").select(
                        "current_stage, status"
                    ).eq("video_id", self.video_id).order("started_at", desc=True).limit(1).execute()

                if cur.data:
                    prior_stage = cur.data[0].get("current_stage")
                    prior_status = cur.data[0].get("status")
                    try:
                        prior_retries = int(cur.data[0].get("retry_count") or 0)
                    except (TypeError, ValueError):
                        prior_retries = 0

                # Resume from a previously-failed/in-progress stage. The
                # stage list lets us pick the right index for skip-ahead.
                # PENDING is excluded — it just means "no work started yet",
                # so a resume from PENDING would be a no-op.
                _RESUMABLE = {"VALIDATING", "TRANSCRIBING_PLANNING", "REVIEWING", "RENDERING"}
                if (
                    prior_stage
                    and prior_stage in _RESUMABLE
                    and prior_status in ("FAILED", "RUNNING")
                ):
                    self._resume_from = prior_stage
                    logger.info(
                        "Phase 7 resume: video %s was at %s (status=%s, retries=%d); "
                        "skipping already-completed stages",
                        self.video_id, prior_stage, prior_status, prior_retries,
                    )

                # Reset the row to RUNNING so the status page doesn't show
                # a stale FAILED badge while we're re-running. retry_count
                # is preserved here — `retry_stage()` will increment it on
                # each subsequent failure. Wrapped in try/except so a
                # missing `retry_count` column on a pre-migration DB
                # doesn't kill the whole pipeline.
                #
                # Phase 7 fix: when resuming from a previously-in-progress
                # stage, do NOT regress `current_stage` back to PENDING —
                # the status page polls every 2s and would briefly see
                # PENDING before `transition("VALIDATING")` fires, which
                # makes the UI rewind from "Rendering Final Reels" back to
                # "Processing Your Video". Hold the prior stage until the
                # first real `transition()` call advances it forward.
                resume_stage = self._resume_from or "PENDING"
                try:
                    supabase_client.table("jobs").upsert({
                        "video_id": self.video_id,
                        "current_stage": resume_stage,
                        "status": "RUNNING",
                        "last_error": None,
                    }, on_conflict="video_id").execute()
                except Exception:
                    supabase_client.table("jobs").upsert({
                        "video_id": self.video_id,
                        "current_stage": resume_stage,
                        "status": "RUNNING",
                    }, on_conflict="video_id").execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not upsert initial jobs row for %s: %s", self.video_id, exc)

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

        Between attempts we sleep `RETRY_BACKOFF_SECONDS` to let transient
        issues (Gemini 503, OOM, etc) clear. Exceptions are caught and
        treated as a failed attempt — we don't want one Python crash to
        bypass the whole retry budget.
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
                logger.info(
                    "Retrying stage %s for %s in %ds (attempt %d → %d)",
                    stage, self.video_id, RETRY_BACKOFF_SECONDS, attempt, attempt + 1,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)

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

    # --- Stage wrappers (lazy imports keep the module import graph simple) ---
    async def _stage_validate(self, video_path: str) -> bool:
        from worker.stages.validate import validate_video
        return await validate_video(video_path)

    async def _stage_transcribe_plan(self, video_path: str) -> bool:
        from worker.stages.transcribe_plan import transcribe_and_plan
        return await transcribe_and_plan(self.video_id, video_path)

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
                reframe_ok = await reframe_video(video_path, reframed_path)
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
            if not await stitch_and_caption(self.video_id, stitch_input, output_path):
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

            # 3. Caption overlay: best-effort. On failure we still ship the
            #    caption-stripped video rather than nothing.
            final_path = output_path
            captioned_path = os.path.join(
                tempfile.gettempdir(), f"reel_{self.video_id}_captioned.mp4"
            )
            if await burn_captions(self.video_id, output_path, captioned_path):
                final_path = captioned_path

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
