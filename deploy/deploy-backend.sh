#!/usr/bin/env bash
# =============================================================
# One-shot deploy of the backend to Cloud Run.
#
# Prerequisites:
#   * gcloud CLI installed and authenticated (gcloud auth login)
#   * PROJECT_ID, REGION exported in your shell
#   * All Supabase + Gemini secrets exported (see env.prod.example)
#
# Usage:
#   ./deploy/deploy-backend.sh
#
# After the script finishes, the service URL is printed. Export it:
#   export BACKEND_URL=$(./deploy/deploy-backend.sh --print-url-only)
# =============================================================
set -euo pipefail

# ---- config (override via env) ----
: "${PROJECT_ID:?PROJECT_ID must be set (e.g. reels-generator-472819)}"
: "${REGION:=us-central1}"
: "${REPO:=reels-images}"
: "${SERVICE:=reels-backend}"

# ---- required secrets (validate but don't echo) ----
: "${SUPABASE_URL:?SUPABASE_URL required}"
: "${SUPABASE_KEY:?SUPABASE_KEY required (service_role)}"
: "${SUPABASE_JWT_SECRET:?SUPABASE_JWT_SECRET required}"
: "${GEMINI_API_KEY:?GEMINI_API_KEY required}"
: "${WORKER_SHARED_SECRET:?WORKER_SHARED_SECRET required}"
# FRONTEND_ORIGIN can be a placeholder on first deploy; we update it later
: "${FRONTEND_ORIGIN:=https://example.com}"
# WORKER_URL gets set to a placeholder on first deploy; we update it after
# the worker is up.
: "${WORKER_URL:=https://example.com/process}"

echo "==> Building ${SERVICE} image (Cloud Build, ~2 min)..."
gcloud builds submit \
  --config=deploy/cloudbuild-backend.yaml \
  --project="$PROJECT_ID" \
  --substitutions="_REGION=$REGION,_REPO=$REPO,_SERVICE=backend"

echo "==> Deploying ${SERVICE} to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:latest" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --timeout=60 \
  --concurrency=80 \
  --min-instances=0 \
  --set-env-vars="SUPABASE_URL=$SUPABASE_URL" \
  --set-env-vars="SUPABASE_KEY=$SUPABASE_KEY" \
  --set-env-vars="SUPABASE_JWT_SECRET=$SUPABASE_JWT_SECRET" \
  --set-env-vars="STORAGE_BUCKET=reels-videos" \
  --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY" \
  --set-env-vars="GEMINI_MODEL=gemini-2.5-flash" \
  --set-env-vars="FRONTEND_ORIGIN=$FRONTEND_ORIGIN" \
  --set-env-vars="WORKER_URL=$WORKER_URL" \
  --set-env-vars="WORKER_SHARED_SECRET=$WORKER_SHARED_SECRET" \
  --set-env-vars="MAX_VIDEO_SIZE_MB=500" \
  --project="$PROJECT_ID" \
  --format='value(status.url)'

echo
echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)'

echo
echo "Next step: deploy the worker, then update WORKER_URL on this service:"
echo "  gcloud run services update $SERVICE --region=$REGION \\"
echo "    --update-env-vars=WORKER_URL=https://WORKER-URL/process \\"
echo "    --project=$PROJECT_ID"
