# Spec: use case 1, "Source 20 leads in Denver"

Status: **draft for approval**. Nothing in this spec is implemented yet.

## 1. Goal

A rep types in Slack:

> @gtm-agent source 20 leads in Denver

and, with one approval click, gets back:

1. qualified leads written to **Salesforce** (Lead records),
2. a **CSV artifact and summary** posted in the same Slack thread,
3. a **"Create outreach for these leads"** button that queues use case 2.

That is "use case 1, fully complete". Use case 2 (the outreach graph) is out of scope; the button
only records the request as queued.

### Definition of done

- A real Slack message reaches the agent running on Cloud Run, in the new GCP project.
- The brain (Claude Agent SDK) recognizes the request and starts the leadgen graph (LangGraph).
- The graph posts a plan with a budget to Slack and waits; nothing paid runs before **Approve**.
- After approval, a live run sources, dedupes, enriches, and qualifies leads with cited evidence,
  within the approved budget.
- Qualified leads appear in the Salesforce Developer Edition org, without duplicates on rerun.
- The thread receives the CSV, a summary (sourced, qualified, written, cost), and the outreach
  button. Clicking it records a queued use-case-2 request and replies "queued".
- Every brain run and graph run is traced in LangSmith; every paid call is in the cost ledger.

### Non-goals (for this spec)

- The outreach graph, style memory, and Slack draft approvals (roadmap steps 7 to 9).
- A real dispatcher. Dispatch is a placeholder that gives every request every tool (section 4.2).
- Webhook and cron surfaces beyond Slack, a chat UI, and multiple reps or roles.
- The review agent from the shipped pipeline (uncertain leads are labeled low confidence instead).
- Measurement dashboards (roadmap step 10); this spec only records the raw data.

## 2. Architecture

```
Slack ──► Cloud Run service "gtm-agent" (FastAPI, min instances 0)
            POST /slack/events        verify signature, dedupe, save request, ack < 3s,
                                      enqueue Cloud Task
            POST /slack/interactions  button clicks (Approve, Cancel, Create outreach)
            POST /tasks/brain         Cloud Tasks → dispatch → brain run
            POST /tasks/graph         Cloud Tasks → start or resume a leadgen graph run
            GET  /healthz
              │
              ├─ Dispatch (placeholder): request → profile (all tools, all permissions)
              ├─ Brain: Claude Agent SDK, options built from the profile, GTM tools
              └─ Leadgen graph: LangGraph, Postgres checkpointer
Cloud Tasks   queue "gtm-agent-work": decouples the 3-second Slack ack from long work
Cloud SQL     Postgres 16, db-f1-micro: app tables + LangGraph checkpoints
Cloud Storage bucket for CSV artifacts
Secret Manager  Slack, Anthropic, Apify, Exa, BlitzAPI, Salesforce, LangSmith, DB
Salesforce    Developer Edition org: Lead records
LangSmith     traces for brain and graph runs (free Developer plan to start)
```

### Request lifecycle

```
 1. Rep: "@gtm-agent source 20 leads in Denver"
 2. Slack → POST /slack/events
      verify v0 signature (5-minute timestamp window); drop retries by event_id
      save request row; enqueue task /tasks/brain {request_id}; return 200
 3. /tasks/brain: dispatch → profile (placeholder: all tools)
      build brain options from profile; run brain on the request text
 4. Brain calls tool start_leadgen(market="Denver, CO", target=20)
      tool creates a graph run (thread_id) and enqueues /tasks/graph {start}
      brain replies in thread: "Planning a run for Denver…"
 5. /tasks/graph: graph runs plan node → computes cities, searches, budget
      interrupt(): posts "Approve plan and $X budget?" with Approve / Cancel; run pauses
 6. Rep clicks Approve → POST /slack/interactions
      verify signature; record approval (who, when, budget); enqueue /tasks/graph {resume}
 7. /tasks/graph resumes the thread from its checkpoint:
      source_maps → source_exa → dedupe → enrich → qualify → write_salesforce → artifact
 8. artifact node uploads CSV to the thread, posts summary + "Create outreach" button
 9. Rep clicks Create outreach → outreach request saved as queued; reply "Queued for outreach"
```

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Cloud | GCP only, new project | Learn one ecosystem end to end; costs and permissions separate from leadgen |
| GCP project | Display name `applied_ai_portfolio`; project ID `applied-ai-portfolio` (or with a suffix if taken) | Project IDs can't contain underscores and must be globally unique |
| Compute | One Cloud Run service, min instances 0 | Production-style, costs nothing idle |
| Long work | Cloud Tasks → the same service | Slack needs an ack in 3 s; tasks give retries |
| Database | Cloud SQL Postgres 16, `db-f1-micro` | All on GCP; ~$10/month |
| Brain | Claude Agent SDK (the existing harness, adapted) | Continues the harness as the brain |
| Workflows | LangGraph graphs with the Postgres checkpointer | Durable runs, interrupt for approval, resume |
| Leadgen | Rebuilt minimally as a LangGraph graph | Reference: `past_builds/leadgen_pipeline_agents/` |
| Run mode | Live providers, approval before spend, budget cap | Real portfolio demo, bounded cost |
| CRM | Salesforce Developer Edition | Matches what GTM job postings ask for |
| Tracing | LangSmith | Traces now, evals later |

