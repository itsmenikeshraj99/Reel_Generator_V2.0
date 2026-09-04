# Deploy to Google Cloud Run

This is a **demo/portfolio** deployment. The backend and worker run on
[Cloud Run](https://cloud.google.com/run); the frontend can run on
[Vercel](https://vercel.com) (recommended) or also on Cloud Run.

**What this gives you**

- Public HTTPS URL for the backend
- Auto-scaling, pay-per-use workers
- Zero ops — no VM, no Docker daemon, no Kubernetes

**What this doesn't give you** (Phase 11 hardening if you outgrow the demo)

- Cloud Tasks queue (we call the worker via HTTPS directly)
- Secret Manager (env vars are passed in plain text to `gcloud run deploy`)
- Custom domain + Cloud CDN
- Monitoring / alerting
- IAM-based auth (we use the existing JWT / X-Worker-Secret in-app)

---

## 0. Prerequisites

- A Google account
- A Supabase project (you already have one — see `db/schema.sql`)
- A Gemini API key (https://aistudio.google.com/app/apikey)
- About 30 minutes

> The steps below assume `gcloud` is not yet installed. If you have it, skip to step 2.

---

## 1. Install the Google Cloud CLI

### macOS
```bash
brew install --cask google-cloud-sdk
```

### Windows (PowerShell)
```powershell
(New-Object Net.WebClient).DownloadFile("https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe", "$env:Temp\GoogleCloudSDKInstaller.exe")
& $env:Temp\GoogleCloudSDKInstaller.exe
```

### Linux
```bash
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
```

### Verify
```bash
gcloud --version
```

---

## 2. Create a GCP project and link billing

1. Open https://console.cloud.google.com/projectcreate
2. **Project name:** `reels-generator` (or whatever you want)
3. **Location:** your org (or "No organization" for personal)
4. Click **Create**
5. **Link a billing account** — Cloud Run requires it. https://console.cloud.google.com/billing
   - Even on the free tier, you need a billing account on file.

Save your **Project ID** (not the project name — the ID is lowercase and may have a
random suffix). Example: `reels-generator-472819`.

Export it for the rest of this doc:

```bash
export PROJECT_ID="reels-generator-472819"   # ← your actual ID
gcloud config set project $PROJECT_ID
```

---

## 3. Enable the APIs you need

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project=$PROJECT_ID
```

- `run.googleapis.com` — Cloud Run
- `cloudbuild.googleapis.com` — to build images without local Docker
- `artifactregistry.googleapis.com` — to store the built images

---

## 4. Pick a region

Pick one and stick with it. Common choices:

- `us-central1` (Iowa — cheapest)
- `europe-west1` (Belgium — close to most EU users)
- `asia-south1` (Mumbai — close to your Supabase project)

```bash
export REGION="us-central1"
```

---

## 5. Create an Artifact Registry repo

This is where your Docker images will live.

```bash
gcloud artifacts repositories create reels-images \
  --repository-format=docker \
  --location=$REGION \
  --project=$PROJECT_ID
```

---

## 6. Generate a fresh worker shared secret

The backend and worker authenticate to each other with a shared secret.
**Use a different value than your local dev secret.**

```bash
openssl rand -base64 48
# Or on Windows PowerShell:
# [Convert]::ToBase64String((1..48|ForEach-Object{Get-Random -Maximum 256}))
```

Save the output:

```bash
export WORKER_SHARED_SECRET="paste-the-output-here"
```

---

## 7. Gather your Supabase + Gemini values

From your Supabase Dashboard → **Settings → API**:

| What                | Where to find it                                                |
| ------------------- | --------------------------------------------------------------- |
| `SUPABASE_URL`      | Project URL                                                     |
| `SUPABASE_KEY`      | `service_role` key (⚠️ backend/worker only — never expose to the browser) |
| `SUPABASE_JWT_SECRET` | JWT Secret (under "JWT Secret" section)                       |

From https://aistudio.google.com/app/apikey:

| What              | Notes                                        |
| ----------------- | -------------------------------------------- |
| `GEMINI_API_KEY`  | The AIza… string                              |

---

## 8. Deploy the backend

```bash
cd backend
gcloud builds submit --config=../deploy/cloudbuild-backend.yaml \
  --project=$PROJECT_ID \
  --substitutions=_REGION=$REGION

gcloud run deploy reels-backend \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/reels-images/backend:latest \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi --cpu=1 \
  --timeout=60 \
  --concurrency=80 \
  --set-env-vars="SUPABASE_URL=$SUPABASE_URL" \
  --set-env-vars="SUPABASE_KEY=$SUPABASE_KEY" \
  --set-env-vars="SUPABASE_JWT_SECRET=$SUPABASE_JWT_SECRET" \
  --set-env-vars="STORAGE_BUCKET=reels-videos" \
  --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY" \
  --set-env-vars="GEMINI_MODEL=gemini-2.5-flash" \
  --set-env-vars="FRONTEND_ORIGIN=$FRONTEND_ORIGIN" \
  --set-env-vars="WORKER_URL=https://PLACEHOLDER-set-after-worker-deploy/process" \
  --set-env-vars="WORKER_SHARED_SECRET=$WORKER_SHARED_SECRET" \
  --set-env-vars="MAX_VIDEO_SIZE_MB=500" \
  --project=$PROJECT_ID
```

The output ends with a line like:

```
Service URL: https://reels-backend-xyz-uc.a.run.app
```

Save that:

```bash
export BACKEND_URL="https://reels-backend-xyz-uc.a.run.app"
```

> **Don't set `WORKER_URL` yet** — we don't have the worker URL. We'll come back and update it.

---

## 9. Deploy the worker

```bash
cd ../worker
gcloud builds submit --config=../deploy/cloudbuild-worker.yaml \
  --project=$PROJECT_ID \
  --substitutions=_REGION=$REGION

gcloud run deploy reels-worker \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/reels-images/worker:latest \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi --cpu=2 \
  --timeout=900 \
  --concurrency=1 \
  --min-instances=1 \
  --set-env-vars="SUPABASE_URL=$SUPABASE_URL" \
  --set-env-vars="SUPABASE_KEY=$SUPABASE_KEY" \
  --set-env-vars="SUPABASE_JWT_SECRET=$SUPABASE_JWT_SECRET" \
  --set-env-vars="STORAGE_BUCKET=reels-videos" \
  --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY" \
  --set-env-vars="GEMINI_MODEL=gemini-2.5-flash" \
  --set-env-vars="WORKER_SHARED_SECRET=$WORKER_SHARED_SECRET" \
  --set-env-vars="WORKER_HOST=0.0.0.0" \
  --set-env-vars="WORKER_MAX_PARALLEL=2" \
  --project=$PROJECT_ID
```

Save the worker URL:

```bash
export WORKER_URL_BASE="https://reels-worker-abc-uc.a.run.app"
```

### Notes on the worker flags

- **`--memory=2Gi --cpu=2`** — render takes 5–10 min and ffmpeg is multi-threaded. 1 GB is too tight.
- **`--timeout=900`** — 15 min. The render stage can take 10 min for a long video.
- **`--concurrency=1`** — single concurrent HTTP request. The worker's internal `ThreadPoolExecutor` (size 2) handles the actual pipeline parallelism.
- **`--min-instances=1`** — avoid the 10–20s cold start. Costs ~$5/mo at this size but worth it for UX.

---

## 10. Wire the backend to the worker

Now that you have the worker URL, update the backend's `WORKER_URL` and redeploy:

```bash
gcloud run services update reels-backend \
  --region=$REGION \
  --update-env-vars="WORKER_URL=$WORKER_URL_BASE/process" \
  --project=$PROJECT_ID
```

This is a no-downtime update — Cloud Run handles the rollout.

---

## 11. Deploy the frontend

### Recommended: Vercel

1. Push your code to GitHub (you already did — https://github.com/itsmenikeshraj99/Reel_Generator_V2.0)
2. Open https://vercel.com/new
3. **Import** your GitHub repo
4. **Root Directory:** `frontend`
5. **Framework Preset:** Next.js (auto-detected)
6. **Environment Variables:**
   ```
   NEXT_PUBLIC_SUPABASE_URL        = https://owwojzonjbnnumqukshs.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY   = <your anon key>
   NEXT_PUBLIC_API_URL             = $BACKEND_URL/api
   ```
7. Click **Deploy**

Save the Vercel URL:

```bash
export FRONTEND_ORIGIN="https://reels-generator-xxx.vercel.app"
```

### Alternative: Cloud Run

```bash
cd ../frontend
gcloud builds submit --config=../deploy/cloudbuild-frontend.yaml \
  --project=$PROJECT_ID \
  --substitutions=_REGION=$REGION

gcloud run deploy reels-frontend \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/reels-images/frontend:latest \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi --cpu=1 \
  --set-env-vars="NEXT_PUBLIC_SUPABASE_URL=$SUPABASE_URL" \
  --set-env-vars="NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  --set-env-vars="NEXT_PUBLIC_API_URL=$BACKEND_URL/api" \
  --project=$PROJECT_ID
```

> Note: the Next.js Cloud Build config (`deploy/cloudbuild-frontend.yaml`) uses `next build` which takes 2–3 min. The `next.config.js` standalone output is the next iteration.

---

## 12. Update CORS on the backend

The backend needs to know the deployed frontend URL. Update it:

```bash
gcloud run services update reels-backend \
  --region=$REGION \
  --update-env-vars="FRONTEND_ORIGIN=$FRONTEND_ORIGIN" \
  --project=$PROJECT_ID
```

The backend reads this as a comma-separated list, so if you have multiple
URLs (Vercel preview, custom domain, localhost dev) you can pass them all:

```bash
--update-env-vars="FRONTEND_ORIGIN=https://reels-xxx.vercel.app,http://localhost:3000"
```

---

## 13. Sanity checks

```bash
# Backend health
curl $BACKEND_URL/health
# → {"status": "ok", "service": "reels-generator-api"}

# Worker health
curl $WORKER_URL_BASE/health
# → {"status": "ok", "service": "reels-generator-worker", "active_threads": 0}

# Backend auth (no JWT → 401)
curl -i $BACKEND_URL/api/videos/00000000-0000-0000-0000-000000000000/status
# → HTTP/2 401

# Worker auth (no secret → 401)
curl -i -X POST $WORKER_URL_BASE/process -H 'Content-Type: application/json' -d '{}'
# → HTTP/2 401
```

All four checks should pass.

---

## 14. Full smoke test

See [`SMOKE_TEST.md`](SMOKE_TEST.md) for the end-to-end test
(local frontend → prod backend → prod worker).

---

## Cost expectations (demo / portfolio)

At 1–5 videos/day on the free tier:

| Item              | Cost                                |
| ----------------- | ----------------------------------- |
| Cloud Run backend | ~$0 (free tier covers 2M requests/mo) |
| Cloud Run worker  | ~$5/mo (min-instances=1)            |
| Artifact Registry | ~$0.10/GB/mo (image is ~1.5GB)      |
| Cloud Build       | ~$0 (free tier: 120 build-min/day)  |
| Supabase          | Free tier (500MB DB, 1GB storage)   |
| Gemini API        | Free tier: 15 RPM                   |

**Total: ~$5/mo** for a demo.

To shut it all down, delete the project:

```bash
gcloud projects delete $PROJECT_ID
```

---

## Troubleshooting

### Worker cold start takes 30+ seconds
You forgot `--min-instances=1` on the worker. Update the deploy.

### "Permission denied" on backend auth
Your Supabase JWT secret is wrong. Re-check the value in
Supabase Dashboard → Settings → API → JWT Secret.

### "Worker rejected the shared secret"
The `WORKER_SHARED_SECRET` env var on the backend and worker don't match.
Re-set them on both.

### CORS error in the browser console
`FRONTEND_ORIGIN` on the backend doesn't exactly match the URL the user is
visiting (no trailing slash, exact protocol, no `www` mismatch).

### Build fails: "Docker daemon not running"
You're trying to build locally. Use the `cloudbuild-*.yaml` configs (steps
above) which build on Google's servers.

### Worker `/tmp` full
`/tmp` is 50% of memory. With 2GB, that's 1GB. If your source video is
larger than ~500MB, the worker will fail. Either:
- raise `--memory=4Gi` (more headroom but pricier), or
- lower `MAX_VIDEO_SIZE_MB` on the backend to reject large uploads upstream.
