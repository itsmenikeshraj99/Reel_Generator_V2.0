# UI/UX Improvement Plan

## Context

The Reels Generator frontend currently works end-to-end but has UX gaps that
make it feel like a demo, not a product. This plan closes those gaps without
touching the pipeline or the deploys.

**Why this is being done.** The goal is to take the project from "the
pipeline produces a reel" to "a user opens the app, feels oriented, can find
their previous work, and share the result." Concretely:

- A user who lands on the home page has no idea what the app does or that
  they need to upload a video.
- A user who finishes a generation has no way to see past videos — the
  gallery is per-video (`?videoId=…`), so navigating away loses the reel.
- A user who generates a great clip has no easy way to share it.
- The upload flow has a drag-drop zone but no drop indicator and no progress
  bar — just a "Uploading…" label.

**Scope chosen.** Full UX overhaul with video history (requires a small
backend addition) and copy-link + social share on the gallery.

---

## What's missing today

| # | Gap                                                                                | Severity |
| - | ---------------------------------------------------------------------------------- | -------- |
| 1 | No persistent **dashboard** — home page is a "Welcome back" + 3-link card grid      | High     |
| 2 | No **video history** — gallery is per-`videoId`, no list of past uploads           | High     |
| 3 | No **upload progress bar** — just `Uploading…` text                                  | High     |
| 4 | No **drag-drop visual feedback** — file is selected silently                         | Medium   |
| 5 | No **share buttons** on the gallery (copy link, Twitter, LinkedIn, WhatsApp)         | Medium   |
| 6 | No **mobile-friendly nav** — large tap targets missing on small screens              | Medium   |
| 7 | No **skeleton loaders** for the gallery — only a spinner                             | Low      |
| 8 | No **toast notifications** for success/error — relies on in-page banners             | Low      |
| 9 | No **demo / sample video** option for new users without a file                      | Low      |
| 10 | No **dark/light mode toggle** — the rest of the app is dark-only                    | Low      |
| 11 | No **empty-state illustration** on the gallery when reels are gone                  | Low      |
| 12 | No **transcript / "why this clip" preview** on the gallery                          | Low      |

We are addressing **#1–#8 and #11** (the high/medium ones + cheap polish).
We are **NOT** addressing #9, #10, #12 in this round (out of scope; can be
Phase 12 polish if needed).

---

## Architecture decisions

### 1. Backend gets ONE new endpoint, no schema change

`GET /api/videos` — list the current user's videos, newest first, with
status from the most recent job and a count of reels per video.

```python
@router.get("")
async def list_my_videos(
    current: CurrentUser = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List the current user's videos, newest first, with their job status
    and reel count. RLS already restricts to the user's own rows; the
    service-role key in the backend doesn't add a row, so we filter
    explicitly by `user_id`."""
```

The endpoint returns:
```json
{
  "videos": [
    {
      "id": "uuid",
      "filename": "vacation.mp4",
      "status": "READY",
      "created_at": "2026-09-04T...",
      "gcs_uri": "<user_id>/<safe_filename>",
      "thumbnail_url": "<signed-url>" | null,
      "reel_count": 3,
      "last_stage": "RENDERING"
    }
  ],
  "total": 12
}
```

- `thumbnail_url` — generated on-the-fly using the Supabase
  `create_signed_url()` helper for the **first reel's storage path** (cheap;
  no extra storage). If no reels yet, it's `null`.
- `last_stage` and `reel_count` come from joining `jobs` and `reels` —
  the existing service_role client can do this in 3 small queries.
- Limit default 50, max 200 (enforced in the handler).
- Sorted by `videos.created_at desc`.

### 2. Frontend gets a new `/dashboard` route

`/` (the home page) becomes a **marketing landing** when logged-out and a
**dashboard** when logged-in. Currently it does both in one file. We'll
split:

- `app/page.tsx` — landing (logged out)
- `app/dashboard/page.tsx` — dashboard (logged in) — moved out of `page.tsx`
- `page.tsx` redirects to `/dashboard` if a user is signed in

