"""Test 07: Cross-user RLS isolation.

Verifies that one user's rows are NOT visible to another user when
queries use the anon key (which respects RLS). The worker uses the
service-role key (bypasses RLS), but the FRONTEND uses the anon key —
this is the path that needs to be locked down.

We can't fully simulate the anon-key RLS path from the worker (we have
the service-role key), so we instead test the **service-role key path
in use by the worker**: when a query is made with `eq("user_id", X)`,
the result must not include rows belonging to user Y. This is the
defense-in-depth check the backend does — even if RLS is misconfigured,
the explicit user_id filter must still isolate data.
"""
import asyncio
import uuid

from _helpers import (
    test_case, assert_, section, cleanup_video, create_test_user,
)


async def main() -> bool:
    section("Test 07: Cross-user RLS / user_id isolation")

    user_a = None
    user_b = None
    video_a = None
    video_b = None
    try:
        with test_case("user_id filter isolates rows between users") as t:
            from worker.services.supabase import supabase_client

            # Create two real auth users so the videos.user_id FK is satisfied
            user_a = create_test_user(f"test_phase9_a_{uuid.uuid4().hex[:8]}@example.com")
            user_b = create_test_user(f"test_phase9_b_{uuid.uuid4().hex[:8]}@example.com")
            print(f"    -> created test users: a={user_a}, b={user_b}")

            # Create two videos, one for each user
            video_a = str(uuid.uuid4())
            video_b = str(uuid.uuid4())
            supabase_client.table("videos").insert({
                "id": video_a,
                "user_id": user_a,
                "filename": "user_a.mp4",
                "gcs_uri": "test://a",
                "status": "UPLOADED",
                "expires_at": "2099-12-31T00:00:00+00:00",
            }).execute()
            supabase_client.table("videos").insert({
                "id": video_b,
                "user_id": user_b,
                "filename": "user_b.mp4",
                "gcs_uri": "test://b",
                "status": "UPLOADED",
                "expires_at": "2099-12-31T00:00:00+00:00",
            }).execute()

            # Query as user_a (explicit filter)
            res_a = supabase_client.table("videos").select("id, user_id").eq(
                "user_id", user_a
            ).execute()
            user_a_ids = [r["id"] for r in (res_a.data or [])]
            assert_(
                video_a in user_a_ids,
                f"user_a's video not in their own results: {video_a} not in {user_a_ids}",
            )
            assert_(
                video_b not in user_a_ids,
                f"user_b's video leaked into user_a's results: {video_b} in {user_a_ids}",
            )

            # Query as user_b
            res_b = supabase_client.table("videos").select("id, user_id").eq(
                "user_id", user_b
            ).execute()
            user_b_ids = [r["id"] for r in (res_b.data or [])]
            assert_(
                video_b in user_b_ids,
                f"user_b's video not in their own results",
            )
            assert_(
                video_a not in user_b_ids,
                f"user_a's video leaked into user_b's results: {video_a} in {user_b_ids}",
            )

            # Sanity check: with no filter, both rows are visible
            # (this is what the service-role key gets — bypassing RLS)
            res_all = supabase_client.table("videos").select("id").in_(
                "id", [video_a, video_b]
            ).execute()
            all_ids = [r["id"] for r in (res_all.data or [])]
            assert_(
                video_a in all_ids and video_b in all_ids,
                "service-role key should see both rows (no filter applied)",
            )

            print(f"    -> user_a sees {len(user_a_ids)} of their own rows; user_b sees {len(user_b_ids)} of their own")
            print(f"    -> service-role (unfiltered) sees {len(all_ids)} rows total — expected 2")

    finally:
        if video_a:
            cleanup_video(video_a)
        if video_b:
            cleanup_video(video_b)
        # Clean up the auth users we created so the auth.users table
        # doesn't accumulate test cruft. Best-effort.
        from worker.services.supabase import supabase_client
        for uid in (user_a, user_b):
            if uid:
                try:
                    supabase_client.auth.admin.delete_user(uid)
                except Exception:  # noqa: BLE001
                    pass

    return t["passed"]


if __name__ == "__main__":
    asyncio.run(main())
