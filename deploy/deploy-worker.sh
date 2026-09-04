#!/usr/bin/env bash
# =============================================================
# One-shot deploy of the worker to Cloud Run.
#
# Worker is the heavy service — 2Gi memory, 900s timeout, 1
# concurrent request (the worker's internal thread pool handles
# the actual pipeline parallelism).
#
# Usage: see deploy-backend.sh for prerequisites.
# =============================================================
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID required}"
: "${REGION:=us-central1}"
: "${REPO:=reels-images}"
: "${SERVICE:=reels-worker}"

: "${SUPABASE_URL:?SUPABASE_URL required}"
: "${SUPABASE_KEY:?SUPABASE_KEY required (service_role)}"
: "${SUPABASE_JWT_SECRET:=}"  # not strictly needed on worker, keep empty
: "${GEMINI_API_KEY:?GEMINI_API_KEY required}"
: "${WORKER_SHARED_SECRET:?WORKER_SHARED_SECRET required (must match backend)}"

echo "==> Building ${SERVICE} image (Cloud Build, ~5 min — ffmpeg+opencv are heavy)..."
gcloud builds submit \
  --config=deploy/cloudbuild-worker.yaml \
  --project="$PROJECT_ID" \
  --substitutions="_REGION=$REGION,_REPO=$REPO,_SERVICE=worker"

echo "==> Deploying ${SERVICE} to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/worker:latest" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
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
  --project="$PROJECT_ID" \
  --format='value(status.url)'

echo
echo "==> Done. Service URL:"
WORKER_URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
echo "$WORKER_URL"

echo
echo "Next step: point the backend at this worker:"
echo "  gcloud run services update reels-backend --region=$REGION \\"
echo "    --update-env-vars=WORKER_URL=$WORKER_URL/process \\"
echo "    --project=$PROJECT_ID"
