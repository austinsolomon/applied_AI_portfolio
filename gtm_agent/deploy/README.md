# Deploy the harness to Cloud Run and talk to it from Slack

Goal: @mention the agent in Slack (or DM it), and the harness running on Cloud Run answers in
the thread. The harness is unchanged apart from a Slack route; it still answers questions about
its own code, with read, grep, safe-list bash, and its subagents.

```
Slack ──POST /slack/events──► Cloud Run "gtm-agent"
                                verify signature → 200 at once
                                background: run the harness → chat.postMessage in the thread
```

Cost: Cloud Run scales to zero; you pay only while an instance is up after a message (CPU stays
on after the reply so the background run isn't paused). Expect cents per month at portfolio
volume, plus Anthropic usage per message.

## 1. Create the GCP project (once)

In the [Cloud Console](https://console.cloud.google.com/projectcreate):

1. Project name `applied_ai_portfolio`; project ID `applied-ai-portfolio` (add a suffix if it's
   taken; IDs can't contain underscores). Link your existing billing account.
2. Billing → Budgets & alerts → Create budget: scope this project, $20/month, alerts at 50%, 90%,
   100%.

## 2. Create the Slack app (once)

1. https://api.slack.com/apps → **Create New App** → **From an app manifest** → your workspace →
   paste [`slack_app_manifest.yaml`](slack_app_manifest.yaml) → Create.
2. **Install App** → Install to workspace.
3. Keep two values for step 4 (don't paste them anywhere else):
   - **Basic Information → Signing Secret**
   - **OAuth & Permissions → Bot User OAuth Token** (starts with `xoxb-`)

## 3. Sign in to gcloud (once)

```bash
gcloud auth login
```

## 4. Set up APIs, the service account, and secrets (once)

From the repository root:

```bash
PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/cloud_run.sh setup
```

It enables Cloud Run, Cloud Build, Artifact Registry, and Secret Manager; creates the
`gtm-agent-runtime` service account; and makes sure three secrets exist, asking for each value
with hidden input: `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`. If a secret
already has a value (for example, created in the console or copied from another project), press
Enter to keep it. Run it again any time to replace a secret.

To copy existing secrets from another project without displaying them:

```bash
SRC=<source-project-id>; DST=applied-ai-portfolio
for S in ANTHROPIC_API_KEY APIFY_API_KEY BLITZ_API_KEY EXA_API_KEY; do
  gcloud secrets versions access latest --secret="$S" --project="$SRC" \
    | gcloud secrets create "$S" --project="$DST" --replication-policy=automatic --data-file=-
done
```

## 5. Deploy

```bash
PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/cloud_run.sh deploy
```

To limit who can use the agent, add your Slack user ID (Slack profile → ⋮ → Copy member ID):

```bash
ALLOWED_SLACK_USER_IDS=U0123456789 PROJECT_ID=applied-ai-portfolio gtm_agent/deploy/cloud_run.sh deploy
```

The first deploy builds the image with Cloud Build (a few minutes). It prints the service URL.
Check it:

```bash
curl https://<service-url>/healthz      # {"ok":true}
```

## 6. Point Slack at the service (once)

1. Slack app → **Event Subscriptions** → On → Request URL: `https://<service-url>/slack/events`.
   Slack sends a check; it should turn green ("Verified").
2. **Subscribe to bot events** → add `app_mention` and `message.im` → Save. Reinstall the app if
   Slack asks.

## 7. Try it

- DM the app: "What does this project do?"
- Or invite it to a channel (`/invite @gtm-agent`) and mention it.

It replies "Working on it…" in the thread, then the answer.

## Troubleshooting

| Symptom | Check |
|---|---|
| Slack says the URL didn't verify | `curl <url>/healthz`; the signing secret in Secret Manager matches the app's; logs: `gcloud run services logs read gtm-agent --region us-central1` |
| No reply in Slack | Event subscriptions saved with both events; the app is in the channel; logs show `Slack post failed: …` (e.g. `not_in_channel`, `invalid_auth`) |
| "not on this agent's allow list" | Your member ID is in `ALLOWED_SLACK_USER_IDS` |
| Build fails with a permissions error | Rerun `setup` (it grants Cloud Build's service account the Cloud Run builder role) |

## Security notes

- The URL is public so Slack can reach it. Every Slack request is checked against the signing
  secret, and the web UI routes are switched off (`DISABLE_WEB_UI=1`).
- Bash runs only safe-list commands (read-only: `ls`, `cat`, `git log`, …). Everything else is
  blocked, because nobody in Slack can approve it.
- The agent works on a throwaway copy of the image's code (`SANDBOX=temp-copy`).
- Secrets live in Secret Manager and reach the container as environment variables.
