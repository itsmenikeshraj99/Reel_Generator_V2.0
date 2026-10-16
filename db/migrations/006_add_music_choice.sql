-- Migration 006: the user's "add music" choice, made on the upload page.
-- One boolean per video, off by default; existing rows and queries are unaffected. Idempotent.
alter table videos
add column if not exists add_music boolean not null default false;
