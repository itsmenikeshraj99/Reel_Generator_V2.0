# Deployment

This directory contains everything you need to deploy the project to
production. The current target is **Railway** (backend + worker) +
**Vercel** (frontend) + **Supabase** (DB + auth + storage, already
live). Earlier versions targeted Google Cloud Run — see
[`SETUP.md`](SETUP.md#migration-back-to-cloud-run-when-you-get-a-visa)
for migration notes.

| Doc                                | What it does                                            |
| ---------------------------------- | ------------------------------------------------------- |
| [`SETUP.md`](SETUP.md)             | Step-by-step: from zero Railway/Vercel accounts → first deploy |
| [`SMOKE_TEST.md`](SMOKE_TEST.md)   | End-to-end test of the deployed stack                   |
| [`env.prod.example`](env.prod.example) | Template of the env vars to paste into Railway/Vercel |

## Architecture

```
Browser
  │  https://reels-generator-xxx.vercel.app
  ▼
Frontend (Vercel — Next.js 16)
  │  HTTPS + Supabase JWT
  ▼
Backend (Railway: reels-backend)
  │  Verifies JWT, calls Supabase, hands off to worker
  │  WORKER_URL env: http://reels-worker.railway.internal:8080/process
  │  WORKER_SHARED_SECRET env
  ▼
Worker (Railway: reels-worker)
  │  X-Worker-Secret auth
  │  Talks to Supabase + Gemini
  ▼
Supabase + Gemini
```

## Cost expectations

~$5/mo for a demo-grade deployment (1–5 videos/day). The Railway $5
trial credit usually covers this exactly, so you may pay $0. See
`SETUP.md` § "Cost expectations" for the breakdown.

## Why Railway (and not Render free, not Cloud Run)?

| Option              | Why we picked / skipped                            |
| ------------------- | -------------------------------------------------- |
| **Railway (chosen)**| No card, $5 credit/mo, no cold-start sleep, Docker-based |
| Render free         | Web service spins down after 15 min idle; worker is heavy (ffmpeg + 5-10 min renders) → would be unusable |
| Google Cloud Run    | Requires a Visa/Mastercard on file (RuPay not accepted for billing in India) |
| Fly.io              | Card required for the free tier overages            |
| Heroku              | No free tier since 2022                            |

## Production hardening (Phase 11)

This is a **demo/portfolio** deploy. The following are deliberately
**out of scope** and should be added before real production traffic:

- **Secret Manager** — env vars are stored in Railway's dashboard, which
  is fine for a single dev. For a team, move to Railway's encrypted
  variables or AWS/GCP Secret Manager.
- **Cloud Tasks / SQS queue** — the backend calls the worker via HTTPS
  directly. A queue gives you retries, dead-letter, rate limiting.
- **Custom domain + CDN** — currently on the auto-generated
  `*.up.railway.app` and `*.vercel.app` URLs.
- **Monitoring** — no Sentry, no uptime check. Use the Railway + Vercel
  built-in dashboards.
- **Worker auth** — the worker trusts any caller with
  `X-Worker-Secret`. For prod, add Railway's private network policy so
  the worker is only reachable from the backend service.
- **Postgres connection pooler** — the Supabase JS client opens a new
  connection per request. Add Supavisor at ~50 concurrent users.
- **Gemini fallback** — we already retry on 429/5xx, but the free tier
  15 RPM is the real ceiling. For real traffic, paid tier.
