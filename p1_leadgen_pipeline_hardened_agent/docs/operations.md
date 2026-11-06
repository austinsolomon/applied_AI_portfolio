# Operating an approved run

Start Postgres, migrate the schema, seed the demo once, and load Census places using the README
quickstart. Keep credentials in the ignored `.env`; the container excludes that file from its build.
Cloud hosting and automated scheduling are not configured by this repository.

`leadgen plan` creates a draft and asks for approval once. Check the cell count, target and total
budget. `leadgen run --plan ID` executes it. A completed run can be queried again without new paid
work. The SDK is limited to the pipeline's MCP stage tool; changing scope requires a new plan.

## Run close-out

A run is reported `finished` only after a completion check (`leadgen.run_closeout`) confirms that
it leaves nothing open: every stage checkpointed, no queued or running jobs, and no reservation
still `started` or `uncertain`. A paid call that raised (a Firecrawl timeout, a 5xx) is over once
the call returns; only its charge is unknown. Close-out books it at its reservation ceiling as an
`estimated` cost-ledger row (`endpoint` `unknown-outcome`) and settles the reservation, so the next
run never waits on it. This also happens when a run fails, and `leadgen weekly` applies it to
runs that ended before the check existed.

An Apify actor run is the exception. It can keep running and charging after the call that started
it failed, so its reservation is never settled by estimate. A run that leaves one open ends
`failed` with `Run is not complete: paid calls still open: maps:...`, and the next run stays
blocked until an operator confirms in Apify that the actor stopped and reconciles it (below).

## Failure recovery

Read `runs`, `pipeline_stages`, `run_ledger`, `traces`, and `budget_reservations` for the run. Check
`cost_ledger` and the relevant provider's usage or run record before making any change. A timeout
is ambiguous: it can mean the request was accepted and billed. Never clear a reservation just
because the process stopped.

When provider records establish the outcome, an operator may call
`leadgen.budget.reconcile_operation(connection, reservation_id, outcome, note)`.
Use `succeeded` only after recording the actual cost and persisting the result (or an explicitly
marked conservative cost estimate and review outcome when final usage is unavailable); use `rejected` only
when the provider confirms no operation started. The required note records the evidence in traces.
This function is deliberately absent from the agent's tools.

Then `leadgen run --plan ID --resume` continues the same failed run. It refuses to start while any
reservation is unresolved and skips completed stages. A successful paid operation whose downstream
checkpoint is missing requires restoring that checkpoint from the persisted provider result before
resuming; the runner will not silently repeat it. Running processes must be stopped and their
provider operations reconciled before a failed run can be resumed.

Queue retries are suitable for repeatable work. Pipeline enrichment uses one attempt because a
provider timeout can already have consumed credits. General queue consumers may choose a larger
attempt limit. Do not requeue a job whose worker may still be running an external operation.

## Weekly unattended runs

`leadgen weekly` approves plans by policy (`config/weekly_policy.yaml`), not by a person. Before
changing the policy, run `leadgen weekly --dry-run`: it shows the market, cities and ceilings the
policy would approve and stores nothing. Each approval records the policy file's git blob id
(`policy:weekly@<sha>`), so `git log` on the policy file shows which limits approved a run.

| Exit | What to do |
|---|---|
| 0 | Nothing. The summary is in the output, in `traces` (operation `weekly`) and in the dashboard |
| 1 | The run failed. Read the summary's `error`, then follow Failure recovery above; the next weekly run stays blocked only while an Apify actor may still be running |
| 2 | Nothing. It already ran within the cadence, or no market is below goal |
| 3 | Act on the reason: run `leadgen db migrate`, confirm and reconcile an open Apify actor run, or raise `budget_usd` or shrink the plan in the policy file |

The weekly command never migrates and never retries a failed run by itself. Retrying paid work is
always an operator decision.

## Review and evidence

