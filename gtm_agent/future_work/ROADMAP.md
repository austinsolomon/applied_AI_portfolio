# GTM agent roadmap

The single plan for turning the hosted coding harness into the GTM agent. It combines two older
documents, which are now deleted (their full text is in git history):

- `use_case_1_slack_to_leadgen_graph_to_salesforce.md`: the spec for "source 20 leads in Denver".
- `post-course-todos.md`: notes from the Vercel coding-harness course. The course material itself
  stays in [`past_builds/vercel_coding_harness/`](../../past_builds/vercel_coding_harness/).

**How to read each item:** *Before* is what a rep sees today. *After* is what the item makes
possible. *Tasks* are the concrete steps, and *Origin* says which older document the item came from.

| # | Item | Size | Status |
|---|---|---|---|
| ✅ | Harness live in Slack, deployed on merge | | Done |
| 1 | Clean front door | Small | Next |
| 2 | Brain → GTM | Medium | Planned |
| 3 | Dispatch placeholder | Small | Planned |
| 4 | State and tracing | Medium | Planned |
| 5 | Durable runs | Small | Planned |
| 6 | Shared rules as hooks | Medium | Planned |
| 7 | Slack interactions | Medium | Planned |
| 8 | Graph 1: leadgen with fakes | Large | Planned |
| 9 | Live providers and Salesforce | Large | Planned |
| 10 | Graph 2: outreach and style memory | Large | Planned |
| 11 | Measurement and evals | Medium | Planned |
| 🅿️ | Parked | | Not planned |

Recommended order: 1 → 2 → 4 → 5 build the base (a reliable bot that remembers). 8 and 9 are the
first real GTM payoff.

---

## ✅ Done: harness live in Slack, deployed on merge

- **Before:** The harness ran only on a laptop, as a CLI or a local web UI.
- **After:** Anyone in the allow list can @mention the bot or DM it in Slack, and it answers in
  the thread. Merging a pull request that touches `gtm_agent/` runs the tests and redeploys to
  Cloud Run, so colleagues can change the bot without touching Google Cloud.

What was built:
- [x] Slack front door: `POST /slack/events`, signature check, retries ignored, duplicate events
  dropped, allow list, "I'm on it...running" ack (`slack_events.py`)
- [x] Cloud Run service with Secret Manager for the Anthropic and Slack keys (`deploy/deploy.sh`)
- [x] GitHub Actions: tests on every PR; test, deploy and health check on merge, signed in with
  Workload Identity Federation (`.github/workflows/gtm-agent.yml`)
- [x] One-time setup scripts for GCP, GitHub and the Slack app manifest (`one_time_setup/`)
- [x] One `tests/` folder (17 tests), repo restructure, GCP project and $20 budget alert

---

## 1. Clean front door

- **Before:** Replies show raw `**bold**` and `## headings`. A DM that also @mentions the bot
  runs twice and posts two answers. The Slack code is added to `server.py`, so the original
  harness file is changed.
- **After:** Replies look normal in Slack, with one answer per message. `app.py` is the cloud
  entry point and `server.py` goes back to the original, so it's clear which code is Slack and
  which is the harness.

Tasks:
- [ ] `app.py`: the FastAPI app for Cloud Run (`/health`, the Slack router); Dockerfile runs it
- [ ] Restore `server.py` to the original harness version (local web UI only)
- [ ] Convert Markdown answers to Slack mrkdwn (bold, links, headings, code blocks)
- [ ] Handle each DM once: ignore `app_mention` events in IM channels (the `message.im` event
  already covers them)
- [ ] Tests for the conversion and the single run

Origin: issues found while getting the harness live; todos 3 (`server.py` gaps).

## 2. Brain → GTM

- **Before:** The bot is a *coding* agent. Ask "find leads in Denver" and it tries to read files
  and run bash.
- **After:** It knows it's a GTM assistant and has GTM tools: it can start a leadgen run (a stub
  for now) and check that run's status. Agent settings are built in one place (`build_options`),
  so adding a tool or changing the prompt is a one-line change.

Tasks:
- [ ] Move coding tools: `tools.py` → `tools/coding.py`; existing tests still pass
- [ ] Factory: `make_options(events)` → `build_options(profile, context)`; every surface (Slack,
  CLI, web UI) calls it
- [ ] GTM system prompt: role, how to read leadgen requests, never spend outside the approval
  flow, reply in the thread
- [ ] GTM tools (in-process MCP server), each with a when-to-use contract and capped output:
  - [ ] `start_leadgen(market, target, budget_usd?)`: creates a run (stub until item 8)
  - [ ] `get_leadgen_run(run_id)`, `list_leadgen_runs(limit)`
  - [ ] `reply_in_thread(text)`