A `/dashboard` route also lets us deep-link to it from email notifications
later ("View your reels" → `/dashboard`).

### 3. Global navigation

A `<AppShell>` component (server component) wraps every authenticated
page and provides:

- Top nav bar with logo, dashboard link, gallery link, sign-out
- Mobile drawer (hamburger on small screens)
- "Upload" CTA always in the top-right

The unauthenticated pages (landing, auth callback) don't use the shell.

### 4. Upload progress

Right now the upload calls `supabase.storage.upload()` with no progress
callback. Supabase JS doesn't expose progress events out of the box, so
we use the **Fetch API + `XMLHttpRequest`** to PUT to the signed URL with
an `upload.onprogress` listener. This works because the backend already
returns a **signed PUT URL** in `UploadUrlResponse` — actually wait,
let me check…

Actually, the backend uses `supabase.storage.from_(bucket).create_signed_url()`
which by default creates a **GET** signed URL. For uploads, we need a
**PUT signed upload URL** — which Supabase supports via
`create_signed_upload_url()`. The backend currently uses the regular
upload path, not signed upload URLs.

Two options:
- **Option A (recommended):** Switch the backend to `create_signed_upload_url()`
  and have the frontend PUT to it with `XMLHttpRequest` for progress.
- **Option B:** Keep using `supabase.storage.upload()` and show a fake
  indeterminate progress bar (the upload is fast for our file sizes).

Going with **Option A** because (a) it's a small backend change, (b) the
real progress bar is a real UX win for big files, and (c) it removes a
dependency on the Supabase JS client's internal upload path. **This
becomes a separate small subtask** in the implementation.

### 5. Share buttons

On each gallery card, add:

- **Copy link** — copies a signed URL to the reel to the clipboard
- **Twitter / X** — opens `https://twitter.com/intent/tweet?url=...&text=...`
- **LinkedIn** — opens `https://www.linkedin.com/sharing/share-offsite/?url=...`
- **WhatsApp** — `https://wa.me/?text=...`

We need a **signed URL** for sharing, because the bucket is currently
public for reads. Public read is fine for the gallery itself, but for
sharing a private link to someone who doesn't have a Supabase account,
we want a **time-limited signed URL** (1 hour). The backend already
returns a `public_url` field in the `reels` row; we add a `signed_url`
field returned by `GET /api/reels/{video_id}` that's freshly generated
on each call. (This is cheap — Supabase generates them in O(1) time.)

### 6. Mobile polish

- Nav: hamburger menu on `<md`
- Upload dropzone: bigger touch target, full-width on mobile
- Gallery grid: 1 column on `<sm`, 2 on `sm`, 3 on `md`+ (already there)
- Status page: stage rows stack vertically with bigger icons on mobile
- Buttons: `min-h-[44px]` for tap targets (Apple HIG)

### 7. Skeleton loaders

A `<Skeleton>` component (3 shapes: text, circle, rect) for:

- Dashboard video cards (while list is loading)
- Gallery reel cards
- Status page (rarely — only on first paint)

### 8. Toasts

A `<ToastProvider>` that exposes `useToast()` for fire-and-forget
notifications. Used for:

- "Reel link copied!"
- "Upload failed — please try again"
- "Session cleared"
- "Re-pipeline started" (if we add that)

Stacked top-right, auto-dismiss after 4s, manual close.

---

## Critical files

### Backend (2 changes)

- **`backend/app/routers/videos.py`** — add `GET /api/videos` (list endpoint)
  and switch `/upload-url` to return a `create_signed_upload_url()` instead
  of the legacy `create_signed_url()`. ~80 new lines.
- **`backend/app/routers/reels.py`** — add `signed_url` field to the
  `GET /api/reels/{video_id}` response (regenerated per request). ~10 lines.

### Backend (no change)

- No schema migration. The new endpoint joins `videos` + `jobs` + `reels`
  on the fly.
- No new dependencies.

### Frontend — new files

