# Smoke test — local frontend → Railway backend → Railway worker

After you've deployed the backend + worker to Railway and the frontend
to Vercel (see [`SETUP.md`](SETUP.md)), run this to confirm the whole
stack works end-to-end.

The "production" parts are the backend and worker on Railway. The
frontend runs on your laptop — this is the cheapest, fastest way to
verify the deploy without redeploying the frontend.

---

## 0. Set up

```bash
export BACKEND_URL="https://reels-backend-production-xxxx.up.railway.app"
export WORKER_URL="https://reels-worker-production-xxxx.up.railway.app"
export FRONTEND_URL="https://reels-generator-xxx.vercel.app"   # only for the curl E2E in §4
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

If either returns 5xx, the service is crashing on startup — check the
Railway logs (open the service → **Logs** tab).

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
hanging. Open Railway → `reels-worker` → **Logs** and look for the
last log line.

Common errors:
- **`Connection refused` to Supabase** — the env var on the worker is
  wrong. Update the **Variables** tab on the `reels-worker` service.
- **`Invalid API key`** on Gemini — the `GEMINI_API_KEY` is wrong.
- **`Permission denied` on storage upload** — the worker's
  `SUPABASE_KEY` is the **anon** key, not the service role. Fix the env
  var and Railway will redeploy.

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

- **Railway:** open the project → **Settings** → **Delete Project**. Removes
  the backend and worker in one click.
- **Vercel:** open the project → **Settings** → **Delete Project**.
- **Supabase:** keep the free tier; pause the project from the dashboard
  if you want to be sure no traffic hits it.
- **Gemini:** keep the free tier; revoke the API key from
  <https://aistudio.google.com/app/apikey> if you're worried.

You can also just leave everything running — the $5 Railway credit
covers a low-traffic demo indefinitely, and the Vercel + Supabase +
Gemini free tiers don't bill you unless you exceed quotas.