- [ ] `can_use_tool` for GTM: allow the profile's tools; refuse built-in and unlisted tools
- [ ] Evaluate the SDK's built-in features before keeping custom versions: `AskUserQuestion`,
  `TodoWrite`, native subagents (`agents=`), native skills
- [ ] CLI for local testing: `gtm-agent ask "source 20 leads in Denver"`
- [ ] Tests: tool contracts, tool filtering, "source 20 leads in Denver" calls `start_leadgen`
  (recorded model response)

Origin: spec item 7; todos 2.0 (one `create_session()` for all surfaces) and 3.

## 3. Dispatch placeholder

- **Before:** Every message gets the same agent, with no record of who asked or what kind of
  request it was.
- **After:** Each request is tagged with a profile (user, role, allowed tools) and saved. Today
  every profile gets every tool. Later the dispatcher can limit tools by role (an SDR gets
  leadgen, a manager gets reports) without rewiring anything.

Tasks:
- [ ] `Profile` dataclass: name, tools, subagents, sandbox, approval mode, budget
- [ ] `dispatch(request) -> Profile`: returns `placeholder-all` (every tool, no sandbox, spend
  needs approval, $5 cap)
- [ ] Save the profile on the request row (needs item 4)
- [ ] Tests: profile contents, saved on the row

Origin: spec item 6.

## 4. State and tracing

- **Before:** The bot forgets everything once it answers. A follow-up like "now only 10 of them"
  in the same thread starts from zero. Duplicate tracking is lost on restart. When a run is slow
  or wrong, there's no way to see why.
- **After:** Postgres stores each thread's session, so follow-ups continue the same
  conversation. Every run shows up in LangSmith with each step, tool call, token count and cost,
  so you can debug it and see what each request cost.

Tasks:
- [ ] Scaffolding: `pyproject.toml` (uv), settings module (pydantic-settings), `.env.example`,
  local Postgres in `compose.yaml`
