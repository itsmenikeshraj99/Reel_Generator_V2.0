-- Migration 004: Fix missing updated_at column on videos
-- Restores the videos table to the intended source schema.
alter table videos
add column if not exists updated_at timestamp with time zone default current_timestamp;
