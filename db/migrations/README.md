# Database migrations

This directory holds **forward-only** schema migrations. The base schema lives in
[`../schema.sql`](../schema.sql) — apply that first, then apply each numbered
migration in order.

## How to apply

1. Open the Supabase Dashboard.
2. Go to **SQL Editor** (left sidebar).
3. Paste the contents of the migration file.
4. Click **Run**.
5. (Optional) verify with `select * from jobs limit 1;`.

Every migration is **idempotent** — re-running it is safe. Constraints are
added with `drop constraint if exists … add constraint …` so partial state
won't break a re-run.

## Naming convention

```
NNN_short_description.sql
```

- `NNN` — 3-digit sequence, applied in order.
- `short_description` — snake_case, ~30 chars max.

## What's in here

| File                                              | Adds                                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `002_status_constraints_and_triggers.sql`         | CHECK constraints on `videos.status`, `jobs.status`, `jobs.current_stage`, `edit_plans.status`; `updated_at` triggers on `jobs`, `videos`, `reels`. |
| `003_auto_delete_cron.sql`                        | 24-hour auto-delete via `pg_cron`. Adds `cleanup_expired_sessions()` function and a 5-min schedule. Storage objects are deleted via the Supabase HTTP API; DB rows cascade. See the operator checklist at the bottom of the file for one-time Vault + GUC setup. |