- [ ] Migration runner: numbered SQL files in `gtm_agent/migrations/`, `gtm-agent db migrate`
- [ ] Tables (see [data model](#data-model)): `requests`, `brain_sessions`, `graph_runs`,
  `approvals`, `budget_reservations`, `cost_ledger`, `leads`, `outreach_queue`
- [ ] LangGraph Postgres checkpointer tables, created by the migrate command
- [ ] Move duplicate tracking from memory to `requests.event_id` (unique)
- [ ] Sessions: save the Agent SDK session ID per Slack thread; `resume` it on follow-ups
- [ ] LangSmith tracing: project `gtm-agent`; brain runs wrapped with `@traceable` (request ID,
  profile, run ID as metadata); `LANGSMITH_API_KEY` in Secret Manager
- [ ] Cost ledger: each run's token cost written to `cost_ledger` (reuse `telemetry.py` pricing)
- [ ] Cloud SQL (Postgres 16, `db-f1-micro`, backups), attached to Cloud Run; migrate in the
  deploy workflow
- [ ] Tests against a throwaway database per test session; follow-up turn resumes the session

Origin: spec items 2, 3, 4.6, 7.6–7.8, 10.3; todos 4 (JSONL telemetry, replaced by LangSmith).

## 5. Durable runs

- **Before:** The agent runs as a background job inside the web server. If Cloud Run restarts or
  scales down mid-run, the run dies silently and Slack keeps showing "I'm on it...running".
- **After:** Each request becomes a Cloud Tasks job, which retries if the server goes away. Long
  runs (a 10-minute leadgen) finish reliably.

Tasks:
- [ ] Task queue interface `enqueue(path, payload)`: in-process locally, Cloud Tasks in the cloud
- [ ] Cloud Tasks queue `gtm-agent-work`: OIDC-authenticated calls to the service, limited retries
- [ ] `POST /tasks/brain`: load request → dispatch → run brain → mark done or failed; rejects
  calls without the task auth
- [ ] On failure, post an error in the thread instead of leaving "running"
- [ ] Tests: the task endpoint, auth rejection, failure message

Origin: spec items 4.7, 6.4, 10.7; todos 1 (durable runs).

## 6. Shared rules as hooks

- **Before:** Safety rules are scattered: some are in the prompt, some are in tool code, and
  some don't exist yet. Nothing stops a run from spending $50 on lead data.
- **After:** One checkpoint runs before and after every tool call. It enforces approvals ("ask
  before spending money"), budget caps, protected files, and an audit log of who did what. New
  tools get these rules automatically.

Tasks:
- [ ] `PreToolUse` hook: approval check, budget reservation, protected paths
- [ ] `PostToolUse` hook: record outcome and actual cost; audit row
- [ ] Budget reservations keyed by `(run_id, node, item)`; a call with an unknown outcome is not
  repeated on resume
- [ ] Audit rows for request, profile, approval (who, when, budget) and Salesforce writes
- [ ] Tests: over-budget call refused, audit row written, reservation not repeated

Origin: spec 4.6 and the shared layer; todos 4 (event bus and extensions).

## 7. Slack interactions

- **Before:** When the agent needs a yes/no ("this will cost $4, proceed?"), it can't ask you.
  You only see one final message, after a silent wait.
- **After:** Slack shows **Approve / Cancel** buttons and multiple-choice questions you can
  click. Status updates come in as it works ("searching… found 34… scoring…"), and each answer
  ends with a cost line.

Tasks:
- [ ] Slack client wrapper: `post_message`, `update_message`, `upload_file`; one fake for tests
- [ ] Block Kit builders: plan approval card, question card, summary card, outreach button
- [ ] `POST /slack/interactions`: verify signature, parse `block_actions`, route by `action_id`;
  turn on Interactivity in the Slack app
- [ ] Actions: `approve_plan`, `cancel_plan`, `answer_question`, `queue_outreach`; each enqueues a
  task and updates the message so it can't be clicked twice
- [ ] Status updates: edit one message as tools run; cost footer per answer
- [ ] Tests: signed fake button payloads, double click, unknown action

Origin: spec item 5; todos 2.1 (streaming, tool activity, approval and askUser buttons, cost
footer).

## 8. Graph 1: leadgen with fakes

- **Before:** "Source 20 leads in Denver" has nothing to run. The `start_leadgen` tool is a stub.
- **After:** A real LangGraph workflow runs end to end from Slack: plan → pause for your approval
  → search → enrich → score → results. Fake data providers keep it free while the flow is tested.

Tasks:
- [ ] Package `gtm_agent_graphs/leadgen/`: typed state and graph (see [leadgen graph](#leadgen-graph))
- [ ] Provider interfaces with fakes: Maps, Exa, Blitz, Claude judge, Salesforce, Slack
- [ ] Category config and Census places data, copied from the shipped pipeline with attribution
- [ ] Nodes: `plan`, `approve` (`interrupt()`), `source_maps`, `source_exa`, `dedupe`, `enrich`,
  `qualify`, `write_salesforce`, `artifact`
- [ ] `POST /tasks/graph`: start a run or resume it with `Command(resume=...)`
- [ ] Tests: full run with fakes; cancel; crash mid-run then resume without repeating paid calls;
  budget exhausted stops the run

Origin: spec item 8.

## 9. Live providers and Salesforce

- **Before:** The leads are fake, and nothing reaches Salesforce.
- **After:** Real companies and contacts come from Apify, Exa and BlitzAPI, and Claude judges
  which ones fit. They're written to Salesforce without duplicates, and you get a CSV in the
  Slack thread. This is the first time the agent does real GTM work.

Tasks:
- [ ] You: Salesforce Developer Edition org, Lead external ID field `GTM_Lead_Key__c`, Connected
  App (client credentials); Apify, Exa and BlitzAPI keys into Secret Manager
- [ ] Live clients behind the provider interfaces: Apify Google Maps, Exa, BlitzAPI, Claude judge
  (structured output, cost recorded)
- [ ] Salesforce client: client-credentials login, query for dedupe, composite upsert for Leads
- [ ] Slack file upload (two-step external upload); summary with a LangSmith trace link
- [ ] Cloud Storage bucket (30-day lifecycle) with a copy of each CSV
- [ ] `queue_outreach` writes an `outreach_queue` row and replies "Queued for outreach"
- [ ] Local live smoke: `target=5`, budget $2, real providers and Salesforce, fake Slack
- [ ] Live run 1: "source 5 leads in Denver", $2. Live run 2: "source 20 leads in Denver", $5,
  then click "Create outreach". Record counts, cost and screenshots in the root README

Origin: spec items 1.5–1.6, 9, 10.6, 10.11–10.13.

## 10. Graph 2: outreach and style memory

- **Before:** The rep writes every outreach email by hand from the lead list.
- **After:** The agent drafts emails in *that rep's* voice and posts them as Slack cards with
  **Send / Edit / Cancel**. When a rep edits a draft, the agent learns from the change, so later
  drafts need fewer edits.

Tasks:
- [ ] Package `gtm_agent_graphs/outreach/` (LangGraph): reads `outreach_queue`, one draft per lead
- [ ] Style notes per rep, stored in Postgres
- [ ] Slack cards: Send, Edit, Cancel per draft
- [ ] Learning loop: draft → edit → sent updates the rep's style notes
- [ ] Tests: drafts use the style notes; an edit changes them

Origin: root README roadmap steps 7–9.

## 11. Measurement and evals

- **Before:** No way to answer "is this agent worth it?"
- **After:** Each workflow has scores on five measures: volume (runs), usage (who uses it),
  quality (lead fit, how much reps edit drafts), time saved, and outcomes (meetings booked,
  pipeline). Evals catch it when a prompt change makes results worse.

Tasks:
- [ ] Per-graph metrics from Postgres and LangSmith: volume, usage, quality, time saved, outcomes
- [ ] Eval datasets in LangSmith from failures and human corrections (rejected leads, edited drafts)
- [ ] Eval run in CI for prompt and graph changes

Origin: root README roadmap step 10.

---

## 🅿️ Parked

Kept for reference, not planned. The GTM agent runs without a long-lived sandbox and doesn't need
these.

- Sandbox lifecycle: state machine, snapshots, chaos testing (todos 1)
- Other surfaces: VS Code extension, Chat SDK (todos 2.2)
- The executor's write tool and locks for concurrent executors (todos 3)
- All TypeScript harness items; that harness lives in `past_builds/` (todos 3)
- Cloud Scheduler triggers (spec, not used)

---

## Reference design

Kept from the use-case-1 spec so the items above have the detail they need.

### Leadgen graph

State (typed): `run_id`, `request_id`, `slack_channel`, `slack_thread_ts`, `market`, `target`,
`budget_usd`, `plan`, `approval`, `raw_leads`, `new_leads`, `enriched_leads`, `qualified`,
`low_confidence`, `rejected`, `salesforce_ids`, `artifact_url`, `costs`.

| Node | Kind | Does |
|---|---|---|
| `plan` | Code | Expands the market into its largest cities (Census places), picks keywords from the category config, prices the Maps and Exa ceilings plus model spend, sets the budget |
| `approve` | Interrupt | Posts the plan and budget with Approve / Cancel and pauses; resumes with the decision. Cancel ends the run |
| `source_maps` | Code + Apify | Google Maps search per city and keyword, stops at the target ceiling |
| `source_exa` | Code + Exa | Company searches per city; caches page text for evidence |
| `dedupe` | Code | Removes duplicates within the run and against Salesforce (domain, phone, email) |
| `enrich` | Code + BlitzAPI | Contact lookup only for leads missing an email or phone |
| `qualify` | One Claude call per lead | Verdict, reason, exact quote; code checks the quote is in the page text. No quote → low confidence |
| `write_salesforce` | Code | Upserts qualified leads (external ID = normalized domain), records IDs |
| `artifact` | Code | CSV to Cloud Storage and the thread, summary, "Create outreach" button |

Rules from the shipped pipeline: every paid call reserves its ceiling first; the checkpointer
saves state after every node; model calls have their own cap inside the run budget. Demo
category: independent CFP® financial planners, loaded from config.

**Salesforce Lead fields:** company, website, phone, email, city, state, source, qualification
reason, evidence URL, quote, run ID.

### Data model

| Table | Purpose |
|---|---|
| `requests` | One row per Slack request: event ID (unique), user, channel, thread, text, profile, status |
| `brain_sessions` | Slack thread ↔ Agent SDK session ID, for follow-ups |
| `graph_runs` | Graph name, thread, request, status, current node |
| `approvals` | Run ID, plan, budget, decision, decided by, decided at |
| `budget_reservations` | Paid-call reservations and outcomes (no repeated spend) |
| `cost_ledger` | Actual cost per paid call and per model call |
| `leads` | Leads per run with verdict, evidence, Salesforce ID |
| `outreach_queue` | Requests queued by the "Create outreach" button |
| LangGraph tables | Created by the Postgres checkpointer |

### Infrastructure still to add

| Resource | Setting | Item |
|---|---|---|
| Cloud SQL | Postgres 16, `db-f1-micro`, 10 GB, daily backups | 4 |
| Cloud Tasks | Queue `gtm-agent-work`, OIDC to the service, limited retries | 5 |
| Cloud Storage | Artifact bucket, 30-day lifecycle | 9 |
| Runtime SA roles | Cloud SQL client, Cloud Tasks enqueuer, storage writer | 4, 5, 9 |
| Secrets | `LANGSMITH_API_KEY`, `DATABASE_URL`, `APIFY_TOKEN`, `EXA_API_KEY`, `BLITZ_API_KEY`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_INSTANCE_URL` | 4, 9 |

Estimated cost once complete: about $10–13/month (mostly Cloud SQL), plus provider spend per run,
capped by the approved budget (about $2–3 for 20 leads).

### Risks

| Risk | Mitigation |
|---|---|
| Cloud Tasks waits at most 30 minutes for an HTTP task | Each graph task runs to the next interrupt or the end; long runs split into one task per node, resuming from checkpoints |
| Live spend | Approval before spend, budget reservations, per-run cap, budget alert |
| Salesforce Developer Edition API limits | Composite API, one upsert per lead |
| Duplicate leads across runs | Dedupe against Salesforce plus external ID upsert |

### Open questions

1. Default budget cap per run: $5?
2. Demo category: keep independent CFP® planners from the shipped pipeline?
3. Slack: one channel (for example `#gtm-agent`) plus DMs, or DMs only?
