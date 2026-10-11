# Operating an approved run

Start Postgres, migrate the schema, seed the demo once, and load Census places using the README
quickstart. Keep credentials in the ignored `.env`; the container excludes that file from its build.
Cloud hosting and automated scheduling are not configured by this repository.

`leadgen plan` creates a draft and asks for approval once. Check the cell count, target and total
budget. `leadgen run --plan ID` executes it. A completed run can be queried again without new paid
work. The SDK is limited to the pipeline's MCP stage tool; changing scope requires a new plan.

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

## Review and evidence

`leadgen evals review` opens the review UI. Label decisions, then export fixtures with
`leadgen evals export-fixtures --out out/evals`. Review and redact business contact data and page
text before moving any fixture into version control. The local database contains the full evidence;
the public sample report contains business names and aggregate counts only.

Unit prices convert Firecrawl credits and Blitz records into accounting costs. Set them for the
actual subscription. Provider balance or price changes require checking these assumptions before
a new run. Never interpret a missing final usage report as zero spend.
