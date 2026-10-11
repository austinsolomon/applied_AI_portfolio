# P1 architecture — three connected perspectives

Generated from one component catalog. Implementation baseline: [5ac8398](https://github.com/austinsolomon/applied_AI_portfolio/commit/5ac8398).

## Explore

- [Lead pipeline](index.html) — How does a market gap become usable leads?
- [Production safeguards](safeguards.html) — What keeps unattended work controlled and recoverable?
- [Qualification and review](qualification.html) — Why did this business qualify, fail, or remain uncertain?

## Boundaries

These maps explain one Python application with one Postgres database. A store shape represents a logical data responsibility, not a separate service. Cloud deployment is planned. Packet payloads are representative; live totals are dated observations.

Phoenix, 2026-10-11: **50 sourced → 12 qualified → 10 saved**; 14 rejected and 24 review. Ledger: 1.385297 USD including 0.374400 USD in conservative estimates. See [the sample report](../sample_run.md).

## Lead pipeline

How does a market gap become usable leads?

### Chapters

1. **Choose and approve** — Start with a measured gap and one human decision.
2. **Source and deduplicate** — Find candidates, then remove businesses already known.
3. **Qualify and save** — Evidence and a usable contact determine what reaches the CRM.
4. **The complete lead pipeline** — Follow one approved campaign from gap to CRM.

### Decisions

| Decision | Rationale |
|---|---|
| Scope | One category and one approval; configured keyword order. |
| Identity | Exact normalized keys, including CRM constraints. |
| Observed result | 2026-10-11: 50 raw → 12 qualified → 10 saved. |

### Flow: Phoenix campaign

| From | To | Handoff |
|---|---|---|
| Demand gaps | Scrape plan | market gap |
| Scrape plan | One approval | priced plan |
| One approval | Google Maps sourcing | approved cell |
| Google Maps sourcing | Exa lookalikes | source checkpoint |
| Exa lookalikes | Dedupe | combined candidates |
| Dedupe | Contact enrichment | new businesses |
| Contact enrichment | Qualification | contact data retained |
| Qualification | CRM write | eligible decisions |
| CRM write | Demand gaps | available leads |

## Production safeguards

What keeps unattended work controlled and recoverable?

### Chapters

1. **Authorize every paid operation** — The pipeline is one cube; approval and budget gates surround it.
2. **Bound work and retain state** — Keep ownership, checkpoints and reusable pages in Postgres.
3. **Inspect and verify** — Observe what happened and verify the container before release.
4. **The complete safeguards view** — Compare allowed work, denied spend and recovery after interruption.

### Decisions

| Decision | Rationale |
|---|---|
| Paid retries | Ambiguous requests stay reserved until operator reconciliation. |
| Accounting | Conservative estimates are explicit; exact Firecrawl invoice reconciliation remains open. |
| Deployment | Container verified; cloud deployment and scheduling are planned. |

### Flow: Allowed work

| From | To | Handoff |
|---|---|---|
| One approval | Lead pipeline | approved scope |
| Lead pipeline | Budget guard | reserve before calling |
| Budget guard | Lead pipeline | within budget |
| Lead pipeline | Costs and traces | report usage |
| Lead pipeline | Durable run state | stage checkpoint |

### Flow: Budget blocked

| From | To | Handoff |
|---|---|---|
| Lead pipeline | Budget guard | request above remainder |
| Budget guard | Costs and traces | record denial |
| Budget guard | Lead pipeline | stop paid work |

### Flow: Interrupted → resume

| From | To | Handoff |
|---|---|---|
| Lead pipeline | Durable run state | ambiguous paid call |
| Durable run state | Lead pipeline | deny premature resume |
| Durable run state | Costs and traces | operator reconciliation |
| Costs and traces | Durable run state | audit retained |
| Durable run state | Lead pipeline | resume same run |

## Qualification and review

Why did this business qualify, fail, or remain uncertain?

### Chapters

1. **Gather before judging** — Give the judge bounded text with source URLs.
2. **Check and research** — A model claim must survive deterministic citation checks.
3. **Save and review outcomes** — CRM acceptance and human evaluation are different gates.
4. **The complete qualification view** — Trace a successful qualification or an unresolved case.

### Decisions

| Decision | Rationale |
|---|---|
| Provenance | Citation checks verify quoted evidence, not the truth of every interpretation. |
| Escalation | Research only Tier 1 review decisions; use fixed turn, tool and budget caps. |
| Evaluation | Live fixtures are observations until a human labels their accuracy. |

### Flow: Qualified → CRM

| From | To | Handoff |
|---|---|---|
| Evidence gathering | Page cache | fetch evidence |
| Page cache | Evidence gathering | page text |
| Evidence gathering | Structured judge | bounded evidence |
| Structured judge | Decision checks | structured decision |
| Decision checks | CRM write | quote and fields pass |
| Decision checks | Human eval review | record decision |

### Flow: Unclear → human review

| From | To | Handoff |
|---|---|---|
| Evidence gathering | Page cache | fetch evidence |
| Page cache | Evidence gathering | page text |
| Evidence gathering | Structured judge | bounded evidence |
| Structured judge | Decision checks | uncertain evidence |
| Decision checks | Bounded research | bounded research |
| Bounded research | Structured judge | re-judge |
| Structured judge | Decision checks | still unclear |
| Decision checks | Human eval review | retain uncertainty |
| Human eval review | Replay fixtures | label and export |

## Shared component catalog

### DA · Demand gaps

Find the market furthest below its available-lead goal.

The dashboard and planner read the same Postgres demand view. Low coverage determines where to look next; the model does not invent a market.

Source: [src/leadgen/gaps.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/gaps.py).

Steps:

1. **Read goals** — Compare available leads with the remaining goal.
2. **Choose market** — Select the market with the lowest coverage.

### PL · Scrape plan

Turn a real market into priced city and keyword cells.

Census places supply the geography. The operator sets the raw target and city/keyword caps; reviewed configuration supplies keyword order. Learned keyword ranking is not implemented.

Source: [src/leadgen/orchestrator.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/orchestrator.py).

Steps:

1. **Expand market** — Use Census places within the market radius.
2. **Create cells** — Pair cities with configured keywords in a deterministic order.
3. **Price scope** — Show the raw target and Maps ceiling before approval.

### AP · One approval

Approve the plan and total budget once, before paid work.

The terminal records who approved which plan and its budget. Runtime tools cannot invent new cells or keywords. The Phoenix campaign had a 5 USD total budget and a separate 0.75 USD model allowance.

Source: [src/leadgen/plans.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/plans.py).

Steps:

1. **Review** — Read the proposed cells and ceilings.
2. **Approve** — Save the named operator and approved budget.
3. **Run** — Execute only that approved scope.

### GM · Google Maps sourcing

Sample, grade and retain businesses from an approved Maps search.

Apify is polled and aborted at the sample count or ceiling; a poll can return extra results. Category purity and distance gate continuation. Partial results are persisted even after abort. The Phoenix campaign yielded 50 businesses.

Source: [src/leadgen/sourcing_maps.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/sourcing_maps.py).

Steps:

1. **Start sample** — Save the provider run ID on the approved cell.
2. **Poll and persist** — Keep partial results while checking count and cost.
3. **Grade** — Check raw category purity and in-market share; record a discounted metric.
4. **Continue or stop** — Resurrect a passing sample when more results are needed.

### EX · Exa lookalikes

Use eligible CRM websites as seeds for similar businesses.

At most 50 qualified own-domain seeds produce ten results each. Results are combined by domain and seed consensus; shared hosts and directories are excluded. Phoenix had no eligible real-domain seeds, so this stage made no live requests.

Source: [src/leadgen/sourcing_exa.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/sourcing_exa.py).

Steps:

1. **Select seeds** — Qualified CRM domains with category signal.
2. **Find similar** — Exclude each source domain.
3. **Combine** — Count consensus and filter directories.

- **Q-EX1:** Was Exa exercised live? — No eligible seeds in the 2026-10-11 sample; network behavior is covered by fakes.

### DD · Dedupe

Drop known businesses before enrichment and qualification.

Exact normalized place ID, phone, email and domain keys are compared against the CRM and the current batch. Shared hosting domains do not count as business identity. Cross-run rejected-lead memory is not part of this pipeline.

Source: [src/leadgen/dedupe.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/dedupe.py).

Steps:

1. **Normalize** — Use the same Postgres expressions as CRM keys.
2. **Match** — Compare CRM and batch identities.
3. **Skip duplicates** — Only new leads continue.

### EN · Contact enrichment

Use existing contacts first, then a cached Blitz lookup if needed.

This is a plain function, not an agent. Two workers process enrichment jobs. Domain results are cached; existing phone or email data avoids unnecessary provider work.

Source: [src/leadgen/contacts.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/contacts.py).

Steps:

1. **Keep contacts** — Use already-sourced contact information.
2. **Check domain cache** — Reuse an earlier lookup.
3. **Enrich if needed** — Record provider units and the configured accounting rate.

### QL · Qualification

Decide eligibility from cited service and market evidence.

A fixed gather and structured judge handle the first pass. Only review cases enter bounded agent research. Mechanical checks establish citation provenance; human review is still needed to assess accuracy.

Source: [src/leadgen/qualification.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/qualification.py).

Steps:

1. **Gather** — Read business pages and search evidence.
2. **Judge** — Return a structured eligibility decision.
3. **Check** — Validate the quote, URL, services and market.
4. **Research unclear cases** — Re-judge, or keep the decision in review.

### CW · CRM write

Save qualified businesses with a usable contact in one transaction.

A qualified decision alone is not enough: at least one usable phone or email is required. Unique keys reject races and normalized duplicates. Contradictions are recorded for later review. Phoenix saved 10 of its 12 qualified businesses; two lacked usable contacts.

Source: [src/leadgen/crm_write.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/crm_write.py).

Steps:

1. **Check eligibility** — Keep qualified, nonduplicate leads.
2. **Check contact** — Discard junk email values and require a usable contact.
3. **Insert atomically** — Count conflicts with ON CONFLICT DO NOTHING.
4. **Capture contradictions** — Save suspicious reasoning to the eval log.

### PIPE · Lead pipeline

Execute the approved stages through one restricted SDK tool.

The pipeline is deliberately collapsed in this view. Its SDK can request only the next permitted stage; it has no shell or arbitrary built-in tools. Open the pipeline perspective to see the individual stages.

Source: [src/leadgen/orchestrator.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/orchestrator.py).

Steps:

1. **Load approval** — Read the stored plan.
2. **Run next stage** — Enforce stage order in both the hook and handler.
3. **Checkpoint** — Persist the stage result before advancing.

### BG · Budget guard

Reserve budget atomically before each paid operation starts.

The pre-tool hook restricts stage and plan scope. A locked approval row serializes reservations so concurrent workers cannot both spend the same remaining budget. Spent money plus held reservations counts against the plan; model calls also share a 0.75 USD allowance.

Source: [src/leadgen/budget.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/budget.py).

Steps:

1. **Check scope** — Require an active approved plan and valid cell.
2. **Lock approval** — Serialize competing reservations.
3. **Reserve ceiling** — Account for prior spend and held operations.
4. **Allow or block** — Refuse over-budget work without another approval prompt.

- **Q-BG1:** Is the SDK cap a provider billing guarantee? — No. It is an estimated session stop threshold; final costs and unknown outcomes still require accounting.

### RS · Durable run state

Keep checkpoints and uncertain outcomes across process failure.

Completed stages are skipped. A finished plan returns without provider calls. Failed runs require explicit resume, and unresolved paid operations block that resume. An operator must reconcile ambiguous outcomes rather than simply retrying a request.

Source: [src/leadgen/orchestrator.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/orchestrator.py).

Steps:

1. **Persist identity** — Keep run, cell, provider and raw-lead identities.
2. **Checkpoint stages** — Save successful stage results.
3. **Hold uncertainty** — Retain reservations after ambiguous failures.
4. **Reconcile and resume** — Use provider evidence or explicit conservative estimates; skip completed work.

### JQ · Worker ownership

Give each job to one worker and fence stale completions.

Postgres SKIP LOCKED claims jobs concurrently. Completion requires the owning worker and attempt number. General jobs can retry with jitter; paid pipeline jobs use one attempt because a timeout may already have consumed credits. Enrichment uses two workers and qualification uses four.

Source: [src/leadgen/job_queue.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/job_queue.py).

Steps:

1. **Claim** — Lock and claim one available job.
2. **Run** — Respect the configured concurrency cap.
3. **Fence completion** — Verify worker and attempt ownership.
4. **Handle failure** — Bound retries; do not automatically repeat ambiguous paid work.

### PC · Page cache

Reuse a fetched page for seven days before paying to fetch again.

Pages are stored by normalized URL in the same Postgres database. Search, scraping and the free fallback share a web interface. Local free fetching rejects internal destinations, checks redirects and caps response size. The cache is not a distributed single-flight lock.

Source: [src/leadgen/web_research.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/web_research.py).

Steps:

1. **Look up URL** — Read a fresh cached page if available.
2. **Fetch if needed** — Use the configured backend and bounded fallback.
3. **Record and store** — Capture costs and cache readable evidence.

### CL · Costs and traces

Make spend, latency, blocked work and reconciliations inspectable.

The ledger records provider units and model usage; traces record outcomes. Unknown web charges are marked estimated. The live ledger totaled 1.385297 USD, including 0.374400 USD of conservative Firecrawl estimates. Exact Firecrawl invoice reconciliation remains open.

Source: [src/leadgen/budget.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/budget.py).

Steps:

1. **Record** — Append usage and cost when known.
2. **Trace** — Capture timing, outcome and blocked operations.
3. **Reconcile** — Retain an audit note when an operator resolves uncertainty.

- **Q-CL1:** Reconcile the sample’s estimated Firecrawl charges against the provider invoice.

### CI · Build and verification

Ship the same locked package in a tested non-root container.

CI runs lint, secret scanning, migrations, fake-provider tests, container builds and image smoke checks. The live-tested build had 356 passing tests. Provider credentials are absent from CI; the image excludes the local environment file. This does not constitute cloud deployment.

Source: [Dockerfile](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/Dockerfile).

Steps:

1. **Check source** — Lint and scan for secrets.
2. **Test behavior** — Use throwaway Postgres databases and fake providers.
3. **Build image** — Install locked Python dependencies and the pinned Claude CLI.
4. **Smoke test** — Run both CLIs and migrate a fresh database.

### DEP · Cloud deployment (planned)

Cloud hosting and scheduled execution are still planned.

The container exists, but Cloud Run, Scheduler, Secret Manager and deployment identity are outside the completed build. No live cloud service is represented here.

Source: [docs/design.md](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/docs/design.md).

Steps:

1. **Choose hosting** — Define the deployment environment.
2. **Wire secrets** — Provide runtime credentials securely.
3. **Schedule** — Configure the approved operating cadence.

### EV · Evidence gathering

Collect bounded evidence from business pages and one search.

Tier 1 reads the home, services and about pages plus a search through the shared web interface. It limits the evidence block to 18,000 characters. Web text is untrusted data, not instructions for the agent.

Source: [src/leadgen/qualification.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/qualification.py).

Steps:

1. **Read pages** — Fetch the configured business paths.
2. **Search** — Look for service and market evidence.
3. **Bound text** — Keep source URLs and cap the evidence block.

### JU · Structured judge

Return qualified, rejected or review with a reason and citation.

A single Messages API call returns a typed decision: verdict, reason, evidence quote, source URL, eligible services and market coverage. Missing evidence or incomplete output stays review. The live run used Haiku 5.5; model choice is separate from deterministic validation.

Source: [src/leadgen/qualification.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/qualification.py).

Steps:

1. **Provide rubric** — Include business, market and source evidence.
2. **Request decision** — Use typed structured output and an output-token cap.
3. **Record usage** — Ledger returned tokens without automatic paid retries.

### VA · Decision checks

Verify the cited quote and eligibility fields before accepting qualification.

A qualified result needs an exact quote in a supplied URL, an eligible service and affirmative market coverage. Failed checks downgrade it to review; an explicit market no becomes rejected. This proves provenance, not that every model interpretation is correct.

Source: [src/leadgen/qualification.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/qualification.py).

Steps:

1. **Check quote** — Find the normalized exact quote in the cited evidence.
2. **Check fields** — Require eligible services and affirmative market coverage.
3. **Keep or downgrade** — Preserve valid decisions; leave uncertainty in review.

### RA · Bounded research

Research only review cases, then run the judge again.

The SDK can search and fetch discovered URLs, with at most four tool calls and four turns and a small model budget. Saved evidence can be reused during recovery. A turn-limit result is a bounded stop, not permission to restart research. All 32 Phoenix first-pass review cases were researched.

Source: [src/leadgen/research_agent.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/research_agent.py).

Steps:

1. **Find missing evidence** — Search for service and area information.
2. **Read discovered pages** — Limit tools and URLs.
3. **Stop at bounds** — Capture final usage and retain evidence.
4. **Re-judge once** — Keep remaining ambiguity as review.

### ER · Human eval review

Let an operator label recorded decisions and contradictions.

All judge decisions are captured, not just the successful ones. A Streamlit review page records correct/incorrect labels and notes. Phoenix ended with 24 review decisions. Human labels do not automatically retrain or change the model.

Source: [src/leadgen/eval_review.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/src/leadgen/eval_review.py).

Steps:

1. **Inspect evidence** — Read the saved decision and sources.
2. **Label** — Mark correct or incorrect with a note.
3. **Export** — Prepare reviewed fixtures for offline regression.

### FX · Replay fixtures

Use reviewed cases and redacted observations to check behavior offline.

Fixture export supports human-labelled cases. The committed Phoenix examples are redacted machine observations, not human-labelled accuracy benchmarks. Tests validate contracts without paid calls; any exported contacts or full pages must be reviewed before committing.

Source: [tests/test_live_replay.py](https://github.com/austinsolomon/applied_AI_portfolio/blob/main/p1_leadgen_pipeline_hardened_agent/tests/test_live_replay.py).

Steps:

1. **Review export** — Check labels and redact contact data.
2. **Replay offline** — Validate deterministic decision contracts.
3. **Check regressions** — Run in CI without provider credentials.

## Maintenance

Edit `atlas/data.mjs`, then run `node atlas/build.mjs`. Both the maps and this text reference are generated; do not edit their outputs. `node atlas/build.mjs --check` checks graph integrity, script syntax and generated-file freshness without rewriting files.