## 4. Components

### 4.1 Ingestion: Slack in and out

**Endpoints** (`gtm_agent/app/slack.py`, FastAPI router):

- `POST /slack/events`
  - Answer `url_verification` challenges.
  - Verify `X-Slack-Signature` with the signing secret over `v0:{timestamp}:{raw body}`; reject
    timestamps older than 5 minutes.
  - Handle `app_mention` and direct-message events; ignore bot messages (including our own).
  - Drop Slack retries (`X-Slack-Retry-Num`) and duplicate `event_id`s.
  - Save a `requests` row, enqueue `/tasks/brain`, and return 200 within 3 seconds.
- `POST /slack/interactions`: verify the signature; route `approve_plan`, `cancel_plan`, and
  `queue_outreach` button actions; update the message so buttons can't be clicked twice.
- **Outbound** (`gtm_agent/app/slack_client.py`): `chat.postMessage` (threaded), `chat.update`,
  and file upload using `files.getUploadURLExternal` + `files.completeUploadExternal`.

**Slack app** (created by you): bot scopes `app_mentions:read`, `chat:write`, `files:write`,
`im:history`, `im:read`; event subscriptions for `app_mention` and `message.im`; interactivity on.
Both URLs point at the Cloud Run service.

### 4.2 Dispatch (placeholder)

`gtm_agent/app/dispatch.py`:

```python
@dataclass(frozen=True)
class Profile:
    name: str              # "placeholder-all"
    tools: list[str]       # every registered tool
    subagents: list[str]   # every subagent
    sandbox: str           # "none" for GTM work
    approval_mode: str     # "spend_requires_approval"
    budget_usd: float      # default cap per run, e.g. 5.00

def dispatch(request: Request) -> Profile:
    """Placeholder: every request gets every tool. Saves the profile with the request."""
```

It still records `who`, `surface`, and the chosen profile on the request row, so step 3's real
classifier can replace the function body without changing anything around it.

### 4.3 Brain: adapting the harness to GTM

The harness today is a coding assistant. Changes, each small and reviewed on its own:

| File | Change |
|---|---|
| `server.py` | Split into `app/main.py` (FastAPI app and routes) and `app/brain.py`. `make_options(events)` becomes `build_options(profile, context)`: the factory. No sandbox for GTM profiles. |
| `registry.py` | Unchanged pattern; register GTM tools alongside built-ins. The profile's `tools` list selects which are exposed. |
| `tools.py` → `tools/coding.py` | Coding tools kept but excluded from GTM profiles. |
| new `tools/gtm.py` | GTM tools, as an in-process MCP server (below). |
| `system.py` | New GTM prompt sections: role (GTM operator for the team), how to read a leadgen request, never spend without the approval flow, reply in the Slack thread. |
| `approval.py` | Kept for coding tools. Spend approval moves to the graph's interrupt (4.4); tools that write externally are listed in the profile as requiring approval. |
| `telemetry.py` | Also write per-run token cost to the database; wrap brain runs in LangSmith traces. |
| `index.py` | The CLI stays for local testing: `python -m gtm_agent.cli "source 20 leads in Denver"`. |

**GTM tools** (each with a contract: when to use, when not to, capped output):

| Tool | Does | Writes? |
|---|---|---|
| `start_leadgen(market, target, budget_usd?)` | Creates a leadgen graph run and enqueues it. Returns run ID. The plan and approval happen in the graph | Creates a run (no spend) |
| `get_leadgen_run(run_id)` | Status, stage, counts, cost so far | No |
| `list_leadgen_runs(limit)` | Recent runs for this workspace | No |
| `reply_in_thread(text)` | Posts the brain's answer to the request's Slack thread | Slack message |

Spend and Salesforce writes happen only inside the graph, after approval. The brain can't call a
paid provider directly.

### 4.4 Leadgen graph (LangGraph)

`gtm_agent_graphs/leadgen/` (package `leadgen_graph`):

