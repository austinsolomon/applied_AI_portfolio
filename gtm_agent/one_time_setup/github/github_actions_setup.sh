#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy the GTM agent to Cloud Run, with no stored keys.
#
# Run once from the repository root, after gcp_project_setup.sh and one manual deploy.sh:
#   PROJECT_ID=applied-ai-portfolio gtm_agent/one_time_setup/github/github_actions_setup.sh
#
# What it creates:
#   - a deployer service account (the identity GitHub Actions acts as), allowed to build and
#     deploy in this project only
#   - a Workload Identity pool and provider: Google trusts GitHub's short-lived login token
#     (OIDC), but only for this repository's main branch
# At the end it prints three values to add in GitHub → Settings → Secrets and variables →
# Actions → Variables. None of them is secret.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?set PROJECT_ID, e.g. applied-ai-portfolio}
GITHUB_REPO=${GITHUB_REPO:-austinsolomon/applied_AI_portfolio}  # owner/name, exact case
POOL=github
PROVIDER=github-actions
DEPLOYER_NAME=gtm-agent-deployer
DEPLOYER="${DEPLOYER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="gtm-agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Enabling the APIs Workload Identity Federation needs…"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

echo "Creating the deployer service account…"
gcloud iam service-accounts describe "$DEPLOYER" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$DEPLOYER_NAME" --display-name="GTM agent deployer (GitHub Actions)"

# A new service account takes a few seconds to become visible to IAM; binding a role to it
# straight away can fail with "does not exist". Retry each binding for up to a minute.
retry() {
  for attempt in 1 2 3 4 5 6; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    echo "  waiting for the new service account to be ready (attempt $attempt)…"
    sleep 10
  done
  "$@" >/dev/null   # last try, showing the error if it still fails
}

# What `gcloud run deploy --source` needs: upload the source, start a Cloud Build, push the image
# to Artifact Registry, update the Cloud Run service (including allowing Slack to call it), and
# read the build's status.
for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer \
            roles/storage.admin roles/serviceusage.serviceUsageConsumer roles/logging.viewer; do
  retry gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER}" --role="$role" --condition=None
  echo "  granted $role"
done

# The deployer must be allowed to act as the runtime account (the service runs as it) and as the
# build account (Cloud Build runs as it).
for sa in "$RUNTIME_SA" "$BUILD_SA"; do
  retry gcloud iam service-accounts add-iam-policy-binding "$sa" \
    --member="serviceAccount:${DEPLOYER}" --role="roles/iam.serviceAccountUser"
  echo "  deployer can act as $sa"
done

echo "Creating the Workload Identity pool and GitHub provider…"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"

# The condition is the lock: only tokens from this repository's main branch are accepted.
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool="$POOL" --location=global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --workload-identity-pool="$POOL" --location=global \
    --display-name="GitHub Actions main branch" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/main'"

# Let tokens from that repository act as the deployer.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

cat <<EOF

Done. Add these three repository variables in GitHub:
  github.com/${GITHUB_REPO} → Settings → Secrets and variables → Actions → Variables tab → New repository variable

  GCP_PROJECT_ID                  ${PROJECT_ID}
  GCP_WORKLOAD_IDENTITY_PROVIDER  projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}
  GCP_DEPLOYER_SA                 ${DEPLOYER}

Optional (keeps your Slack allow list on every automatic deploy):
  ALLOWED_SLACK_USER_IDS          your Slack member ID(s), comma-separated
EOF
