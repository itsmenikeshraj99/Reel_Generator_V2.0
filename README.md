# 🎬 AI Reels Generator

> Upload a long video, get back 9:16 vertical reels ready for TikTok / Reels / Shorts —
> auto-detected scenes, AI-picked highlights, captioned, reframed, and stored.

A three-tier application that turns long-form video into short, shareable reels using
**Google Gemini** for planning + review and **ffmpeg + OpenCV** for the heavy media work.

```
┌──────────────┐    auth'd REST    ┌──────────────┐   X-Worker-Secret   ┌──────────────┐
│  Next.js 16  │ ────────────────▶ │ FastAPI API  │ ──────────────────▶ │ FastAPI Work │
│  (frontend)  │                   │  (backend)   │                     │  (worker)    │
└──────┬───────┘                   └──────┬───────┘                     └──────┬───────┘
       │                                  │                                    │
       │       Supabase Auth (JWT)        │          Supabase Service Role     │
       └──────────────────────────────────┼────────────────────────────────────┘
                                          ▼
                              ┌──────────────────────┐
                              │  Supabase (Postgres  │
                              │  + Auth + Storage)   │
                              └──────────────────────┘
                                          │
                                          ▼
                              ┌──────────────────────┐
                              │  Google Gemini API   │
                              │  (transcribe / plan  │
                              │  / review captions)  │
                              └──────────────────────┘
```

## ✨ Features

