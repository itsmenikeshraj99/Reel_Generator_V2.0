# Smoke test — local frontend → production backend → production worker

After you've deployed the backend, worker, and frontend (see
[`SETUP.md`](SETUP.md)), run this to confirm the whole stack works
end-to-end.

The "production" parts are the backend and worker on Cloud Run. The
frontend runs on your laptop — this is the cheapest, fastest way to
verify the deploy without spinning up a separate Vercel project.

---

## 0. Set up

```bash
export BACKEND_URL="https://reels-backend-xxx.a.run.app"
export WORKER_URL="https://reels-worker-xxx.a.run.app"
```

---

## 1. Health checks (no auth)

```bash
# Backend
curl -s $BACKEND_URL/health
# Expected: {"status":"ok","service":"reels-generator-api"}

# Worker
curl -s $WORKER_URL/health
# Expected: {"status":"ok","service":"reels-generator-worker","active_threads":0}
```

If either returns 5xx, the service is crashing on startup — check
Cloud Run logs:

```bash
gcloud run services logs read reels-backend --region=$REGION --limit=50
gcloud run services logs read reels-worker  --region=$REGION --limit=50
```

---

## 2. Auth checks (should 401)

```bash
# Backend without JWT
curl -s -o /dev/null -w "%{http_code}\n" \
  $BACKEND_URL/api/videos/00000000-0000-0000-0000-000000000000/status
# Expected: 401

# Worker without shared secret
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  $WORKER_URL/process
# Expected: 401
```

---

## 3. Full E2E via the browser

This is the real test. The frontend runs locally but talks to your
production backend and worker.

### 3a. Update the local frontend env

```bash
cd frontend
cat > .env.local <<EOF
NEXT_PUBLIC_SUPABASE_URL=$SUPABASE_URL
NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
NEXT_PUBLIC_API_URL=$BACKEND_URL/api
EOF
```

(Use the same Supabase project the backend uses. The frontend is
talking to Supabase directly for auth, but to your Cloud Run backend
for everything else.)

### 3b. Start the frontend

```bash
npm run dev
# → http://localhost:3000
```

### 3c. Walk the flow

1. Open http://localhost:3000
2. Sign up with a real email (Supabase requires confirmation unless you
   disabled it; check your inbox)
3. Land on the dashboard
4. Click **Upload Video** → pick a short MP4 (under 100 MB for the
   first run, to keep it cheap)
5. Click **Generate AI Reels ✨**
6. Watch the status page advance through the stages:
   `VALIDATING → TRANSCRIBING_PLANNING → REVIEWING → RENDERING → READY`
7. Open the gallery, click play, click download

If any stage hangs for more than 2 minutes, the worker is probably
timing out. Check:

```bash
gcloud run services logs read reels-worker --region=$REGION --limit=100
```

Common errors:
- **`Connection refused` to Supabase** — the env var on the worker is
  wrong. Re-deploy with the right `SUPABASE_URL` / `SUPABASE_KEY`.
- **`Invalid API key`** on Gemini — the `GEMINI_API_KEY` is wrong.
- **`Permission denied` on storage upload** — the worker's
  `SUPABASE_KEY` is the **anon** key, not the service role. Fix the env
  var and redeploy.

---

## 4. E2E via curl (no browser)

If you want to verify the API shape without the UI:

```bash
# Sign in via Supabase Auth to get a JWT
TOKEN=$(curl -s -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $NEXT_PUBLIC_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# 1. Get an upload URL
RESP=$(curl -s -X POST "$BACKEND_URL/api/videos/upload-url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"smoke.mp4"}')
echo "$RESP"
UPLOAD_URL=$(echo "$RESP" | python -c "import json,sys; print(json.load(sys.stdin)['upload_url'])")
VIDEO_ID=$(echo "$RESP" | python -c "import json,sys; print(json.load(sys.stdin)['video_id'])")

# 2. Upload the file to the signed URL
curl -s -X PUT "$UPLOAD_URL" \
  -H "Content-Type: video/mp4" \
  --data-binary "@./test-video.mp4"

# 3. Kick off processing
curl -s -X POST "$BACKEND_URL/api/videos/$VIDEO_ID/process" \
  -H "Authorization: Bearer $TOKEN"

# 4. Poll status
while true; do
  STATUS=$(curl -s "$BACKEND_URL/api/videos/$VIDEO_ID/status" \
    -H "Authorization: Bearer $TOKEN" \
    | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('current_stage', d.get('status','?')))")
  echo "  stage: $STATUS"
  if [ "$STATUS" = "READY" ] || [ "$STATUS" = "FAILED" ]; then break; fi
  sleep 5
done

# 5. Get the reel
curl -s "$BACKEND_URL/api/reels/$VIDEO_ID" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 5. Tear down

When you're done with the demo:

```bash
# Stop paying for Cloud Run
gcloud run services delete reels-backend --region=$REGION --quiet
gcloud run services delete reels-worker  --region=$REGION --quiet

# Or nuke the whole project
gcloud projects delete $PROJECT_ID
```

The free tier of Supabase and Gemini can stay — they don't bill you
unless you exceed the free quota.
