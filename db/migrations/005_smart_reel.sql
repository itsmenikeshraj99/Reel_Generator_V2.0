-- Migration 005: columns for the smart reel pipeline
-- All additions are nullable and idempotent; existing rows and queries are unaffected.

-- Sentence index built from the word-timed transcript:
-- JSONB array of {id, start, end, text, first_word, last_word}.
alter table transcripts
add column if not exists sentences jsonb;

-- The planner's output, its shape, and the verifier's final verdict.
alter table edit_plans
add column if not exists plan jsonb;

alter table edit_plans
add column if not exists shape text;

alter table edit_plans
add column if not exists verifier jsonb;

alter table edit_plans drop constraint if exists edit_plans_shape_check;
alter table edit_plans add constraint edit_plans_shape_check
  check (shape is null or shape in ('single_stretch', 'montage'));
