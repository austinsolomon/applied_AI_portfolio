#!/usr/bin/env bash
# Deploy the harness to Cloud Run in the applied_ai_portfolio GCP project.
#
# Run from the repository root after `gcloud auth login`:
#   PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/cloud_run.sh setup    # once: APIs, secrets, service account
#   PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/cloud_run.sh deploy   # every time the code changes
#
# Secrets are read from your keyboard (nothing is echoed or saved in your shell history) and
# stored in Secret Manager. See deploy/README.md for the full walkthrough.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?set PROJECT_ID, e.g. applied-ai-portfolio}
REGION=${REGION:-us-central1}
SERVICE=${SERVICE:-gtm-agent}
RUNTIME_SA="gtm-agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
# Secret names match the environment variable names (the same convention as the leadgen project).
SECRETS=(ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_SIGNING_SECRET)
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

setup() {
  gcloud config set project "$PROJECT_ID"

  echo "Enabling APIs (Cloud Run, Cloud Build, Artifact Registry, Secret Manager)…"
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com secretmanager.googleapis.com

  echo "Creating the runtime service account…"
  gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
    gcloud iam service-accounts create gtm-agent-runtime --display-name="GTM agent runtime"

  # `gcloud run deploy --source` builds the image with Cloud Build, which runs as the project's
  # default compute service account; it needs the Cloud Run builder role in new projects.
  local project_number
  project_number=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${project_number}-compute@developer.gserviceaccount.com" \
    --role="roles/run.builder" --condition=None >/dev/null

  for name in "${SECRETS[@]}"; do
    gcloud secrets describe "$name" >/dev/null 2>&1 ||
      gcloud secrets create "$name" --replication-policy=automatic
    read -r -s -p "Value for $name (input hidden, Enter to keep the current value): " value
    echo
    if [[ -n "$value" ]]; then
      printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
      echo "  stored a new version of $name"
    fi
    gcloud secrets add-iam-policy-binding "$name" \
      --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null
  done
  echo "Setup done. Next: $0 deploy"
}

deploy() {
  gcloud config set project "$PROJECT_ID"
  # --allow-unauthenticated: Slack must be able to call the URL; the Slack route checks Slack's
  #   signature on every request, and the web UI is switched off (DISABLE_WEB_UI=1).
  # --no-cpu-throttling: keep CPU after the 200 response, so the agent run started in the
  #   background isn't paused. Billed only while an instance is up; min instances is 0.
  # --timeout 900: Slack gets its 200 at once; this only bounds the request itself.
  # --quiet: never stop to ask a question, so the same command works in GitHub Actions.
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

  local url
  url=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
  echo
  echo "Service URL:        $url"
  echo "Health check:       curl $url/health"
  echo "Slack request URL:  $url/slack/events"
}

case "${1:-}" in
  setup) setup ;;
  deploy) deploy ;;
  *) echo "usage: PROJECT_ID=... $0 {setup|deploy}" >&2; exit 2 ;;
esac
