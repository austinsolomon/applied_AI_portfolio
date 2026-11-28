#!/usr/bin/env bash
# Build the GTM agent and deploy it to Cloud Run.
#
# Normally GitHub Actions runs this on every merge to main that changes gtm_agent/
# (.github/workflows/gtm-agent.yml). To deploy by hand from the repository root:
#   PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/deploy.sh
#
# Needs the one-time setup in deploy/one_time_setup/ to have been done once.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?set PROJECT_ID, e.g. applied-ai-portfolio}
REGION=${REGION:-us-central1}
SERVICE=${SERVICE:-gtm-agent}
RUNTIME_SA="gtm-agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # gtm_agent/, where the Dockerfile is

gcloud config set project "$PROJECT_ID"

# --quiet: never stop to ask a question, so the same command works in GitHub Actions.
# --allow-unauthenticated: Slack must be able to call the URL; the Slack route checks Slack's
#   signature on every request, and the web UI is switched off (DISABLE_WEB_UI=1).
# --no-cpu-throttling: keep CPU after the 200 response, so the agent run started in the
#   background isn't paused. Billed only while an instance is up; min instances is 0.
# --timeout 900: Slack gets its 200 at once; this only bounds the request itself.
# --set-secrets: Secret Manager values become environment variables in the container.
gcloud run deploy "$SERVICE" \
  --quiet \
  --source "$SOURCE_DIR" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --min-instances 0 \
  --max-instances 2 \
  --cpu 1 \
  --memory 2Gi \
  --timeout 900 \
  --set-env-vars "MODEL=claude-sonnet-5-5,ALLOWED_SLACK_USER_IDS=${ALLOWED_SLACK_USER_IDS:-}" \
  --set-secrets "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,SLACK_BOT_TOKEN=SLACK_BOT_TOKEN:latest,SLACK_SIGNING_SECRET=SLACK_SIGNING_SECRET:latest"

url=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo
echo "Service URL:        $url"
echo "Health check:       curl $url/health"
echo "Slack request URL:  $url/slack/events"
