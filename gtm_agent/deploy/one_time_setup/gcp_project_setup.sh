#!/usr/bin/env bash
# One-time setup of the Google Cloud project for the GTM agent.
#
# Run once from the repository root, after `gcloud auth login`:
#   PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/one_time_setup/gcp_project_setup.sh
#
# It enables the APIs, creates the runtime service account the app runs as, and makes sure the
# app's three secrets exist in Secret Manager. Secret values are read from your keyboard (nothing
# is echoed or saved in your shell history); press Enter to keep a value that's already stored.
# Safe to run again.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?set PROJECT_ID, e.g. applied-ai-portfolio}
RUNTIME_SA="gtm-agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
# Secret names match the environment variable names the app reads.
SECRETS=(ANTHROPIC_API_KEY SLACK_BOT_TOKEN SLACK_SIGNING_SECRET)

gcloud config set project "$PROJECT_ID"

echo "Enabling APIs (Cloud Run, Cloud Build, Artifact Registry, Secret Manager)…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

echo "Creating the runtime service account…"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create gtm-agent-runtime --display-name="GTM agent runtime"

# `gcloud run deploy --source` builds the image with Cloud Build, which runs as the project's
# default compute service account; it needs the Cloud Run builder role in new projects.
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
echo "Project setup done. Next: run gtm_agent/deploy/deploy.sh once, then github_actions_setup.sh."