`leadgen evals review` opens the review UI (also the dashboard's Eval review tab). It follows the
error-analysis loop of Hamel Husain's "Your AI Product Needs Evals": work through the queue one
judge decision at a time (unlabeled `review` verdicts first), read the evidence page, pick your own
verdict while blind mode hides the judge's, then answer "Was the judge right?" Pass or Fail; a fail
takes a one-line critique, the verdict it should have been and failure-mode tags (the list is
`eval_failure_modes` in `config/category.yaml`). Use the Judge alignment panel's per-verdict
precision and recall, and the failure-mode counts, to decide what to change in the judge
requirement or rubric. Then export fixtures with `leadgen evals export-fixtures --out out/evals`
(or the page's JSONL download); both carry your verdict, failure modes and critique. Review and redact business contact data and page
text before moving any fixture into version control. The local database contains the full evidence;
the public sample report contains business names and aggregate counts only.

Unit prices convert Firecrawl credits and Blitz records into accounting costs. Set them for the
actual subscription. Provider balance or price changes require checking these assumptions before
a new run. Never interpret a missing final usage report as zero spend.

## Production deployment

| Piece | Where | Name |
|---|---|---|
| Weekly job | Google Cloud Run Job, `us-west1` | `leadgen-weekly` (`leadgen weekly`, 45-minute timeout, retries 0) |
| Migrations | Google Cloud Run Job | `leadgen-migrate` (`leadgen db migrate`, run by every deploy) |
| Schedule | Google Cloud Scheduler | `leadgen-weekly-friday`: Fridays 08:00 America/Los_Angeles |
| Database | Neon Postgres, AWS `us-west-2` | project `leadgen-prod-neon`, database `neondb` |
| Secrets | Google Secret Manager | `DATABASE_URL` (owner), `APP_DATABASE_URL`, `DASHBOARD_DATABASE_URL`, the provider keys |
| Image | Artifact Registry | `us-west1-docker.pkg.dev/leadgen-prod-austin/leadgen/leadgen:<commit>` |
| Dashboard | Streamlit Community Cloud | `streamlit_cloud/streamlit_app.py`, read-only login |
| Deploys | GitHub Actions | `.github/workflows/deploy.yml`, after CI passes on `main` |

Three database logins, each with the least it needs: the owner (`DATABASE_URL`) only for
migrations; `leadgen_app` for the weekly job (reads and writes rows, cannot change the schema);
`leadgen_dashboard` (reads only the views in `run_status.DASHBOARD_VIEWS`, never contacts or
evidence). `scripts/gcp_setup.sh` records how the Google project was built.

Set `P=leadgen-prod-austin R=us-west1` for the commands below.

**Check the last run.** Cloud Run → Jobs → `leadgen-weekly` → Executions, or:

```bash
gcloud run jobs executions list --job=leadgen-weekly --region=$R --project=$P --limit=5
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="leadgen-weekly"' \
    --project=$P --freshness=2d --order=asc --format="value(textPayload)"
```

The log ends with the JSON summary and `Container called exit(N)`; the exit codes are the ones in
the table above. A failed execution (exit 1 or 3) is not retried: handle it as in Failure
recovery, with the owner URL from Secret Manager as `DATABASE_URL`.

**Run now, or preview.** `--dry-run` plans inside a rolled-back transaction and spends nothing.

```bash
gcloud run jobs execute leadgen-weekly --region=$R --project=$P --wait --args=weekly,--dry-run
gcloud run jobs execute leadgen-weekly --region=$R --project=$P --wait      # paid, policy budget
```

**Pause or resume the schedule.**

```bash
gcloud scheduler jobs pause leadgen-weekly-friday --location=$R --project=$P
gcloud scheduler jobs resume leadgen-weekly-friday --location=$R --project=$P
```

**Deploy.** Merging to `main` deploys after CI passes: the workflow builds the image, runs
`leadgen-migrate`, then moves `leadgen-weekly` to the new image. Actions → "Deploy p1 to Google
Cloud" → Run workflow redeploys by hand. A migration that adds a dashboard view also needs the
grant: rerun `scripts/provision_database_roles.py` (no `--rotate`).

**Rotate a provider key.** Create the new key with the provider, then
`printf '%s' "$NEW_KEY" | gcloud secrets versions add EXA_API_KEY --data-file=- --project=$P`.
Jobs read `latest` at start, so the next execution uses it; revoke the old key afterwards.

**Rotate the database logins.**
`DATABASE_URL="$(gcloud secrets versions access latest --secret=DATABASE_URL --project=$P)" uv run python scripts/provision_database_roles.py --project $P --rotate`,
then paste the new `DASHBOARD_DATABASE_URL` into the Streamlit Cloud app's secrets. Rotate the
owner password in the Neon console and add it as a new `DATABASE_URL` version.

**Back up.** Neon's free plan keeps a short restore window, not long-term backups. Before risky
changes, and monthly:

```bash
docker run --rm -v "$PWD:/dump" -e URL="$(gcloud secrets versions access latest --secret=DATABASE_URL --project=$P)" \
    postgres:18 sh -c 'pg_dump "$URL" -Fc --no-owner --no-acl -f /dump/leadgen_$(date +%Y%m%d).dump'
```

Restore into an empty database with `pg_restore --no-owner --no-acl -d "$URL" <file>`, then
rerun `provision_database_roles.py`. Dumps hold contact details: keep them off the repository.

**Cost.** Hosting stays inside free tiers (Cloud Run, Scheduler, Secret Manager, Neon, Streamlit
Cloud); the image registry costs cents. The `leadgen-monthly-5usd` budget alert emails at 50%,
90% and 100% of $5 a month. Provider spend per run is capped by the policy budget.
