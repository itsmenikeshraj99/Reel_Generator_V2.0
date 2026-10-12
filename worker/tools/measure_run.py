"""Measure one clip through the real pipeline: stage times and Gemini usage.

Local tool, not part of production. It creates a throwaway test user and
video row in the Supabase project configured in worker/.env, runs
`Pipeline.run()` exactly as the worker does, prints stage durations and
every Gemini call (model, seconds, tokens), then deletes everything it
created.

Usage (from the worker/ folder):
    ./venv/Scripts/python.exe tools/measure_run.py path/to/clip.mp4
    ./venv/Scripts/python.exe tools/measure_run.py clip.mp4 --json result.json

The clip must be under 50 MB (Supabase Free per-file cap). It prints
tokens, not dollars: multiply by the current price of the models used.
Peak memory is not measured.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

MAX_CLIP_BYTES = 50 * 1024 * 1024
_WORKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summarize(
    stage_marks: List[Tuple[str, float]],
    end_ts: float,
    gemini_calls: List[Dict[str, Any]],
    upload_seconds: float = 0.0,
) -> Dict[str, Any]:
    """Turn raw marks and call records into the printed summary.

    `stage_marks` is [(stage_name, start_timestamp), ...] in order; a
    stage lasts until the next mark (the last one until `end_ts`).
    Each gemini call dict has: model, seconds, ok, prompt_tokens,
    output_tokens, thought_tokens, total_tokens.
    """
    stages = []
    for i, (name, start) in enumerate(stage_marks):
        stop = stage_marks[i + 1][1] if i + 1 < len(stage_marks) else end_ts
        stages.append({"stage": name, "seconds": round(max(0.0, stop - start), 2)})

    def _sum(key: str, calls: List[Dict[str, Any]]) -> int:
        return sum(int(c.get(key) or 0) for c in calls)

    ok_calls = [c for c in gemini_calls if c.get("ok")]
    failed_calls = [c for c in gemini_calls if not c.get("ok")]
    by_model: Dict[str, Dict[str, int]] = {}
    for c in ok_calls:
        m = by_model.setdefault(
            c.get("model", "?"),
            {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0},
        )
        m["calls"] += 1
        m["prompt_tokens"] += int(c.get("prompt_tokens") or 0)
        m["output_tokens"] += int(c.get("output_tokens") or 0)
        m["thought_tokens"] += int(c.get("thought_tokens") or 0)

    return {
        "stages": stages,
        "stage_seconds_total": round(sum(s["seconds"] for s in stages), 2),
        "gemini": {
            "ok_calls": len(ok_calls),
            "failed_calls": len(failed_calls),
            "prompt_tokens": _sum("prompt_tokens", ok_calls),
            "output_tokens": _sum("output_tokens", ok_calls),
            "thought_tokens": _sum("thought_tokens", ok_calls),
            "total_tokens": _sum("total_tokens", ok_calls),
            "call_seconds": round(sum(float(c.get("seconds") or 0) for c in gemini_calls), 2),
            "file_upload_seconds": round(upload_seconds, 2),
            "by_model": by_model,
        },
    }


def format_summary(summary: Dict[str, Any], outcome: str) -> str:
    lines = ["", "=" * 60, f"Outcome: {outcome}", "=" * 60, "Stage times:"]
    for s in summary["stages"]:
        lines.append(f"  {s['stage']:<26}{s['seconds']:>9.1f} s")
    lines.append(f"  {'TOTAL':<26}{summary['stage_seconds_total']:>9.1f} s")
    g = summary["gemini"]
    lines += [
        "",
        "Gemini:",
        f"  successful calls : {g['ok_calls']}",
        f"  failed attempts  : {g['failed_calls']} (fallback chain)",
        f"  input tokens     : {g['prompt_tokens']}",
        f"  output tokens    : {g['output_tokens']}",
        f"  thinking tokens  : {g['thought_tokens']}",
        f"  total tokens     : {g['total_tokens']}",
        f"  time in calls    : {g['call_seconds']} s (file upload {g['file_upload_seconds']} s)",
    ]
    for model, m in g["by_model"].items():
        lines.append(
            f"    {model}: {m['calls']} call(s), in {m['prompt_tokens']}, "
            f"out {m['output_tokens']}, thinking {m['thought_tokens']}"
        )
    return "\n".join(lines)


def _usage(response: Any) -> Dict[str, int]:
    u = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(u, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(u, "candidates_token_count", 0) or 0,
        "thought_tokens": getattr(u, "thoughts_token_count", 0) or 0,
        "total_tokens": getattr(u, "total_token_count", 0) or 0,
    }


async def _measure(clip_path: str) -> Tuple[Dict[str, Any], str]:
    from worker.gemini.client import gemini_client
    from worker.pipeline import Pipeline
    from worker.services.storage import worker_storage
    from worker.services.supabase import supabase_client

    stage_marks: List[Tuple[str, float]] = []
    gemini_calls: List[Dict[str, Any]] = []
    upload = {"seconds": 0.0, "name": None}

    # -- instrumentation (inside this tool only; production code is untouched)
    models = gemini_client.client.models
    original_generate = models.generate_content

    def timed_generate(*args: Any, **kwargs: Any) -> Any:
        t0 = time.time()
        model = kwargs.get("model", "?")
        try:
            resp = original_generate(*args, **kwargs)
        except Exception:
            gemini_calls.append({"model": model, "seconds": time.time() - t0, "ok": False})
            raise
        gemini_calls.append(
            {"model": model, "seconds": time.time() - t0, "ok": True, **_usage(resp)}
        )
        return resp

    models.generate_content = timed_generate

    original_upload = gemini_client.upload_video

    def timed_upload(file_path: str) -> Any:
        t0 = time.time()
        f = original_upload(file_path)
        upload["seconds"] += time.time() - t0
        upload["name"] = getattr(f, "name", None)
        return f

    gemini_client.upload_video = timed_upload

    original_transition = Pipeline.transition

    async def timed_transition(self: Any, new_stage: str) -> bool:
        stage_marks.append((new_stage, time.time()))
        return await original_transition(self, new_stage)

    Pipeline.transition = timed_transition

    # -- throwaway user + video row + uploaded clip
    user_id: Optional[str] = None
    video_id = str(uuid.uuid4())
    storage_path: Optional[str] = None
    outcome = "unknown"
    t_start = time.time()
    try:
        email = f"measure_{uuid.uuid4().hex[:8]}@example.com"
        user_id = supabase_client.auth.admin.create_user({
            "email": email,
            "password": uuid.uuid4().hex + "Aa1!",
            "email_confirm": True,
        }).user.id
        filename = os.path.basename(clip_path).replace(" ", "_")
        storage_path = f"{user_id}/{video_id}_{filename}"
        with open(clip_path, "rb") as f:
            worker_storage.upload_file(storage_path, f.read(), upsert=False)
        supabase_client.table("videos").insert({
            "id": video_id,
            "user_id": user_id,
            "filename": os.path.basename(clip_path),
            "status": "UPLOADED",
            "gcs_uri": storage_path,
            "expires_at": "2099-12-31T00:00:00+00:00",
        }).execute()

        # Same job-row initialisation the worker's /process endpoint does.
        supabase_client.table("jobs").upsert({
            "video_id": video_id,
            "current_stage": "PENDING",
            "status": "RUNNING",
            "last_error": None,
        }, on_conflict="video_id").execute()

        t_run = time.time()
        stage_marks.append(("DOWNLOAD", t_run))
        await Pipeline(video_id).run()
        end_ts = time.time()

        job = supabase_client.table("jobs").select(
            "status, current_stage, last_error"
        ).eq("video_id", video_id).order("started_at", desc=True).limit(1).execute()
        row = job.data[0] if job.data else {}
        outcome = f"{row.get('status')} at {row.get('current_stage')}"
        if row.get("last_error"):
            outcome += f" ({row['last_error'][:200]})"
        return summarize(stage_marks, end_ts, gemini_calls, upload["seconds"]), outcome
    finally:
        models.generate_content = original_generate
        gemini_client.upload_video = original_upload
        Pipeline.transition = original_transition
        _cleanup(video_id, user_id, storage_path, upload["name"])
        print(f"(total wall time incl. setup/cleanup: {time.time() - t_start:.1f} s)")


def _cleanup(
    video_id: str, user_id: Optional[str], storage_path: Optional[str], gemini_file: Optional[str]
) -> None:
    """Best effort: remove everything the run created."""
    from worker.gemini.client import gemini_client
    from worker.services.storage import worker_storage
    from worker.services.supabase import supabase_client

    for table in ("reels", "edit_plans", "transcripts", "jobs"):
        try:
            supabase_client.table(table).delete().eq("video_id", video_id).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup: {table}: {exc}")
    try:
        supabase_client.table("videos").delete().eq("id", video_id).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"cleanup: videos: {exc}")
    for path in (storage_path, f"reels/{video_id}.mp4"):
        if path:
            try:
                worker_storage.delete_file(path)
            except Exception as exc:  # noqa: BLE001
                print(f"cleanup: storage {path}: {exc}")
    if gemini_file:
        try:
            gemini_client.client.files.delete(name=gemini_file)
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup: gemini file {gemini_file}: {exc}")
    if user_id:
        try:
            supabase_client.auth.admin.delete_user(user_id)
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup: test user {user_id}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure one clip through the pipeline.")
    parser.add_argument("clip", help="path to a video under 50 MB")
    parser.add_argument("--json", dest="json_path", help="also write the summary to this file")
    args = parser.parse_args()

    if not os.path.isfile(args.clip):
        print(f"File not found: {args.clip}")
        return 2
    if os.path.getsize(args.clip) > MAX_CLIP_BYTES:
        print("Clip is over 50 MB (Supabase Free per-file cap). Use a smaller clip.")
        return 2

    # worker/.env is read relative to the working directory.
    os.chdir(_WORKER_ROOT)
    if _WORKER_ROOT not in sys.path:
        sys.path.insert(0, _WORKER_ROOT)

    summary, outcome = asyncio.run(_measure(os.path.abspath(args.clip)))
    print(format_summary(summary, outcome))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump({"outcome": outcome, **summary}, f, indent=2)
        print(f"\nSaved {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
