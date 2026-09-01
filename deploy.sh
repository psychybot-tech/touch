#!/bin/bash
# ─────────────────────────────────────────────
# Deploy Twitter→Telegram bot to Google Cloud Run
# Prerequisites:
#   1. Install gcloud CLI: https://cloud.google.com/sdk/docs/install
#   2. Run: gcloud auth login
#   3. Run: gcloud config set project YOUR_PROJECT_ID
# ─────────────────────────────────────────────

set -e

# ── CONFIGURE THESE ──────────────────────────
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
SERVICE_NAME="twitter-telegram-bot"
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"
# ─────────────────────────────────────────────

echo "🔧 Building Docker image..."
gcloud builds submit --tag "$IMAGE" .

echo "🚀 Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --no-allow-unauthenticated \
  --min-instances 1 \
  --max-instances 1 \
  --memory 256Mi \
  --set-env-vars "TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN,TELEGRAM_CHANNEL_ID=$TELEGRAM_CHANNEL_ID,TWITTER_USERNAME=$TWITTER_USERNAME,NITTER_INSTANCES=$NITTER_INSTANCES,POLL_INTERVAL_SECONDS=$POLL_INTERVAL_SECONDS"

echo "✅ Bot deployed and running on Google Cloud Run!"
echo "   View logs: gcloud run services logs read $SERVICE_NAME --region $REGION"