**State** (typed): `run_id`, `request_id`, `slack_channel`, `slack_thread_ts`, `market`,
`target`, `budget_usd`, `plan`, `approval`, `raw_leads`, `new_leads`, `enriched_leads`,
`qualified`, `low_confidence`, `rejected`, `salesforce_ids`, `artifact_url`, `costs`.

**Nodes:**

| Node | Kind | Does |
|---|---|---|
| `plan` | Code | Expands the market into its largest cities (Census places), picks keywords from the category config, prices the Maps and Exa ceilings plus model spend, sets the budget |
| `approve` | Interrupt | Posts the plan and budget to Slack with Approve / Cancel and pauses (`interrupt()`); resumes with the decision. Cancel ends the run |
| `source_maps` | Code + Apify | Google Maps search per city and keyword, stops at the target ceiling |
| `source_exa` | Code + Exa | Plain-language company searches per city; caches page text for evidence |
| `dedupe` | Code | Removes duplicates within the run and against Salesforce (by domain, phone, email) |
| `enrich` | Code + BlitzAPI | Contact lookup only for leads still missing an email or phone |
| `qualify` | One structured Claude call per lead | Verdict, reason, exact quote; code checks the quote appears in the page text. No quote → `low_confidence` |
| `write_salesforce` | Code | Upserts qualified leads as Lead records (external ID = normalized domain), records IDs |
| `artifact` | Code | Builds the CSV, stores it in Cloud Storage, uploads it to the thread, posts the summary and the "Create outreach" button |

**Rules carried over from the shipped pipeline:**

- Every paid call reserves its ceiling in `budget_reservations` before it runs, keyed by
  `(run_id, node, item)`. A call with an unknown outcome is not repeated on resume.
- The checkpointer saves state after every node; a failed task resumes from the last finished node.
- Model calls have their own cap inside the run budget.

**Category:** the demo category from the shipped pipeline (independent CFP® financial planners),
loaded from a config file so it can change later.

### 4.5 Delivery

- **Salesforce:** REST API on a Developer Edition org, via a Connected App (OAuth client
  credentials). Lead fields: company, website, phone, email, city, state, source, qualification
  reason, evidence URL, quote, run ID. A custom external ID field on Lead makes writes idempotent.
- **Slack:** CSV (one row per qualified or low-confidence lead with evidence) plus summary:
  sourced by source, new after dedupe, qualified, low confidence, written to Salesforce, cost vs
  budget, link to the LangSmith trace.

### 4.6 Shared layer (this spec's slice)

- **LangSmith:** `LANGSMITH_TRACING=true`, project `gtm-agent`. Graph runs trace automatically;
  brain runs are wrapped with `@traceable`, with the request ID and run ID as metadata.
- **Cost ledger:** one row per paid call (provider, operation, reserved, actual).
- **Audit:** request, profile, approval (who, when, budget), and Salesforce writes are rows, not
  only logs.

## 5. Data model (Cloud SQL)

| Table | Purpose |
|---|---|
| `requests` | One row per Slack request: event ID, user, channel, thread, text, profile, status |
| `brain_sessions` | Slack thread ↔ Claude Agent SDK session ID, for follow-up messages |
| `graph_runs` | One row per graph run: graph name, thread ID, request ID, status, current node |
| `approvals` | Plan approvals: run ID, plan, budget, decision, decided by, decided at |
| `budget_reservations` | Paid-call reservations and outcomes (idempotency for spend) |
| `cost_ledger` | Actual cost per paid call and per model call |
| `leads` | Leads per run with verdict, evidence, Salesforce ID |
| `outreach_queue` | Use-case-2 requests queued by the button (no processing yet) |
| LangGraph tables | Created by the Postgres checkpointer (`setup()`) |

Migrations are numbered SQL files in `gtm_agent/migrations/`, applied by a migrate command.

## 6. Infrastructure (GCP)

| Resource | Setting |
|---|---|
| Project | `applied_ai_portfolio` (display), ID `applied-ai-portfolio[-suffix]`, existing billing account |
| Budget alert | $20/month, emails at 50%, 90%, 100% |
| APIs | Cloud Run, Cloud Tasks, Cloud SQL Admin, Secret Manager, Artifact Registry, Cloud Storage, IAM Credentials |
| Cloud Run | Service `gtm-agent`, region `us-central1`, min 0 / max 2 instances, 1 vCPU, 1–2 GiB, request timeout 30 min, Cloud SQL connection attached |
| Cloud Tasks | Queue `gtm-agent-work`, OIDC-authenticated calls to the service, limited retries |
| Cloud SQL | Postgres 16, `db-f1-micro`, 10 GB SSD, daily backups, IAM or password auth via Secret Manager |
| Cloud Storage | Bucket for artifacts, 30-day lifecycle delete |
| Artifact Registry | Repo for images, keep last 5 |
| Service accounts | `gtm-agent-runtime` (Cloud SQL client, secret accessor, tasks enqueuer, storage writer); `gtm-agent-deployer` for GitHub Actions |
| Deploys | GitHub Actions → Workload Identity Federation → build, migrate, deploy (same pattern as leadgen) |
| Cloud Scheduler | Not used in this spec |

