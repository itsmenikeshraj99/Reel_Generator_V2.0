"""Shared test helpers for Phase 9 regression tests.

These tests are intentionally framework-free — they call directly into
the worker modules and Supabase, and return True/False pass/fail. The
caller (run_all_tests.py) prints the summary and exits with the right
code so a CI job can pick it up.
"""
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Optional

# Ensure the worker package is importable when running from the tests/
# directory. Without this, `from worker.stages.reframe import …` would
# fail with ModuleNotFoundError.
_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER_ROOT = os.path.dirname(_HERE)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)

# Quiet down the noisy HTTP logger that the Supabase client uses —
# tests don't need a transcript of every request.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Lazy imports — keep them inside test functions where possible so a
# missing optional dep (e.g. mediapipe) doesn't break the whole runner.


def make_video_row(filename: str, gcs_uri: str, user_id: Optional[str] = None) -> str:
    """Create a `videos` row owned by a test user. Returns the video_id.

    If `user_id` is omitted, a test user is created via the Supabase
    admin API (so the videos.user_id FK to auth.users is satisfied).
    Pass an explicit user_id for tests that want to share a single
    test user across multiple videos.
    """
    from worker.services.supabase import supabase_client

    if user_id is None:
        user_id = create_test_user(f"test_video_{uuid.uuid4().hex[:8]}@example.com")

    # The live `videos` table has `id uuid primary key` WITHOUT a
    # default — so we must generate one client-side. The schema.sql
    # on disk adds the default; live DB hasn't been migrated yet.
    video_id = str(uuid.uuid4())
    res = supabase_client.table("videos").insert({
        "id": video_id,
        "user_id": user_id,
        "filename": filename,
        "gcs_uri": gcs_uri,
        "status": "UPLOADED",
        "expires_at": "2099-12-31T00:00:00+00:00",
    }).execute()
    return res.data[0]["id"]


def create_test_user(email: str) -> str:
    """Create a user in auth.users via the Supabase admin API. Returns
    the user_id. Used by tests that need rows satisfying the
    videos.user_id FK.
    """
    from worker.services.supabase import supabase_client
    res = supabase_client.auth.admin.create_user({
        "email": email,
        "password": "test_password_for_phase9_only",
        "email_confirm": True,  # skip email verification
    })
    return res.user.id


def cleanup_video(video_id: str) -> None:
    """Delete a test video row + its dependent rows (jobs, transcripts,
    edit_plans, reels). Best-effort — swallows errors so a partial
    cleanup doesn't mask the real test failure."""
    from worker.services.supabase import supabase_client
    for table in ("reels", "edit_plans", "transcripts", "jobs", "videos"):
        try:
            supabase_client.table(table).delete().eq(
                "video_id", video_id
            ).execute()
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def test_case(name: str):
    """Tiny test-runner helper. Prints PASS/FAIL and tracks the result.

    Usage:
        with test_case("my test") as t:
            assert 1 == 1
            t.pass_()  # optional — implicit pass on no exception
    """
    state = {"passed": False, "error": None}
    try:
        yield state
        if state["error"] is None:
            state["passed"] = True
    except AssertionError as exc:
        state["error"] = f"AssertionError: {exc}"
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if state["passed"]:
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name} — {state['error']}")


def assert_(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


def fresh_uuid() -> str:
    return str(uuid.uuid4())


# ANSI color codes — keep the output readable when running the suite
# in a terminal. Stripped automatically if output is redirected.
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def section(title: str) -> None:
    print(f"\n{C.BOLD}{'=' * 60}{C.RESET}")
    print(f"{C.BOLD}{title}{C.RESET}")
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
