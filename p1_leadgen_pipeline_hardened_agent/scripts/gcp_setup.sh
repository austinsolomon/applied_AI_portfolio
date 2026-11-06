#!/usr/bin/env bash
# One-time Google Cloud setup for the weekly lead-gen job, as it was run on 2026-11-05.
# Kept as a record and for rebuilding the project; read it before running it again.
# Prerequisites: a project with billing linked, `gcloud` signed in as an owner, the database URL
# saved as the DATABASE_URL secret, and `leadgen db migrate` already applied to that database.
set -euo pipefail

PROJECT=leadgen-prod-austin
PROJECT_NUMBER=1001406310514
REGION=us-west1                      # Oregon, next to the Neon database in AWS us-west-2
REPO=austinsolomon/applied_AI_portfolio
REPO_ID=1414642796                   # gh api repos/$REPO -q .id (immutable, unlike the name)
SA_DOMAIN="$PROJECT.iam.gserviceaccount.com"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/leadgen/leadgen:${TAG:?set TAG to an image tag}"

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com \
    iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com

# Image registry and one service account per job role.
gcloud artifacts repositories create leadgen --repository-format=docker --location="$REGION"
for account in leadgen-runner leadgen-migrator leadgen-scheduler leadgen-deployer; do
    gcloud iam service-accounts create "$account"
done

# Secrets: DATABASE_URL (owner) is saved by hand; the API keys come from the operator's .env;
# APP_DATABASE_URL and DASHBOARD_DATABASE_URL come from scripts/provision_database_roles.py.
for secret in APP_DATABASE_URL ANTHROPIC_API_KEY ANTHROPIC_WORKSPACE_ID EXA_API_KEY \
    APIFY_API_KEY BLITZ_API_KEY FIRECRAWL_API_KEY; do
    gcloud secrets add-iam-policy-binding "$secret" \
        --member="serviceAccount:leadgen-runner@$SA_DOMAIN" --role=roles/secretmanager.secretAccessor
done
gcloud secrets add-iam-policy-binding DATABASE_URL \
    --member="serviceAccount:leadgen-migrator@$SA_DOMAIN" --role=roles/secretmanager.secretAccessor

# The deployer pushes images, updates and runs the jobs, and deploys them as their accounts.
gcloud artifacts repositories add-iam-policy-binding leadgen --location="$REGION" \
    --member="serviceAccount:leadgen-deployer@$SA_DOMAIN" --role=roles/artifactregistry.writer
gcloud projects add-iam-policy-binding "$PROJECT" --condition=None \
    --member="serviceAccount:leadgen-deployer@$SA_DOMAIN" --role=roles/run.developer
for account in leadgen-runner leadgen-migrator; do
    gcloud iam service-accounts add-iam-policy-binding "$account@$SA_DOMAIN" \
        --member="serviceAccount:leadgen-deployer@$SA_DOMAIN" --role=roles/iam.serviceAccountUser
done

# Trust link: only GitHub Actions on this repository's main branch may act as the deployer.
gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc applied-ai-portfolio --location=global \
    --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository_id=='$REPO_ID' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "leadgen-deployer@$SA_DOMAIN" \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository_id/$REPO_ID"

# Jobs. Retries stay at 0: a failed paid run is reconciled by a person, never blindly rerun.
gcloud run jobs create leadgen-migrate --image="$IMAGE" --region="$REGION" --args=db,migrate \
    --service-account="leadgen-migrator@$SA_DOMAIN" --set-secrets=DATABASE_URL=DATABASE_URL:latest \
    --tasks=1 --max-retries=0 --task-timeout=10m --cpu=1 --memory=512Mi
gcloud run jobs create leadgen-weekly --image="$IMAGE" --region="$REGION" --args=weekly \
    --service-account="leadgen-runner@$SA_DOMAIN" \
    --set-secrets=DATABASE_URL=APP_DATABASE_URL:latest,ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,ANTHROPIC_WORKSPACE_ID=ANTHROPIC_WORKSPACE_ID:latest,EXA_API_KEY=EXA_API_KEY:latest,APIFY_API_KEY=APIFY_API_KEY:latest,BLITZ_API_KEY=BLITZ_API_KEY:latest,FIRECRAWL_API_KEY=FIRECRAWL_API_KEY:latest \
    --tasks=1 --max-retries=0 --task-timeout=45m --cpu=1 --memory=2Gi

# Fridays 08:00 Pacific. Created paused; resume it only after a supervised first run.
gcloud run jobs add-iam-policy-binding leadgen-weekly --region="$REGION" \
    --member="serviceAccount:leadgen-scheduler@$SA_DOMAIN" --role=roles/run.invoker
gcloud scheduler jobs create http leadgen-weekly-friday --location="$REGION" \
    --schedule="0 8 * * 5" --time-zone="America/Los_Angeles" --http-method=POST \
    --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/leadgen-weekly:run" \
    --oauth-service-account-email="leadgen-scheduler@$SA_DOMAIN"
gcloud scheduler jobs pause leadgen-weekly-friday --location="$REGION"
