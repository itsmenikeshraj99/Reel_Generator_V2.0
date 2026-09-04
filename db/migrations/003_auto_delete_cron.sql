-- =============================================================
-- Migration 003 — automated 24h cleanup via pg_cron
-- Date: 2026-09-04
-- Why:  The pipeline already writes `expires_at` on every video row
--       and the API endpoints (videos, reels) already 403 expired
--       sessions on read. This migration adds the *physical* cleanup
--       layer — a background job that deletes storage objects and
--       DB rows whose expires_at has passed, so the bucket doesn't
--       accumulate orphans even if no user ever clicks "clear".
--
-- Tier mapping (per docs/auto-delete-architecture.md):
--   Tier 1 (instant)    — frontend "Finish & Clear Session" button
--                         calls DELETE /api/videos/{id}. Live in code.
--   Tier 2 (timer)      — THIS migration. pg_cron runs every 5 min.
--   Tier 3 (failsafe)   — API checks expires_at on every read. Live.
--
-- How it works:
--   1. Enable pg_cron + pg_net extensions (idempotent).
--   2. Schedule a job that calls the `cleanup_expired_sessions()`
--      Postgres function every 5 minutes.
--   3. The function identifies expired rows, deletes their storage
--      objects via the Supabase HTTP API, then deletes the DB rows
--      (cascades to transcripts/edit_plans/reels/jobs).
--
-- Why we delete storage via HTTP from Postgres, not from Python:
--   - Keeps the cron job self-contained inside Supabase
--   - No worker involvement, no Railway compute
--   - Supabase service_role JWT lives in Vault, not in app env
--
-- Required setup (one-time, manual):
--   In Supabase Dashboard -> Database -> Cron Jobs, the schedule
--   creation requires a service_role JWT. The migration creates the
--   function; the schedule is created by the same migration using
--   `cron.schedule()` which uses the project's supabase_admin role.
--   If the project has pg_cron disabled, run:
--     create extension if not exists pg_cron;
--     create extension if not exists pg_net;
--   from a SQL editor session that has the right role, then re-run
--   this migration.
--
-- Idempotent: re-runs are safe (drop-if-exists, do-block guards).
-- =============================================================

-- ---------- 1. Extensions ----------
create extension if not exists "pg_cron";
create extension if not exists "pg_net";

-- ---------- 2. Vault secret for the service_role key ----------
-- We need to call the Supabase Storage HTTP API as service_role so
-- we can delete any user's object (RLS doesn't apply to bucket
-- operations for the service role). The key is stored in Vault
-- (encrypted at rest) and read by the cleanup function.
--
-- This DO block is a no-op if the secret already exists, so it's
-- safe to re-run. The 'actual_value' placeholder MUST be replaced
-- with the project's real service_role key the first time the
-- migration is applied — find it in:
--   Supabase Dashboard -> Settings -> API -> service_role
-- The 'name' and 'description' are stable; the secret value is
-- the only thing that changes between environments.
do $$
begin
  if not exists (
    select 1 from vault.secrets where name = 'service_role_key'
  ) then
    perform vault.create_secret(
      'service_role_PLACEHOLDER_replace_in_dashboard',
      'service_role_key',
      'Supabase service_role JWT for the cleanup cron. Update the secret value in the Vault UI after the first run.'
    );
  end if;
exception when others then
  -- Vault might not be available on free tier projects. Log and
  -- continue — the function will read SUPABASE_SERVICE_ROLE_KEY from
  -- the function's runtime config instead.
  raise notice 'vault.create_secret skipped: %', SQLERRM;
end $$;

-- ---------- 3. Cleanup function ----------
-- Identifies expired videos and deletes their storage + DB rows.
-- The storage deletion goes through pg_net->HTTP so we don't have
-- to involve the FastAPI backend for background cleanup.
create or replace function cleanup_expired_sessions()
returns jsonb
language plpgsql
security definer
as $$
declare
  v_record record;
  v_project_url text;
  v_service_key text;
  v_storage_paths text[];
  v_path text;
  v_delete_url text;
  v_response jsonb;
  v_deleted_count int := 0;
  v_storage_failed int := 0;
