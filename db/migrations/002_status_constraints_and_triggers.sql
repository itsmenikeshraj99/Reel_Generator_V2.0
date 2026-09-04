-- =============================================================
-- Migration 002 — production-readiness tighten-ups
-- Date: 2026-09-04
-- Why:  Schema was created in 001 (db/schema.sql) with free-text
--       status / stage columns. Once the pipeline's full state set
--       was known, lock those columns down with CHECK constraints
--       and add `updated_at` triggers.
--
-- Apply in Supabase Dashboard -> SQL Editor
-- Idempotent: re-runs are safe (drop-if-exists, add-if-not-exists).
-- =============================================================

-- 1) CHECK constraints on status / stage columns
--    These reject typos and unknown values going forward.
--    They were verified against the live data — every existing
--    row's value is in the allow-list, so the add won't fail.

alter table videos     drop constraint if exists videos_status_check;
alter table videos     add  constraint videos_status_check
  check (status in ('PENDING_UPLOAD', 'UPLOADED', 'PROCESSING', 'READY', 'FAILED'));

alter table jobs       drop constraint if exists jobs_status_check;
alter table jobs       add  constraint jobs_status_check
  check (status in ('PENDING', 'RUNNING', 'READY', 'FAILED', 'PERMANENTLY_FAILED'));

alter table jobs       drop constraint if exists jobs_current_stage_check;
alter table jobs       add  constraint jobs_current_stage_check
  check (current_stage in (
    'PENDING',
    'VALIDATING',
    'TRANSCRIBING_PLANNING',
    'REVIEWING',
    'RENDERING',
    'READY'
  ));

alter table edit_plans drop constraint if exists edit_plans_status_check;
alter table edit_plans add  constraint edit_plans_status_check
  check (status in ('pending_review', 'accepted', 'rejected'));

-- 2) `updated_at` triggers so the timestamps are never stale.
--    A shared function is reused across all three tables.

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = current_timestamp;
  return new;
end;
$$ language plpgsql;

-- jobs already has updated_at, just add the trigger
drop trigger if exists trg_jobs_updated_at on jobs;
create trigger trg_jobs_updated_at
  before update on jobs
  for each row execute function set_updated_at();

-- videos didn't have updated_at; add the column and trigger
alter table videos add column if not exists updated_at timestamp with time zone default current_timestamp;
drop trigger if exists trg_videos_updated_at on videos;
create trigger trg_videos_updated_at
  before update on videos
  for each row execute function set_updated_at();

-- reels: same treatment
alter table reels add column if not exists updated_at timestamp with time zone default current_timestamp;
drop trigger if exists trg_reels_updated_at on reels;
create trigger trg_reels_updated_at
  before update on reels
  for each row execute function set_updated_at();