- `frontend/src/components/AppShell.tsx` — global nav wrapper
- `frontend/src/components/Skeleton.tsx` — `<Skeleton variant="text|circle|rect">`
- `frontend/src/components/Toast.tsx` — provider + `useToast()` hook
- `frontend/src/components/EmptyState.tsx` — reusable empty state
- `frontend/src/components/UploadDropzone.tsx` — the file picker, with drag-drop visual
- `frontend/src/components/ReelCard.tsx` — extracted from `GalleryClient` so we can reuse
- `frontend/src/components/ShareMenu.tsx` — copy + social share buttons
- `frontend/src/app/dashboard/page.tsx` — server entry; reads user, renders client
- `frontend/src/app/dashboard/DashboardClient.tsx` — the dashboard
- `frontend/src/app/dashboard/loading.tsx` — skeleton for the dashboard
- `frontend/src/app/dashboard/error.tsx` — error boundary for the dashboard
- `frontend/src/app/not-found.tsx` — already exists; we'll make it nicer (illustration, links)
- `frontend/src/app/loading.tsx` — already exists; refresh
- `frontend/src/app/auth/callback/page.tsx` — already exists; add a "complete
  profile" prompt if the user is brand-new

### Frontend — modified files

- `frontend/src/app/page.tsx` — keep only the **landing**; redirect to
  `/dashboard` if logged-in (server component)
- `frontend/src/app/upload/page.tsx` — switch to `UploadDropzone`, add
  progress bar, show video preview
- `frontend/src/app/upload/status/StatusClient.tsx` — wrap in AppShell,
  add "open dashboard" link
- `frontend/src/app/upload/gallery/GalleryClient.tsx` — wrap in AppShell,
  use `ReelCard` + `ShareMenu`
- `frontend/src/components/AuthModal.tsx` — add ToS / Privacy links, show
  "we sent you an email" success state with animation
- `frontend/src/lib/api.ts` — add `getMyVideos()`, `getReelSignedUrl(reelId)`
- `frontend/src/app/layout.tsx` — wrap children in `<ToastProvider>`,
  add `noscript` fallback, add the `<html>` `data-theme` attribute for
  future theming
- `frontend/tailwind.config.js` — add `keyframes` for the new animations
  (slide-in toast, pulse-on-active-stage, etc.)
- `frontend/src/app/globals.css` — add the keyframes referenced above

### Documentation

- `frontend/README.md` (new) — component organization guide for future devs
- `docs/ui-improvement-plan.md` (this file) — already written

---

## Implementation order

I'll work in this order so each step is testable:

### Phase A — backend additions (1 PR)

1. **Backend `GET /api/videos`** — list endpoint with reel counts and
   last stage. 30 min.
2. **Backend `/upload-url` switch** — `create_signed_upload_url()` instead
   of `create_signed_url()`. Frontend will then use `fetch` PUT to upload.
   20 min.
3. **Backend `reels` signed URL** — return a freshly-signed URL per reel
   per request. 15 min.

### Phase B — shared UI primitives (1 PR)

4. **`Skeleton` + `Toast` + `EmptyState`** — no app logic, easy to test. 30 min.
5. **`AppShell`** — global nav with mobile drawer. 45 min.
6. **`UploadDropzone`** — drag-drop with visual states (idle, drag-over,
   processing, error). 45 min.
7. **`ReelCard` + `ShareMenu`** — extracted gallery card with share. 45 min.

### Phase C — page-by-page migration (2 PRs)

8. **Dashboard** — new `/dashboard` route. Wire up `getMyVideos()`. 60 min.
9. **Home page split** — landing stays, redirect to /dashboard. 15 min.
10. **Upload page** — use `UploadDropzone`, add progress bar, video
    preview. 45 min.
11. **Status page** — wrap in `AppShell`, add toast on completion, add
    "open dashboard" link. 30 min.
12. **Gallery page** — use `ReelCard` + `ShareMenu`. 45 min.

### Phase D — polish (1 PR)