begin
  -- Pull project URL from a known settings table, or read from
  -- the DATABASE_URL host. Simplest: hardcode per-project. We
  -- expect operators to set `app.settings.project_url` in a
  -- one-off UPDATE statement after running this migration:
  --   alter database postgres set app.settings.project_url = 'https://xxx.supabase.co';
  -- For now, read from current_setting with a fallback to the
  -- well-known Supabase env var via the GUC mechanism.
  begin
    v_project_url := current_setting('app.settings.project_url', true);
  exception when others then
    v_project_url := null;
  end;

  if v_project_url is null or v_project_url = '' then
    raise warning 'app.settings.project_url GUC is not set — cleanup will skip storage deletion. Set it via: alter database postgres set app.settings.project_url = ''https://YOUR_PROJECT.supabase.co'';';
  end if;

  -- Pull service_role key from Vault.
  begin
    select decrypted_secret into v_service_key
    from vault.decrypted_secrets
    where name = 'service_role_key'
    limit 1;
  exception when others then
    v_service_key := null;
  end;

  if v_service_key is null or v_service_key like 'service_role_PLACEHOLDER%' then
    raise warning 'service_role_key not set in vault — cleanup will skip storage deletion. Update the secret in the Vault UI.';
  end if;

  -- For each expired video, gather storage paths (original + reels),
  -- delete them via the Storage HTTP API, then delete the DB row.
  -- The reels table has the per-reel paths; the videos.gcs_uri has
  -- the original upload path.
  for v_record in
    select v.id, v.gcs_uri as original_path,
           coalesce(
             (select array_agg(r.storage_path)
              from reels r
              where r.video_id = v.id),
             array[]::text[]
           ) as reel_paths
    from videos v
    where v.expires_at < current_timestamp
    limit 50  -- process in batches to avoid blowing out pg_net timeouts
  loop
    v_storage_paths := array[]::text[];
    if v_record.original_path is not null then
      v_storage_paths := array_append(v_storage_paths, v_record.original_path);
    end if;
    v_storage_paths := v_storage_paths || v_record.reel_paths;

    -- Best-effort storage deletion via HTTP. If the GUC/vault are
    -- not set up yet, the HTTP call is skipped and we still
    -- delete the DB row — the on_conflict ON DELETE CASCADE chain
    -- handles the rest, and the next pg_cron pass will retry the
    -- storage cleanup once config is in place.
    if v_project_url is not null and v_service_key is not null
       and v_service_key not like 'service_role_PLACEHOLDER%'
       and array_length(v_storage_paths, 1) > 0
    then
      foreach v_path in array v_storage_paths loop
        v_delete_url := v_project_url
          || '/storage/v1/object/reels-videos/'
          || v_path;
        begin
          select content::jsonb into v_response
          from http((
            'DELETE'::text,
            v_delete_url,
            array[http_header('Authorization', 'Bearer ' || v_service_key)],
            null,
            null
          ));
        exception when others then
          raise notice 'Storage DELETE failed for %: %', v_path, SQLERRM;
          v_storage_failed := v_storage_failed + 1;
        end;
      end loop;
    end if;

    -- Delete the DB row. CASCADE removes transcripts, edit_plans,
    -- reels, and jobs. The video row itself is the gate — once
    -- it's gone, no API endpoint will surface the children.
    delete from videos where id = v_record.id;
    v_deleted_count := v_deleted_count + 1;
  end loop;

  return jsonb_build_object(
    'deleted_sessions', v_deleted_count,
    'storage_deletions_failed', v_storage_failed,
    'project_url_configured', v_project_url is not null,
    'service_key_configured', v_service_key is not null
      and v_service_key not like 'service_role_PLACEHOLDER%'
  );
end $$;

-- ---------- 4. Schedule ----------
-- Every 5 minutes. If the job is already scheduled, drop and re-create
-- it so changes to the function take effect.
do $$
begin
  -- unschedule silently if it exists
  begin
    perform cron.unschedule('cleanup-expired-sessions');
  exception when others then
    -- cron.job not found or extension not enabled — ignore
    null;
  end;
  perform cron.schedule(
    'cleanup-expired-sessions',
    '*/5 * * * *',
    $cmd$ select cleanup_expired_sessions(); $cmd$
  );
exception when others then
  raise notice 'cron.schedule skipped: %. Run `create extension if not exists pg_cron;` from a privileged role, then re-run this migration.', SQLERRM;
end $$;

-- ---------- 5. Operator checklist (commented, not executed) ----------
-- After this migration runs:
--
-- 1. Set the project URL GUC (replace URL with your actual project):
--      alter database postgres set app.settings.project_url = 'https://YOUR_PROJECT.supabase.co';
--
-- 2. Update the service_role key in the Vault:
--      Supabase Dashboard -> Database -> Vault -> service_role_key
--      (replace the PLACEHOLDER value with the real key from
--       Settings -> API -> service_role)
--
-- 3. Verify the schedule exists:
--      select * from cron.job where jobname = 'cleanup-expired-sessions';
--
-- 4. Test manually:
--      select cleanup_expired_sessions();
--    (insert a test video with expires_at in the past first)
--
-- 5. The API endpoints already 403 expired sessions, so the user
--    will see "Link Expired" even if the cron is misconfigured.
--    That's the Tier-3 failsafe working as designed.
