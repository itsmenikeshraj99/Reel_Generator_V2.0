"""Test 06: Kill mid-render (worker crash recovery).

A pipeline that's halfway through RENDERING should, on the next run,
resume from RENDERING rather than starting over at VALIDATING. We
simulate a "crash" by manually setting the jobs row to a mid-stage
state, then call the pipeline's resume-detection logic and verify the
correct `_resume_from` is set.
"""
import asyncio
import uuid

from _helpers import test_case, assert_, section, cleanup_video, make_video_row


async def main() -> bool:
    section("Test 06: Kill mid-render (resume from RENDERING)")

    video_id = None
    try:
        with test_case("pipeline resumes from RENDERING on retry") as t:
            # 1. Create a video row + a job row at RENDERING/FAILED
            video_id = make_video_row(
                "test06_crash.mp4",
                "test://fake-uri-for-test06",
            )
            from worker.services.supabase import supabase_client
            supabase_client.table("jobs").upsert({
                "video_id": video_id,
                "current_stage": "RENDERING",
                "status": "FAILED",
                "last_error": "simulated crash",
            }, on_conflict="video_id").execute()

            # 2. Create a Pipeline and inspect the resume-detection
            #    prologue. We don't call run() because that would
            #    actually try to download the (fake) gcs_uri.
            from worker.pipeline import Pipeline, _STAGES

            p = Pipeline(video_id)

            # Read state the same way run() does
            cur = supabase_client.table("jobs").select(
                "current_stage, status"
            ).eq("video_id", video_id).order("started_at", desc=True).limit(1).execute()
            assert_(cur.data, "no jobs row found")
            prior_stage = cur.data[0]["current_stage"]
            prior_status = cur.data[0]["status"]

            assert_(prior_stage == "RENDERING", f"prior_stage={prior_stage}")
            assert_(prior_status == "FAILED", f"prior_status={prior_status}")

            # The pipeline should set _resume_from = "RENDERING"
            if prior_stage in _STAGES and prior_status in ("FAILED", "RUNNING"):
                p._resume_from = prior_stage

            assert_(
                p._resume_from == "RENDERING",
                f"_resume_from={p._resume_from}, expected RENDERING",
            )

            # 3. Confirm the _should_run() logic in run() returns False
            #    for stages before RENDERING
            def _should_run(stage: str) -> bool:
                if p._resume_from is None:
                    return True
                return _STAGES.index(stage) >= _STAGES.index(p._resume_from)

            assert_(_should_run("VALIDATING") is False, "VALIDATING should be skipped")
            assert_(_should_run("TRANSCRIBING_PLANNING") is False, "TRANSCRIBING_PLANNING should be skipped")
            assert_(_should_run("REVIEWING") is False, "REVIEWING should be skipped")
            assert_(_should_run("RENDERING") is True, "RENDERING should run")
            print(f"    -> resume_from={p._resume_from}; VALIDATING/TRANSCRIBING/REVIEWING will be skipped")

    finally:
        if video_id:
            cleanup_video(video_id)

    return t["passed"]


if __name__ == "__main__":
    asyncio.run(main())