13. **Mobile nav** — hamburger drawer, tap targets, breakpoints audit. 30 min.
14. **Loading / error / 404** — skeletons everywhere; nicer 404 with
    a "go home" + "browse gallery" dual button. 20 min.
15. **Auth flow polish** — better success state on signup (animation),
    proper "verification email sent" state, redirect target. 30 min.
16. **Animations** — toasts slide in, stages pulse on active, cards
    scale on hover, dropzone has a subtle gradient on drag-over. 20 min.

### Phase E — verify (1 PR)

17. **Smoke test** — end-to-end on local stack: signup, upload, watch
    progress, see status, share, delete. 30 min.
18. **Mobile smoke** — Chrome devtools iPhone 14 viewport, walk through. 15 min.
19. **README update** — document new components, new backend endpoint,
    screenshot of the new dashboard. 15 min.

**Total estimated effort: ~9 hours.** Split into 5 PRs so the user can
review each one.

---

## What I will NOT do (and why)

- **Tailwind plugin** (e.g. shadcn/ui, Radix) — current Tailwind config
  is minimal and works; adding a component library is a 10x scope
  expansion. Hand-rolled components are fine for this size.
- **Storybook** — we have one dev; Storybook's overhead isn't worth it.
- **Internationalization** — out of scope; English-only for now.
- **Tests** — the project has no test infra on the frontend; adding
  Vitest + Playwright is a separate Phase 12.
- **Light mode** — explicitly out of scope. The dark theme is on-brand
  for a video app and consistent.
- **Drag-reorder, multi-file upload, batch generation** — all Phase 12+.
- **"Edit clip in the browser" (trim, crop, etc.)** — different product.

---

## Verification

### Local (Phase E step 17-18)

1. `cd frontend && npm run dev`
2. Sign up, verify email
3. Land on `/dashboard` (was `/` before)
4. Click "Upload Video" → drop a 50 MB MP4 → see progress bar advance
5. Click "Generate" → land on `/upload/status` → see live progress
6. When ready, click "View Reels" → see grid of 9:16 video cards
7. Click "Copy link" on a card → toast "Link copied!"
8. Resize browser to mobile width → hamburger menu appears, dropzone
   is full-width, gallery is 1-column
9. Delete a video → "Session cleared" toast

### Build check

```bash
cd frontend
npm run build      # must succeed with no TS errors
npm run lint       # must succeed
npm run typecheck  # must succeed (was failing before with AlertCircle)
```

### Lighthouse (mobile)

Target scores after the change:
- Performance: ≥ 90
- Accessibility: ≥ 95
- Best Practices: ≥ 95
- SEO: ≥ 90

(Skeleton loaders, lazy images, and proper alt text should put us there.)

### Re-deploy

No backend changes affect the API contract except two new fields
(`signed_url` on reels, the entire `GET /api/videos` endpoint). Existing
clients (the current frontend) won't break — both additions are
additive. Vercel auto-deploys from `main`, so a single push covers it.

---

## Risks

1. **Backend change to `create_signed_upload_url()`** — if the Supabase
   JS client's `upload()` method is still being used elsewhere, those
   calls will fail. Mitigation: search for `storage.from_` and
   `supabase.storage` before changing; only the `/upload-url` route uses
   it for uploads.
2. **Signed URLs are short-lived** — if we hand the user a 1-hour signed
   URL, then they wait 2 hours to download, it expires. Mitigation:
   re-sign on page load, and surface a "link expired, refresh" error.
3. **Mobile drawer CSS** — Tailwind's `md:` breakpoint might not match
   our actual mobile width. Mitigation: test on Chrome devtools iPhone
   14 (390px) and Pixel 7 (412px) presets.

---

## Out-of-scope notes (for next iteration)

- Inline trim/crop editor
- Batch upload + queue
- A/B caption styles (font, color, position)
- Custom thumbnail picker
- Email notification when reels are ready
- "Reprocess this video with different Gemini settings" button
- Real-time progress via Supabase Realtime (vs. 2s polling)
