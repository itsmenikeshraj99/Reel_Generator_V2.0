# Deployment

This directory contains everything you need to deploy the project to
production. There are two halves:

| Doc                                | What it does                                            |
| ---------------------------------- | ------------------------------------------------------- |
| [`SETUP.md`](SETUP.md)             | Step-by-step: GCP project from zero → first Cloud Run deploy |
| [`SMOKE_TEST.md`](SMOKE_TEST.md)   | End-to-end test of the deployed stack                   |
| [`env.prod.example`](env.prod.example) | Template of the env vars to export before deploying  |
| [`deploy-backend.sh`](deploy-backend.sh) | One-shot script: build + deploy the backend         |
| [`deploy-worker.sh`](deploy-worker.sh)   | One-shot script: build + deploy the worker           |
| [`cloudbuild-backend.yaml`](cloudbuild-backend.yaml) | Cloud Build config for the backend         |
| [`cloudbuild-worker.yaml`](cloudbuild-worker.yaml)   | Cloud Build config for the worker           |
| [`cloudbuild-frontend.yaml`](cloudbuild-frontend.yaml) | Cloud Build config for the frontend (alternative to Vercel) |

## Architecture

```
Browser
  │  https://reels-generator-xxx.vercel.app
  ▼
Frontend (Vercel or Cloud Run)
  │  HTTPS + Supabase JWT
  ▼
Backend (Cloud Run: reels-backend)
  │  HTTPS, X-Worker-Secret
  ▼
Worker (Cloud Run: reels-worker)
  │
  ▼
Supabase + Gemini
```

## Cost expectations

~$5/mo for a demo-grade deployment (1–5 videos/day). See `SETUP.md` §
"Cost expectations" for the breakdown.

## Production hardening (Phase 11)

This is a **demo/portfolio** deploy. The following are deliberately
**out of scope** and should be added before real production traffic:

- **Secret Manager** — env vars are passed to `gcloud run deploy` in
  plain text on the command line. Use `--set-secrets=KEY=secret-name:latest`
  instead.
- **Cloud Tasks queue** — the backend calls the worker via HTTPS
  directly. A queue gives you retries, dead-letter, rate limiting.
- **Custom domain + Cloud CDN** — currently on the auto-generated
  `*.run.app` URL.
- **Monitoring** — no Sentry, no Cloud Monitoring alerts, no uptime check.
- **IAM-based auth** — currently the worker trusts any caller with
  `X-Worker-Secret`. Cloud Run IAM (`--no-allow-unauthenticated`) is
  more secure.
- **Postgres connection pooler** — the Supabase JS client opens a new
  connection per request. Add PgBouncer or Supavisor at ~50 concurrent
  users.