- **🔐 Real auth** — Supabase email + OAuth (Google) login, JWT-verified on every backend call
- **📤 Direct uploads with progress** — XHR PUT to a Supabase signed upload URL (no backend bandwidth) with a live progress bar in the UI
- **🤖 AI edit planning** — Gemini proposes 3–5 candidate highlight reels per video
- **👀 AI review** — Gemini critiques each candidate, the planner re-rolls with feedback (up to N attempts), with a graceful fallback so the user always gets a reel
- **🎬 Scene-aware reframing** — PySceneDetect splits the video into shots; OpenCV finds the subject; each shot gets a custom 720×1280 crop (no awkward mid-shot jumps; resolution is capped to fit Railway's 1GB worker)
- **✂️ Lossless stitching** — re-encoded cuts + concat demuxer + `aresample=async=1` to fix any A/V drift
- **📝 Auto captions** — word-level timing from Gemini transcript → burned-in captions on the final reel
- **📥 Download + share** — public URL on Supabase Storage; click-to-download or share to Twitter / LinkedIn / WhatsApp from the gallery
- **🗂 Dashboard** — every uploaded video in a grid with status badges, reel counts, and an "auto-delete in Xh" countdown. Each card opens the gallery for its reels; "Generate New Reels" lives on the gallery, not the card.
- **📱 Mobile-first UI** — top nav + slide-in drawer under 768px, responsive grids, gradient + Play-icon video previews that load on hover
- **🔔 Toast notifications** — non-blocking feedback for "Reels ready!", "Session cleared", share, etc.
- **🌗 Day/night theme** — semantic CSS variables, persisted in `localStorage` (`reelgen-theme`), Sun/Moon toggle in the nav. Light mode is fully styled; dark mode is the default and visually unchanged.
- **🛡️ Defense in depth** — CORS allow-list, CSP headers, X-Frame-Options, RLS on the user-facing path, service-role keys scoped to the worker

## 🎨 Branding

| Where | Asset | Source |
|---|---|---|
| Browser tab (favicon) | `src/app/icon.png` (32×32, 4 KB) | Next.js auto-detected |
| iOS home screen | `src/app/apple-icon.png` (192×192, 35 KB) | Next.js auto-detected |
| Social share preview (OG / Twitter) | `src/app/opengraph-image.png` (1200×630, 254 KB) | Next.js auto-detected |
| AppShell nav (32×32 icon + "Reel Generator" text) | `public/android-chrome-192x192.png` | referenced in `src/components/AppShell.tsx` |
| Landing-page hero (180×180) | `public/logo-1024x1024.png` | referenced in `src/app/page.tsx` |

All 7 logo variants live under `frontend/public/` and `frontend/src/app/`. The
high-res master is `public/reel_generator_logo.png` (2396×2582, palette mode
— keep as source of truth; the others are sized/optimized derivatives).

To rebrand, drop a new `public/reel_generator_logo.png`, then re-derive
`logo-1024x1024.png` + `android-chrome-192x192.png` + `apple-touch-icon.png`
+ `favicon-40x40.png` + `og-image.png` (and copy the favicon/apple/og into
`src/app/`). No code changes needed — the references are by filename.

## 🏗️ Architecture

The system has three independently deployable services, all driven by a Postgres
`jobs` row that the worker reads as a state machine.

### Pipeline stages

| Stage                  | What happens                                                    | Where       |
| ---------------------- | --------------------------------------------------------------- | ----------- |
| `VALIDATING`           | ffprobe codec / duration / size; reject junk                    | worker      |
| `TRANSCRIBING_PLANNING` | Gemini Call #1 — transcribe + propose 3–5 candidate edit plans | worker      |
| `REVIEWING`            | Gemini Call #2 — review each candidate, re-roll on feedback     | worker      |
| `RENDERING`            | scene-detect → reframe → stitch → upload to Supabase Storage    | worker      |
| `READY`                | Terminal state; frontend shows the gallery                      | —           |

The pipeline is **resumable**: if the worker crashes mid-run, the next invocation
picks up at `current_stage` instead of redoing the whole thing. Each stage is
wrapped in a bounded retry (`MAX_RETRY_ATTEMPTS`, default 3) with exponential
backoff. After exhaustion, the job is marked `PERMANENTLY_FAILED` so the UI can
distinguish it from a transient crash.

### Why split backend and worker?

The backend is a small, low-latency HTTP API (FastAPI) that:

- Issues Supabase signed upload URLs
- Verifies user JWTs and creates `videos` rows
- Hands a job off to the worker via a fire-and-forget HTTP call

The worker is a long-running, CPU/GPU-heavy process that:

- Runs ffmpeg + OpenCV (multi-minute jobs)
- Holds the Gemini API key and the Supabase service-role key
- Authenticates inbound calls with a **shared secret** (`X-Worker-Secret`) so the
  backend — and only the backend — can submit work

This split means the worker can be moved to a GPU box, scaled horizontally, or
taken offline for maintenance without ever touching the user-facing API.

### UI design system (Phase 12)

- **Theme tokens** — all colors live in CSS variables on `:root` (light) and
  `[data-theme="dark"]` (dark). Tailwind colors (`bg`, `surface`, `surface-2`,
  `border`, `text`, `text-muted`, `text-subtle`) reference those variables,
  so swapping themes is a single attribute flip on `<html>`.
- **No-flash boot** — an inline `<head>` script reads
  `localStorage.getItem('reelgen-theme')` and sets `data-theme` before first
  paint, so there's no flash of dark/light on reload.
- **Persistence** — the theme is a single key (`reelgen-theme` ∈
  `{"light","dark"}`, default `dark`); `useTheme()` reads + writes it.
- **No hardcoded colors** in app code — every class is a token. Gradient
  primary buttons, color-coded status badges, and the thumbnail overlay
  are the only intentional exceptions (they read on both themes).

## 📂 Project layout

```
.
├── backend/            FastAPI API (auth, signed URLs, job handoff)
│   ├── app/
│   │   ├── routers/    videos.py, reels.py
│   │   ├── services/   auth.py, supabase.py, storage.py, tasks.py
│   │   ├── models/     pydantic schemas
│   │   └── main.py
│   └── requirements.txt
├── worker/             FastAPI worker (pipeline runner)
│   ├── worker/
│   │   ├── stages/     validate, transcribe_plan, review, reframe, stitch, caption
│   │   ├── gemini/     client + prompts + schemas
│   │   ├── services/   supabase.py, storage.py
│   │   ├── pipeline.py state machine
│   │   └── main.py     HTTP entry point
│   └── requirements.txt
├── frontend/           Next.js 16 (App Router) + Tailwind + Supabase SSR
│   ├── public/         logo + favicon + OG variants (see Branding above)
│   ├── src/
│   │   ├── app/        auth, upload, upload/status, upload/gallery, dashboard,
│   │   │              icon.png, apple-icon.png, opengraph-image.png
│   │   ├── components/ AppShell, AuthModal, Toast, Skeleton, EmptyState,
│   │   │              ProgressBar, UploadDropzone, ReelCard, VideoPreview,
│   │   │              ShareMenu, ThemeToggle
│   │   ├── hooks/      useTheme
│   │   └── lib/        api.ts (typed client), supabase.ts, cn.ts
│   ├── next.config.js  CSP / X-Frame-Options / referrer-policy
│   └── package.json
├── db/
│   └── schema.sql      Supabase tables, indexes, RLS policies — paste in SQL Editor
├── .env.example files  (one per service, never commit .env)
└── README.md
```

## 🚀 Quick start

> **Prereqs:** Python 3.11+, Node 20+, ffmpeg + ffprobe on `PATH`, a Supabase
> project, a Google Gemini API key.

### 1. Clone

```bash
git clone https://github.com/itsmenikeshraj99/Reel_Generator_V2.0.git
cd Reel_Generator_V2.0
```

### 2. Set up Supabase

1. Create a project at <https://supabase.com>.
2. **Settings → API** — copy:
   - Project URL → `SUPABASE_URL`
   - **`service_role` key** (⚠️ backend/worker only — never expose to the browser) → `SUPABASE_KEY`
   - **`anon` key** (frontend-safe) → `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - **JWT secret** (Settings → API → JWT Secret) → `SUPABASE_JWT_SECRET`
3. **SQL Editor** — paste & run [`db/schema.sql`](db/schema.sql). It's idempotent
   (`create … if not exists`, `create index … if not exists`).
4. **Storage → Buckets** — create a bucket called `reels-videos` (public read OR
   use the signed-URL flow described in [`db/schema.sql`](db/schema.sql) comments).
5. **Authentication → Providers** — enable Email + (optionally) Google.

### 3. Configure secrets

```bash
cp backend/.env.example  backend/.env
cp worker/.env.example   worker/.env
cp frontend/.env.example frontend/.env.local
```

Fill them in with the values from step 2. **Generate a fresh
`WORKER_SHARED_SECRET`** — both backend and worker must use the same value:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 4. Install + run

Three terminals, one per service.

**Backend** (port `8000`)

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Worker** (port `8001`)

```bash
cd worker
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# ffmpeg + ffprobe must be on PATH (worker re-encodes)
python -m worker.worker.main
```

**Frontend** (port `3000`)

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>, sign up, and upload a short MP4 (≤ 500 MB by default;
raise `MAX_VIDEO_SIZE_MB` in `backend/.env` if you need more headroom).

### 5. Try the API directly

Grab the access token from your browser's devtools:

```js
// In the browser console, after signing in:
(await supabase.auth.getSession()).data.session.access_token;
```

Then:

```bash
TOKEN="paste-token-here"

# 0) List the caller's videos (for the dashboard)
curl http://localhost:8000/api/videos \
  -H "Authorization: Bearer $TOKEN"

# 1) Get a signed upload URL
curl -X POST http://localhost:8000/api/videos/upload-url \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"test.mp4"}'

# 2) Upload the file to the signed URL (or use the frontend, which does this for you)

# 3) Kick off processing
curl -X POST http://localhost:8000/api/videos/$VIDEO_ID/process \
  -H "Authorization: Bearer $TOKEN"

# 4) Poll status
curl http://localhost:8000/api/videos/$VIDEO_ID/status \
  -H "Authorization: Bearer $TOKEN"

# 5) Once READY, list reels
curl http://localhost:8000/api/reels/$VIDEO_ID \
  -H "Authorization: Bearer $TOKEN"
```

Unauthenticated calls return `401` — that's the point.

## ⚙️ Configuration reference

### Backend (`backend/.env`)

| Var                   | What                                                    |
| --------------------- | ------------------------------------------------------- |
| `SUPABASE_URL`        | Project URL                                             |
| `SUPABASE_KEY`        | `service_role` key                                      |
| `SUPABASE_JWT_SECRET` | For verifying user JWTs                                 |
| `STORAGE_BUCKET`      | Default `reels-videos`                                  |
| `GEMINI_API_KEY`      | Google AI Studio                                        |
| `GEMINI_MODEL`        | `gemini-2.5-flash` (or another supported model)         |
| `MAX_VIDEO_SIZE_MB`   | Upload size cap, default 500                            |
| `FRONTEND_ORIGIN`     | CORS allow-list, e.g. `http://localhost:3000`           |
| `WORKER_URL`          | Where to send processing jobs                           |
| `WORKER_SHARED_SECRET`| Must match worker's secret                              |

### Worker (`worker/.env`)

| Var                    | What                                                |
| ---------------------- | --------------------------------------------------- |
| `SUPABASE_URL`         | Same as backend                                     |
| `SUPABASE_KEY`         | `service_role` key (worker has no user context)     |
| `SUPABASE_JWT_SECRET`  | Not strictly required for the worker, kept for parity |
| `STORAGE_BUCKET`       | `reels-videos`                                      |
| `GEMINI_API_KEY`       | Same key                                            |
| `GEMINI_MODEL`         | Same model                                          |
| `WORKER_SHARED_SECRET` | Must match backend's                                |
| `WORKER_HOST`          | Default `127.0.0.1`; only set `0.0.0.0` behind a trusted proxy |
| `WORKER_PORT`          | Default `8001`                                      |
| `MAX_RETRY_ATTEMPTS`   | Per-stage retry count, default `3`                  |

### Frontend (`frontend/.env.local`)

| Var                            | What                                       |
| ------------------------------ | ------------------------------------------ |
| `NEXT_PUBLIC_SUPABASE_URL`     | Project URL                                |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY`| `anon` key (safe to expose)                |
| `NEXT_PUBLIC_API_URL`          | Backend base, e.g. `http://localhost:8000/api` |

## 🛡️ Security model

Three layers, each independently necessary:

1. **Frontend → Backend**: every request sends a Supabase access token in
   `Authorization: Bearer …`. The backend verifies it with `auth.get_user(token)`
   before touching any user data. The frontend also has middleware on
   `/upload/*` that bounces unauthenticated users back to the landing page.
2. **Backend → Worker**: the worker has a `require_worker_secret` dependency on
   `/process` that compares `X-Worker-Secret` to `WORKER_SHARED_SECRET` using a
   constant-time compare. The worker binds to `127.0.0.1` by default; flip to
   `0.0.0.0` only behind a reverse proxy that already authenticates the caller.
3. **Backend/Worker → Supabase**: the `service_role` key bypasses RLS, so the
   backend explicitly filters every query by `current_user.id`. The frontend
   uses the `anon` key, which respects RLS — the user can only see their own
   rows.

Plus:

- **CORS** is an explicit allow-list, never `*`.
- **CSP**, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, and
  `Permissions-Policy: camera=(), microphone=(), geolocation=()` are set in
  `frontend/next.config.js`.
- **Secrets** are never committed. `.env.example` files document the contract;
  real `.env` files are git-ignored.
- **Service-role keys** live only in `backend/.env` and `worker/.env`. Rotate
  the moment you suspect exposure (e.g. pushed to a public repo).

## 🧪 Testing the local stack

```bash
# Backend
cd backend
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000/docs

# Worker (separate terminal)
cd worker
.\venv\Scripts\activate
pip install -r requirements.txt
python -m worker.worker.main

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

End-to-end:

1. Sign up → confirm email → log in.
2. Upload a short MP4.
3. Watch the status page advance through the pipeline.
4. Open the gallery, hit play, hit download.
5. Click "Finish & Clear Session" to wipe local data.

## 🐛 Troubleshooting

| Symptom                                                | Likely cause                                              |
| ------------------------------------------------------ | --------------------------------------------------------- |
| `401` on every backend call                            | `SUPABASE_JWT_SECRET` mismatch, or anon key in `.env.local` |
| `401` on `/api/videos/.../process` from backend        | `WORKER_SHARED_SECRET` mismatch                           |
| Worker stuck at `VALIDATING`                           | `ffprobe` not on `PATH`                                   |
| `RuntimeError: ffmpeg failed (rc=…): Error reinitializing filters!` | Filter graph bug — reframe/stitch, see logs     |
| Pipeline runs but `edit_plans` are all `rejected`      | Reviewer too strict; cap-reached fallback accepts the best by `hook_score` and the pipeline still completes |
| `UnicodeEncodeError` from worker on Windows            | Run with `PYTHONIOENCODING=utf-8` and `python -X utf8`    |
| Frontend build fails on `AlertCircle not defined`      | Older import — make sure you have the latest `frontend/src/app/upload/gallery/page.tsx` |
| `transcripts.words` is null, captions skipped          | Expected for some videos — captions require overlapping word timings; warning, not error |

## ☁️ Deploy

The project ships with everything needed to deploy the backend and
worker to [Railway](https://railway.app) and the frontend to
[Vercel](https://vercel.com) (free tier, no card required). Earlier
versions targeted Google Cloud Run; the Dockerfiles are unchanged, so
swapping back is a config-only migration — see
[`deploy/SETUP.md`](deploy/SETUP.md#migration-back-to-cloud-run-when-you-get-a-visa).

**Cost: $0–5/mo** for a demo / portfolio deployment (1–5 videos/day).
The Railway $5 trial credit usually covers this exactly.

```bash
# 1. Read the step-by-step guide
open deploy/SETUP.md
```

| File                                          | What                                                |
| --------------------------------------------- | --------------------------------------------------- |
| [`deploy/SETUP.md`](deploy/SETUP.md)          | From zero Railway/Vercel accounts → first deploy    |
| [`deploy/SMOKE_TEST.md`](deploy/SMOKE_TEST.md) | End-to-end test of the deployed stack               |
| [`deploy/env.prod.example`](deploy/env.prod.example) | Env vars template (paste into Railway/Vercel) |
| [`vercel.json`](vercel.json)                  | Vercel config (auto-deploys from `main`)            |



## 🗺️ Roadmap

- [x] Auth (Supabase + JWT verification)
- [x] Direct-to-storage uploads (signed URLs)
- [x] Multi-stage AI pipeline with retry
- [x] Scene-aware reframing
- [x] Caption overlay
- [x] Public gallery with download
- [x] Railway deploy (backend + worker) + Vercel (frontend) — no card required
- [x] **UI/UX overhaul** — AppShell, dashboard, progress bar, share menu, toasts, mobile drawer (Phase 11)
- [x] **Branding** — logo, favicon, OG image, AppShell + landing wire-up (2026-09-05)
- [x] **Light theme + token system** — semantic CSS variables, day/night toggle, name field in AuthModal, dashboard consolidation to single "Reels" button, gallery "Generate New Reels" (Phase 12)
- [ ] Google Cloud Run deploy — switch back when you have a Visa/Mastercard
- [ ] Cloud Tasks queue (production-grade queueing)
- [ ] Secret Manager + custom domain (production hardening)
- [ ] Background music / B-roll suggestions
- [ ] Multi-language caption translation
- [ ] Stripe billing for paid tiers

## 🤝 Contributing

PRs welcome. Please:

1. Don't commit `.env` files or any secrets.
2. Keep `requirements.txt` and `package.json` pinned.
3. Run the end-to-end smoke test before submitting.
4. For new environment variables, update the relevant `.env.example`.

## 📄 License

MIT — see [`LICENSE`](LICENSE).

## 📝 Changelog

### Branding (2026-09-05)
- Added `reel_generator_logo.png` (lightning-bolt + "REEL GENERATOR" wordmark) and 6 sized variants under `frontend/public/` and `frontend/src/app/`
- Auto-detected `icon.png` (32×32 favicon), `apple-icon.png` (192×192 iOS), and `opengraph-image.png` (1200×630 OG) — no `metadata.icons` config needed for them
- Explicit `metadata.icons` added in `layout.tsx` as a defensive fallback
- AppShell top nav now shows the 32×32 mark + "Reel Generator" wordmark instead of the gradient text
- Landing-page hero now shows the 180×1024 logo above the headline
- New "Branding" section in the README documenting the file layout and rebrand steps

### Phase 12 — Light theme + dashboard consolidation (2026-09-05)

UI-only refactor (no backend or worker changes). Adds day/night theming,
a name field in sign-up, and consolidates the dashboard around videos.

**Loading bug fix**
- `api.request()` now wraps every fetch in an `AbortController` with an
  8s timeout; stuck requests surface a "Request timed out" error instead
  of an infinite skeleton.
- On 401, the dashboard clears its `videos` array before the redirect
  so the error state is visible during the bounce (no flash of stale
  skeleton).

**Theme system (PR 2-3)**
- `globals.css` defines semantic CSS variables on `:root` (light) and
  `[data-theme="dark"]` — `--bg`, `--surface`, `--surface-2`,
  `--border`, `--text`, `--text-muted`, `--text-subtle`, `--primary`,
  `--secondary`, `--gradient-bg`.
- `tailwind.config.js` adds token-backed color names so future classes
  are theme-aware by default.
- `layout.tsx` ships an inline `<head>` script that reads
  `localStorage['reelgen-theme']` and stamps `data-theme` on `<html>`
  before first paint (no flash of dark/light on reload).
- `useTheme()` (`src/hooks/`) is a client hook reading/writing the
  preference; `<ThemeToggle>` is a Sun/Moon button wired into
  `AppShell` (desktop nav + mobile drawer).
- Default theme is `dark`; both themes are fully styled.

**AuthModal redesign (PR 4)**
- Sign-up form now asks for a `name` (only on sign-up; sign-in keeps
  email + password). The name is passed to Supabase as
  `data: { full_name }` and lands in `user_metadata`.
- Glass-card visual (backdrop-blur, soft border), gradient Sparkles
  icon header, 2-column OAuth grid (Google + GitHub).
- AppShell uses `user_metadata.full_name` → falls back to
  `user_metadata.name` → email local-part in the user menu.

**Dashboard consolidation (PR 5)**
- The dashboard is now strictly a videos view — no "Reels" section, no
  per-card generation button.
- CTA heading: "Your Reels" → "Your Library".
- Each card has a single "Reels" button (gradient, Film icon) that
  opens the gallery for that video.

**Gallery "Generate New Reels" (PR 6)**
- The gallery now has an always-reachable "Generate New Reels" button
  in the top action group, before "Finish & Clear Session".
- Works for both regenerating an existing video and kicking off the
  first generation when the gallery is empty.
- Local `isGenerating` flag prevents double-submit; backend's 409
  guard on `/videos/{id}/process` is the safety net.

**Semantic token swap (PR 7)**
- Mechanical find-replace across 21 files: hardcoded dark classes
  (`text-white`, `text-gray-300/400/500`, `bg-white/5/10`,
  `border-white/10/20`, `bg-dark`) → token-backed classes
  (`text-text`, `text-text-muted`, `text-text-subtle`, `bg-black/5/10`,
  `border-border`, `bg-bg`).
- Intentionally kept: gradient primary buttons (`text-white` on
  gradient), color-coded status badges (`*-500/15` + `*-300`),
  thumbnail overlay badges (`bg-black/60 backdrop-blur`), and the
  VideoPreview Play icon (`text-white/90` on dark glass).
- Dark mode is visually identical; light mode is now fully styled.

### Phase 11 — UI/UX Overhaul (2026-09-05)

The user-facing rebuild. No backend semantics changed (one new endpoint
plus a `signed_upload_url` on the existing `POST /api/videos/upload-url`).

**Backend**
- New: `GET /api/videos?limit&offset` — list the caller's videos with reel
  counts and latest job stage for the dashboard.
- `POST /api/videos/upload-url` now returns `signed_upload_url` +
  `upload_token` so the client can `XHR.PUT` the file with real progress
  events.
- New Pydantic models: `VideoListItem`, `VideoListResponse`,
  `SignedUploadInfo`.

**Frontend — new components**
- `AppShell` — top nav (sticky, backdrop-blur) + mobile drawer
  (`<768px`); auth via `supabase.auth.onAuthStateChange`.
- `Toast` + `useToast` — 3-variant notification system (success /
  error / info), 4s/6s TTL, z-100, manual close.
- `Skeleton` — text/rect/circle placeholders for loading states.
- `EmptyState` — icon + body + CTA.
- `UploadDropzone` — 4-state drag-drop (idle / drag-over / selected /
  error).
- `ProgressBar` — value (0-100) + indeterminate modes; accessible.
- `ReelCard` + `VideoPreview` — extracted from the gallery: 9:16 frame
  with gradient + Play icon, video cross-fades in on hover.
- `ShareMenu` — popover with Copy Link / Twitter / LinkedIn / WhatsApp;
  uses native `<details>` so no outside-click handler needed.

**Frontend — new pages**
- `/dashboard` — list the user's videos with status badges, reel counts,
  and "Xh left" countdowns. Server-component shell + client
  `DashboardClient` + error boundary.

**Frontend — refactored pages**
- `/` split into landing (logged-out) / bounce-to-dashboard (logged-in).
- `/upload` wrapped in `AppShell`; switches to XHR PUT upload with a
  live progress bar.
- `/upload/status` wrapped in `AppShell`; fires a toast on `READY`,
  adds an "Open Dashboard" link.
- `/upload/gallery` wrapped in `AppShell`; uses `ReelCard` + `ShareMenu`;
  delete-confirm modal z-index lowered from 50 → 40 so toasts sit above
  it.
- `not-found` page wrapped in `AppShell` with dual "Go home" /
  "Browse dashboard" CTAs.
- `AuthModal` redirects new sign-ups to `/auth/callback?next=/dashboard`.

**Other**
- Tailwind keyframes: `toast-in`, `pulse-stage`, `gradient-shift`.
- Zero new dependencies; zero schema migrations; zero new env vars.

### Earlier phases
See the [commit history](https://github.com/itsmenikeshraj99/Reel_Generator_V2.0/commits/main)
for the full changelog of the auth + pipeline + deploy work (Phases 1–10).

---

Built with ❤️ by [itsmenikeshraj99](https://github.com/itsmenikeshraj99)
