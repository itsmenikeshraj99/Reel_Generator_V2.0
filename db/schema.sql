-- =============================================================
-- Reels Generator — Supabase schema
-- Apply this in Supabase Dashboard -> SQL Editor
-- Safe to re-run (idempotent)
-- =============================================================

-- Required for gen_random_uuid()
create extension if not exists "pgcrypto";

-- Videos Table
create table if not exists videos (
    id uuid primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    filename text not null,
    status text not null,
    gcs_uri text not null,
    expires_at timestamp with time zone not null,
    created_at timestamp with time zone default current_timestamp
);

-- Transcripts Table
create table if not exists transcripts (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    full_text text not null,
    -- Word-level timestamps from Gemini. JSONB array of {text, start, end}.
    -- Used for caption overlay (Phase 6). Null is fine for legacy rows.
    words jsonb,
    created_at timestamp with time zone default current_timestamp
);

-- One transcript per video (worker upserts).
create unique index if not exists uq_transcripts_video_id on transcripts (video_id);

-- Edit Plans Table (Candidates)
create table if not exists edit_plans (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    candidate_index integer not null,
    segments jsonb not null,
    status text not null,
    hook_score float,
    overall_score float,
    feedback text,
    created_at timestamp with time zone default current_timestamp
);

-- Reels Table (Final output clips)
create table if not exists reels (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    edit_plan_id uuid references edit_plans(id),
    storage_path text not null,
    public_url text,
    title text,
    meta jsonb default '{}'::jsonb,
    created_at timestamp with time zone default current_timestamp
);

-- Jobs Table (Pipeline Orchestration)
create table if not exists jobs (
    id uuid primary key default gen_random_uuid(),
    video_id uuid references videos(id) on delete cascade,
    current_stage text not null,
    status text not null,
    last_error text,
    retry_count int default 0,                   -- Phase 7: stage-level retry
    started_at timestamp with time zone default current_timestamp,
    updated_at timestamp with time zone default current_timestamp
);

-- One job row per video. Worker uses this for `on_conflict=video_id` upsert.
create unique index if not exists uq_jobs_video_id on jobs (video_id);

-- =============================================================
-- Indexes (hot paths)
-- =============================================================
create index if not exists idx_videos_user_id        on videos (user_id);
create index if not exists idx_videos_expires_at     on videos (expires_at);
create index if not exists idx_transcripts_video_id  on transcripts (video_id);
create index if not exists idx_edit_plans_video_id   on edit_plans (video_id, status);
create index if not exists idx_reels_video_id        on reels (video_id);
create index if not exists idx_jobs_video_id         on jobs (video_id, started_at desc);

-- =============================================================
-- Row Level Security
-- =============================================================
alter table videos     enable row level security;
alter table transcripts enable row level security;
alter table edit_plans enable row level security;
alter table reels      enable row level security;
alter table jobs       enable row level security;

-- Policies (drop-and-recreate so this is idempotent)
drop policy if exists "Users can manage their own videos"      on videos;
drop policy if exists "Users can manage their own transcripts" on transcripts;
drop policy if exists "Users can manage their own edit plans"  on edit_plans;
drop policy if exists "Users can manage their own reels"       on reels;
drop policy if exists "Users can manage their own jobs"        on jobs;

create policy "Users can manage their own videos" on videos for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "Users can manage their own transcripts" on transcripts for all
  using (exists (select 1 from videos v where v.id = transcripts.video_id and v.user_id = auth.uid()));

create policy "Users can manage their own edit plans" on edit_plans for all
  using (exists (select 1 from videos v where v.id = edit_plans.video_id and v.user_id = auth.uid()));

create policy "Users can manage their own reels" on reels for all
  using (exists (select 1 from videos v where v.id = reels.video_id and v.user_id = auth.uid()));

create policy "Users can manage their own jobs" on jobs for all
  using (exists (select 1 from videos v where v.id = jobs.video_id and v.user_id = auth.uid()));

-- =============================================================
-- Storage policies for the 'reels-videos' bucket
-- Recommended setup: bucket is PUBLIC for the MVP (signed URLs in production).
-- Path convention: ALL objects live under `<user_id>/...` so the storage
-- RLS policy `auth.uid() = split_part(name, '/', 1)` is the single rule
-- for both source videos and generated reels.
-- =============================================================

-- Public read so the gallery can play the videos
drop policy if exists "Public read reels-videos" on storage.objects;
create policy "Public read reels-videos"
  on storage.objects for select
  using (bucket_id = 'reels-videos');

-- Authenticated users can upload to their own folder (path: <user_id>/...)
drop policy if exists "Owner upload reels-videos" on storage.objects;
create policy "Owner upload reels-videos"
  on storage.objects for insert
  with check (
    bucket_id = 'reels-videos'
    and auth.role() = 'authenticated'
    and auth.uid()::text = split_part(name, '/', 1)
  );

-- Owners can update their own files
drop policy if exists "Owner update reels-videos" on storage.objects;
create policy "Owner update reels-videos"
  on storage.objects for update
  using (
    bucket_id = 'reels-videos'
    and auth.uid()::text = split_part(name, '/', 1)
  )
  with check (
    bucket_id = 'reels-videos'
    and auth.uid()::text = split_part(name, '/', 1)
  );

-- Owners can delete their own files
drop policy if exists "Owner delete reels-videos" on storage.objects;
create policy "Owner delete reels-videos"
  on storage.objects for delete
  using (
    bucket_id = 'reels-videos'
    and auth.uid()::text = split_part(name, '/', 1)
  );