**Secrets:** `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `ANTHROPIC_API_KEY`, `APIFY_TOKEN`,
`EXA_API_KEY`, `BLITZ_API_KEY`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`,
`SALESFORCE_INSTANCE_URL`, `LANGSMITH_API_KEY`, `DATABASE_URL`.

**Estimated cost:** ~$10–13/month infrastructure (mostly Cloud SQL), plus provider spend per run,
capped by the approved budget (~$2–3 for 20 leads).

## 7. Setup you do (accounts I can't create)

1. GCP project on your billing account, plus the budget alert.
2. Slack app in your workspace (I'll provide a manifest file to paste).
3. Salesforce Developer Edition org, the Lead external ID field, and a Connected App.
4. API keys: Anthropic, Apify, Exa, BlitzAPI, LangSmith.
5. Secrets entered in Secret Manager (never in chat or in the repo).

## 8. Testing

| Level | What | Where |
|---|---|---|
| Unit | Signature verification, retry dedupe, dispatch, tool contracts, budget reservations, quote check | Locally and CI |
| Graph | Full leadgen graph with fake providers and a fake Salesforce; interrupt and resume; resume after a simulated crash doesn't repeat paid calls | Locally and CI, against local Postgres |
| End to end (local) | Signed fake Slack requests → brain → graph (fakes) → Slack calls captured | Locally |
| Live smoke | Real Slack, real providers, `target=5`, budget $2, then `target=20` | Cloud Run |

CI never spends money: providers, Slack, and Salesforce are faked in tests.

## 9. Repository changes

```
gtm_agent/
  app/            main.py, slack.py, slack_client.py, dispatch.py, brain.py, tasks.py, db.py
  tools/          coding.py (moved), gtm.py (new)
  migrations/     numbered SQL
  docs/           this spec
  tests/
  Dockerfile, pyproject.toml
gtm_agent_graphs/
  leadgen/        leadgen_graph package: state.py, graph.py, nodes/, providers/, tests/
.github/workflows/  gtm-agent CI and deploy (leadgen workflows unchanged)
```

## 10. Build order (one PR each)

| PR | Scope | Roadmap step |
|---|---|---|
| A | This spec | — |
| B | Slack ingestion locally: `/slack/events` and `/slack/interactions`, signature checks, retry dedupe, outbound client; tested with signed fake requests | 3 (part) |
| C | Dispatch placeholder + harness factory: `build_options(profile)`, GTM prompt, GTM tools stubbed; CLI and Slack both reach the brain | 3, 4 |
| D | Postgres foundation: migrations for section 5 tables, db access, LangGraph checkpointer setup | 2 |
| E | Leadgen graph with fake providers: all nodes, interrupt and resume, budget reservations | 6 (part) |
| F | Live providers and Salesforce writes, behind the approval | 6 |
| G | GCP: Dockerfile, Cloud Run, Cloud Tasks, Cloud SQL, secrets, GitHub Actions deploy | 3–6 (deploy) |
| H | Artifact and outreach queue: CSV upload, summary, button, queue row; first live end-to-end run | 6, prep for 7 |

## 11. Risks and open questions

| Risk | Mitigation |
|---|---|
| The Claude Agent SDK starts the `claude` program as a subprocess; it must run in the Cloud Run image | PR G builds the image with the SDK's CLI and runs a smoke test before anything else is deployed |
| Cloud Tasks waits at most 30 minutes for an HTTP task | Each graph task runs until the next interrupt or completion; a long run can be split into one task per node, resuming from checkpoints |
| Slack retries if the ack is slow | Ack before any work; dedupe by `event_id` |
| Live spend | Approval before spend, budget reservations, per-run cap, budget alert |
| Salesforce Developer Edition API limits | One upsert per lead, batched with the composite API |
| Duplicate leads across runs | Dedupe against Salesforce plus an external ID upsert |

**Open questions for you:**

1. Default budget cap per run: $5?
2. Category for the demo: keep independent CFP® planners from the shipped pipeline?
3. Slack: one channel (for example `#gtm-agent`) plus DMs, or DMs only?
