# Deploy to Railway (backend + worker) + Vercel (frontend)

This is a **demo/portfolio** deployment. The backend and worker run on
[Railway](https://railway.app); the frontend runs on
[Vercel](https://vercel.com). Supabase is already live.

**Why Railway?**

- **Free tier:** $5 credit/month ≈ 500 hours. Backend (low-traffic API) +
  worker (one-at-a-time, idle between uploads) fit comfortably.
- **No credit card** required for the trial — RuPay works.
- **Docker-based:** our existing Dockerfiles work as-is.
- **GitHub auto-deploy:** push to `main` → automatic rebuild + redeploy.
- **No cold-start sleep** like Render's free tier.

**Why Vercel for the frontend?**

- Free tier, no card required, custom domain support.
- Next.js 16 detected automatically — no Dockerfile needed (we keep the
  Dockerfile for portability, but Vercel ignores it).

**What this doesn't give you** (Phase 11 hardening if you outgrow the demo)

- No Cloud Tasks / SQS queue (backend calls worker via HTTPS directly —
  fine for a demo; the in-process 3x retry covers transient failures).
- No Secret Manager (env vars live in Railway's dashboard — fine for a
  single dev; rotate immediately if anyone else gets dashboard access).
- No custom domain + CDN (auto-generated `*.up.railway.app` URL works
  fine for portfolio).
- No monitoring / alerting (check the Railway logs tab).
- No Postgres connection pooler (the Supabase JS client opens a new
  connection per request — fine until ~50 concurrent users).

---

## 0. Prerequisites

- A GitHub account (you have one — repo is at
  <https://github.com/itsmenikeshraj99/Reel_Generator_V2.0>)
- A Supabase project (you already have one — see `db/schema.sql`)
- A Gemini API key (<https://aistudio.google.com/app/apikey>)
- A Railway account (sign up at <https://railway.app> with GitHub — free
  trial, no card required)
- A Vercel account (sign up at <https://vercel.com> with GitHub)
- About 20 minutes

> If you already pushed to GitHub, skip step 1. The repo is already there.

---

## 1. Sanity-check your Supabase project

If you haven't yet, make sure `db/schema.sql` has been run in the
Supabase SQL Editor for your project. The schema creates the `videos`,
`jobs`, `reels`, and `transcripts` tables, plus the RLS policies and
the `reels-videos` storage bucket. Apply the optional
`db/migrations/002_status_constraints_and_triggers.sql` migration too —
it's a CHECK constraint + `updated_at` triggers, idempotent.

---

## 2. Sign up on Railway

1. Go to <https://railway.app>
2. **Login with GitHub**
3. You'll land on the dashboard. **No project yet — we'll create one.**

Railway gives you $5 of free usage per month without a credit card. For
this app (low traffic, one-at-a-time worker) you'll stay well under
that. When you exceed it, the service pauses — no surprise charges.

---

## 3. Create the Railway project

1. Click **+ New Project** → **Deploy from GitHub repo**
2. Select **`itsmenikeshraj99/Reel_Generator_V2.0`**
3. Railway creates a single service from the repo root and tries to
   build it. **It will fail** because the repo root has no Dockerfile.
   That's expected — we'll add two more services next.

### 3a. Add the backend service

1. In the project canvas, click **+ New** → **GitHub Repo** → same repo
2. Click the new service to open it
3. **Settings** tab:
   - **Name:** `reels-backend`
   - **Root Directory:** `backend`
   - **Watch Paths:** `backend/**` (so worker-only commits don't trigger
     a backend rebuild)
4. **Variables** tab — add these (use the values from your local
   `backend/.env`; the service_role key and Gemini key come from
   Supabase / Google AI Studio):

   | Variable                  | Value                                  |
   | ------------------------- | -------------------------------------- |
   | `SUPABASE_URL`            | `https://owwojzonjbnnumqukshs.supabase.co` |
   | `SUPABASE_KEY`            | `<your service_role key>`              |
   | `SUPABASE_JWT_SECRET`     | `<your JWT secret>`                    |
   | `STORAGE_BUCKET`          | `reels-videos`                         |
   | `GEMINI_API_KEY`          | `<your Gemini key>`                    |
   | `GEMINI_MODEL`            | `gemini-2.5-flash`                     |
   | `WORKER_URL`              | `https://PLACEHOLDER.railway.internal/process` (we'll update after worker deploy) |
   | `WORKER_SHARED_SECRET`    | `<a long random string — see step 5>`  |
   | `MAX_VIDEO_SIZE_MB`       | `500`                                  |
   | `FRONTEND_ORIGIN`         | `https://example.com` (placeholder, update after Vercel deploy) |

5. **Settings** tab → **Deploy**:
   - **Build Command:** *(leave blank — Dockerfile is auto-detected)*
   - **Healthcheck Path:** `/health`
   - **Healthcheck Timeout:** `100` seconds
   - **Restart Policy Type:** `ON_FAILURE`
   - **Max Restart Retries:** `5`

6. Click **Deploy** (or it may auto-deploy on first save). The first
   build takes ~2–3 minutes (Python venv + small image).
7. When the deploy succeeds, the **Settings** tab shows a **Domains**
   section with a URL like `reels-backend-production-xxxx.up.railway.app`.
   **Copy it** — that's `BACKEND_URL`.

   ```bash
   export BACKEND_URL="https://reels-backend-production-xxxx.up.railway.app"
   ```

8. **Important:** Railway private networking uses `*.railway.internal`
   hostnames between services in the same project. For now, leave
   `WORKER_URL` as a placeholder; we'll wire it after the worker deploys.

### 3b. Add the worker service

1. In the same project canvas, **+ New** → **GitHub Repo** → same repo
2. Click the new service
3. **Settings** tab:
   - **Name:** `reels-worker`
   - **Root Directory:** `worker`
   - **Watch Paths:** `worker/**`
4. **Variables** tab:

   | Variable                  | Value                                  |
   | ------------------------- | -------------------------------------- |
   | `SUPABASE_URL`            | same as backend                        |
   | `SUPABASE_KEY`            | same as backend                        |
   | `SUPABASE_JWT_SECRET`     | same as backend                        |
   | `STORAGE_BUCKET`          | `reels-videos`                         |
   | `GEMINI_API_KEY`          | same as backend                        |
   | `GEMINI_MODEL`            | `gemini-2.5-flash`                     |
   | `WORKER_SHARED_SECRET`    | **same value as backend**              |
   | `WORKER_HOST`             | `0.0.0.0`                              |
   | `WORKER_PORT`             | `8080`                                 |
   | `MAX_RETRY_ATTEMPTS`      | `3`                                    |

5. **Settings** → **Deploy**:
   - **Healthcheck Path:** `/health`
   - **Healthcheck Timeout:** `300` seconds (worker boot is slow
     because of ffmpeg/OpenCV image size)
   - **Restart Policy Type:** `ON_FAILURE`

6. **Deploy**. The first build is **slow** — 5–8 minutes (ffmpeg +
   opencv + scenedetect wheels are heavy). The image is ~1.2 GB. Watch
   the **Build Logs** tab; pip install will dominate.

7. After deploy, **Settings → Domains** shows the public URL like
   `reels-worker-production-xxxx.up.railway.app`. Save it:

   ```bash
   export WORKER_URL="https://reels-worker-production-xxxx.up.railway.app"
   ```

### 3c. Wire the backend to the worker

The backend needs the worker's URL to call `/process`. Two ways:

**Option 1 (Railway private networking, recommended):**
Railway auto-creates a `*.railway.internal` DNS record per service.
The hostname matches the service name (e.g. `reels-worker.railway.internal`).
From the backend → worker: `WORKER_URL=http://reels-worker.railway.internal:8080/process`.

1. Go to the **reels-backend** service → **Variables** tab
2. Set `WORKER_URL=http://reels-worker.railway.internal:8080/process`
3. Railway redeploys the backend automatically.

**Option 2 (public URL):**
Use the worker's public `*.up.railway.app` URL + `/process`. The worker
is already `allow-unauthenticated`-equivalent in our config; for
production you'd want to lock it down with Railway's private network or
a service token, but for a demo this is fine.

1. Set `WORKER_URL=https://reels-worker-production-xxxx.up.railway.app/process`

> The worker does **not** verify Supabase JWTs; it only checks
> `X-Worker-Secret` against `WORKER_SHARED_SECRET`. Both backend and
> worker have the same value, so the handshake works.

---

## 4. Generate the worker shared secret

If you haven't already:

```bash
# macOS / Linux
openssl rand -base64 48

# Windows PowerShell
[Convert]::ToBase64String((1..48|ForEach-Object{Get-Random -Maximum 256}))
```

Paste the output as the value of `WORKER_SHARED_SECRET` on **both** the
backend and worker. If they don't match, the worker returns 401 and no
video ever gets processed.

---

## 5. Deploy the frontend to Vercel

1. Go to <https://vercel.com/new**
2. **Import** the same GitHub repo
3. **Configure project:**
   - **Project Name:** `reels-generator` (or whatever)
   - **Root Directory:** `frontend` (click **Edit** next to the project
     name; Vercel auto-detects Next.js but the monorepo root needs the
     override)
   - **Framework Preset:** Next.js (auto-selected)
4. **Environment Variables** — add:

   | Variable                          | Value                                  |
   | --------------------------------- | -------------------------------------- |
   | `NEXT_PUBLIC_SUPABASE_URL`        | `https://owwojzonjbnnumqukshs.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY`   | `<your Supabase anon key>`             |
   | `NEXT_PUBLIC_API_URL`             | `$BACKEND_URL/api`                     |

5. Click **Deploy**
6. After ~2 minutes, Vercel shows you a URL like
   `https://reels-generator-xxx.vercel.app`. Save it:

   ```bash
   export FRONTEND_ORIGIN="https://reels-generator-xxx.vercel.app"
   ```

---

## 6. Wire CORS

Now that the frontend URL is known, update the backend:

1. **reels-backend** service → **Variables**
2. Set `FRONTEND_ORIGIN` to the Vercel URL (no trailing slash, exact
   protocol, no `www` mismatch)
3. You can comma-separate multiple origins for preview branches:
   `FRONTEND_ORIGIN=https://reels-generator.vercel.app,http://localhost:3000`
4. Save → Railway redeploys.

---

## 7. Sanity checks

```bash
# Backend health (public)
curl $BACKEND_URL/health
# → {"status": "ok", "service": "reels-generator-api"}

# Worker health (public URL)
curl $WORKER_URL/health
# → {"status": "ok", "service": "reels-generator-worker", "active_threads": 0}

# Backend auth (no JWT → 401)
curl -i $BACKEND_URL/api/videos/00000000-0000-0000-0000-000000000000/status
# → HTTP/2 401

# Worker auth (no secret → 401)
curl -i -X POST $WORKER_URL/process -H 'Content-Type: application/json' -d '{}'
# → HTTP/2 401
```

All four should pass before you run the full smoke test.

---

## 8. Full smoke test

See [`SMOKE_TEST.md`](SMOKE_TEST.md) for the end-to-end test.

---

## Cost expectations (demo / portfolio)

At 1–5 videos/day:

| Item                | Cost                                |
| ------------------- | ----------------------------------- |
| Railway backend     | ~$1–2/mo (low-traffic web service)  |
| Railway worker      | ~$3–4/mo (1 vCPU, 2 GB, ~20% util) |
| Vercel frontend     | Free                                |
| Supabase            | Free tier (500 MB DB, 1 GB storage) |
| Gemini API          | Free tier: 15 RPM                   |

**Total: ~$5/mo** for a demo. The Railway $5 credit usually covers
this exactly, so you may pay $0 if you stay light.

To shut it all down, go to Railway → your project → **Settings** →
**Delete Project**. Supabase and Vercel can stay on free tiers.

---

## Troubleshooting

### Worker fails to start: `Address already in use` or no logs

The worker binds to `WORKER_HOST=0.0.0.0` and `WORKER_PORT=8080`.
Railway maps its own `$PORT` env var. Make sure your `worker/.env.example`
**doesn't** set `WORKER_PORT=8001` in production — leave it at 8080.

### Worker build takes 10+ minutes and times out

ffmpeg + opencv + scenedetect wheels are huge. Railway's default build
timeout is 15 min. If it times out, open the service → **Settings** →
**Build** → increase **Build Timeout** to `1800` seconds (30 min).

### Worker health check fails

`/health` should return 200 immediately. If it returns 503, the worker
crashed on startup. Check **Logs** in the Railway dashboard. Common
causes:

- Wrong `SUPABASE_KEY` (used `anon` instead of `service_role`).
- Wrong `GEMINI_API_KEY` (typo / expired).
- Wrong `WORKER_SHARED_SECRET` (mismatch with backend).

### Backend 500s on every request

Usually an env var typo. The Railway logs stream to the **Logs** tab —
open it during a request to see the traceback.

### "CORS policy: No 'Access-Control-Allow-Origin' header"

`FRONTEND_ORIGIN` on the backend doesn't exactly match the URL the
user is visiting. Re-check: no trailing slash, exact protocol, no
`www` mismatch. Vercel preview URLs need to be added too (or use a
wildcard — not supported by our CORS code, so add them explicitly).

### Worker URL not reachable from backend

If you used `*.railway.internal` and the backend can't reach it, fall
back to the public URL. The internal DNS only works between services
in the **same Railway project**.

### Frontend build fails on Vercel

Vercel auto-detects Next.js. If the build fails, check that the
**Root Directory** is set to `frontend` (not the repo root). The
Node version should be 20+; Vercel auto-selects.

### Frontend can sign up but upload fails with 401

The `NEXT_PUBLIC_API_URL` env var was set wrong (typo, missing `/api`,
wrong protocol). Vercel redeploys when you change it.

---

## Migration back to Cloud Run (when you get a Visa)

When you're ready to upgrade:

1. The Dockerfiles work on Cloud Run unchanged (they don't reference
   Railway or Vercel anywhere — they just listen on `$PORT`).
2. The Cloud Build YAMLs we used to have (`cloudbuild-*.yaml`) are
   deleted from this repo. You can recreate them from the GitHub
   history (we removed them in the Railway migration commit).
3. Or use `gcloud run deploy --source .` which auto-builds without
   needing a Cloud Build config at all.

The architecture is platform-agnostic — only the deploy scripts and
docs differ.
