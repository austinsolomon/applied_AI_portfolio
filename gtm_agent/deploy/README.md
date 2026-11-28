# Deploying the GTM agent

Deploys are automatic. Merge a pull request that changes `gtm_agent/` and GitHub Actions
(`.github/workflows/gtm-agent.yml`) runs the tests, then `deploy.sh`, then a health check.
About 3 minutes later the new version answers in Slack.

```
PR opened → tests
PR merged → tests → sign in to Google (Workload Identity Federation) → deploy.sh → /health
```

## Files

| File | Runs | What it does |
|---|---|---|
| `deploy.sh` | On every merge (by GitHub Actions), or by hand | Builds the container with Cloud Build and deploys it to the Cloud Run service `gtm-agent` |
| `../one_time_setup/` | Once per project (not part of deploys) | GCP project setup, GitHub deploy trust, Slack app manifest, and the step-by-step guide ([README](../one_time_setup/README.md)) |

## Deploy by hand (rarely needed)

From the repository root, after `gcloud auth login`:

```bash
PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/deploy.sh
```

Or rerun the last automatic deploy without a code change: GitHub → **Actions** → **GTM agent** →
**Run workflow**.

## Check it's running

```bash
curl https://gtm-agent-r5df32yi3a-uc.a.run.app/health
gcloud run services logs read gtm-agent --region us-central1 --project applied-ai-portfolio --limit 30
```
