# Lead-gen agent: design

This document describes the target design. The [README](../README.md) tracks which parts are built.

## 1. Goal

A lead-generation team keeps a goal per market: how many qualified leads each market should have available. An
operator watches a dashboard, picks the markets furthest below goal, configures scrapers, checks sample quality,
runs the full scrape, removes leads already in the CRM, enriches and qualifies the rest, and loads them into the
CRM.

This project replaces the operator's routine work with an agent while keeping a human in control of spend: the
human approves one plan and one budget per run, and reviews the agent's recorded decisions on a weekly cadence.

Scope is one category per deployment (a buyer of this system wants one category). The demo category is mobile and
in-home massage providers in US markets.

## 2. Components

**Type:** *Pipeline* components produce leads. *Hardening* components make the pipeline safe, observable and
repeatable at scale.

| # | Component | Type | What it does | Technology |
|---|---|---|---|---|
| 1 | Demand dashboard | Pipeline | Leads Available % per market, biggest gap first | Streamlit · Postgres view `gap_view` |
| 2 | `read_gaps()` | Pipeline | The agent reads the same view the dashboard renders | Postgres view · psycopg 3 |
| 3 | Category config | Pipeline | The one category: keywords, expected qualification rate, eligibility rubric, excluded hosts | `config/category.yaml`, changed by pull request |
| 4 | Scraper planner | Pipeline | Expands each gap market into cities × keywords, sized to the goal, with a cost estimate | Postgres `markets`, `places`, `scrape_plans`, `scrape_plan_cells` · Claude for keyword judgment |
| 5 | Plan approval | Hardening | The only human step: approve the plan and a dollar budget | Terminal prompt · Postgres `approvals` |
| 6 | Sample scrape | Pipeline | Small Google Maps run per cell; aborts at N new unique businesses or $X | Apify Google Maps actor via `apify-client` |
| 7 | Sample grader | Pipeline | Purity per keyword and in-market share → proceed, reconfigure or abort | Python · rubric from config |
| 8 | Full scrape | Pipeline | Continues the sampled run, or a fresh sweep after reconfiguring | Apify · pandas · Postgres `raw_leads`, `seen_leads` |
| 9 | Lookalike sourcing | Pipeline | For markets still under target: finds businesses similar to that market's qualified leads | Exa `find_similar` via `exa-py` |
| 10 | Dedupe | Pipeline | Drops any lead already in the CRM by Google place ID, phone, email or website domain | Postgres, exact match on normalized keys |
| 11 | Contact enrichment | Pipeline | Phone and email per lead; a plain API call, cached per domain | BlitzAPI |
| 12 | Qualification | Pipeline | Gathers evidence and judges each lead against the rubric, citing sources | Claude API structured outputs · Claude Agent SDK for hard cases · Firecrawl |
| 13 | CRM write | Pipeline | Inserts qualified leads in one transaction; conflicts reported, never lost | Postgres `INSERT … ON CONFLICT DO NOTHING` |
| 14 | Orchestrator agent | Pipeline | Runs the pipeline in order, retries, reconfigures within the approved budget | Claude Agent SDK · skills in `.claude/skills/` |
| 15 | Budget guard | Hardening | After approval, blocks any tool call outside the approved plan or budget | Agent SDK pre-tool-use hook |
| 16 | Run state | Hardening | Nothing runs twice; runs resume where they stopped | Postgres `runs`, `ledger`, `seen_leads` |
| 17 | Cost ledger and traces | Hardening | Tokens, dollars, latency and outcome of every model and tool call | Postgres `cost_ledger`, `traces` |
| 18 | Eval capture and review | Hardening | Every sample verdict and qualification decision recorded; reviewed weekly; labels become test fixtures | Postgres `eval_records`, `eval_labels` · Streamlit |
| 19 | Job queue and worker pool | Hardening | Qualification fans out to concurrent workers with capped concurrency and jittered retries | Postgres `SELECT … FOR UPDATE SKIP LOCKED` · `asyncio` |
| 20 | Page cache | Hardening | A page is fetched at most once a week | Postgres `page_cache` |
| 21 | Retrieval and memory | Hardening | Past samples and recorded lessons inform the planner | Postgres full-text search |
| 22 | Demo dataset | Hardening | Fictional, deterministic markets, goals and CRM leads (recorded scrapes later) | `leadgen db seed-demo` |
| 23 | Offline replay | Hardening | Recorded agent sessions stand in for live model calls, so tests and the demo cost nothing | JSON fixtures |
| 24 | Migrations | Hardening | Schema changes as numbered SQL files, each applied once in a transaction; edited history refused | `src/leadgen/migrations/` · `leadgen db migrate` |
| 25 | Tests and eval regression | Hardening | Every stage tested against a real Postgres; grader and judge fixtures must keep passing | pytest · respx |
| 26 | Tooling and CI | Hardening | Pinned dependencies, lint, typed config; lint → migrate → test → evals → build on every push | uv · ruff · pre-commit · pydantic-settings · GitHub Actions |
| 27 | Container and deployment | Hardening | One image; hosted database; weekly scheduled run; keys outside the code | Docker · Supabase (Postgres host) · Cloud Run Jobs · Cloud Scheduler · Secret Manager · Workload Identity Federation |

## 3. A run, step by step

| # | Step | What happens | Human |
|---|---|---|---|
| 0 | Trigger | Cloud Scheduler starts the job weekly, or someone runs `leadgen plan` | |
| 1 | Read demand | `gap_view` computes, per market, Leads Available % = available ÷ (goal − already assigned). The dashboard and the agent read the same view, so the agent never scrapes a screen | |
| 2 | Find gaps | Markets under 100%, biggest gap first; cells already run are skipped | |
| 3 | Plan | Each gap market is expanded into cities and keywords; the plan is sized to the goal and priced | |
| 4 | Approve | The plan, expected leads and total estimated cost are shown; the human approves once and sets a budget | **yes** |
| 5 | Sample | A small scrape per cell, graded for purity and in-market share | |
| 6 | Reconfigure or abort | Weak keywords are dropped and re-sampled if the budget allows; otherwise the cell waits for the next plan | |
| 7 | Full run | Passing cells are scraped in full; the same business is never stored twice | |
| 8 | Lookalikes | Markets still under target get Exa lookalike sourcing, seeded from that market's qualified leads | |
| 9 | Dedupe | Leads already in the CRM are dropped before anything is spent on them | |
| 10 | Enrich | Phone and email lookup per lead | |
| 11 | Qualify | Evidence gathered and judged; unclear cases escalate to agentic research | |
| 12 | Write | Qualified leads inserted into the CRM in one transaction | |
| 13 | Report | Spend per stage, leads per dollar; the dashboard shows the smaller gap | |
| — | Weekly review | Sample verdicts and qualification decisions are reviewed and labelled; labels become regression fixtures | **yes, after the fact** |

## 4. Market expansion

A market is a center point and a service radius (default 50 km). `expand_market("Los Angeles")` returns every US
Census place inside the radius above a population floor (default 5,000), largest first, each with its state
(`Pasadena, CA`), capped at a configurable count (default 40). The city list comes from public data, not from the
model, so it never contains invented or ambiguous towns.

Keywords come from the category config, ordered by the purity they produced in past samples. Cities × keywords form
the coverage matrix. The plan schedules cells from the strongest corner outward until the raw lead target
(goal ÷ expected qualification rate) is covered.

The same center and radius drive the grader's in-market check: a result farther from the center than the radius is
counted as out of market.

## 5. Qualification

Qualification follows the shape of a web-research enrichment column: a prompt with parameters, a gathering step,
a reasoning step, and a fixed JSON answer that later steps can rely on.

1. **Column definition.** A row in `agent_columns` holds the prompt template (with `{{fields}}` from the lead),
   the rubric, which pages and searches to gather, the output schema, a run-if condition and the model.
2. **Tier 1, fixed gathering.** The lead's website (home, services, about) and one search are fetched through
   Firecrawl and reduced to an evidence block with source URLs.
3. **Tier 1, judgment.** One Claude call reads the rubric and the evidence and returns schema-validated JSON:
   verdict (`qualified`, `rejected` or `review`), reason, evidence quote, source URL, eligible services found, and
   whether the business serves the market.
4. **Tier 2, only when Tier 1 returns `review`.** An Agent SDK research loop with a turn limit decides which pages
   to read next, then the judgment runs again.

Most leads finish at Tier 1's cost. Only unclear leads pay for an agent. When evidence stays unclear, the answer is
`review`, never a guess.

Search and scraping sit behind a small interface (`search`, `fetch_page`), with Firecrawl as the live backend and
httpx with trafilatura as a free fallback and test backend.

## 6. One database

| Role | Postgres feature |
|---|---|
| CRM | `crm_leads`; Postgres-generated normalized phone, email and domain; unique place ID, phone and email; domain indexed as a match key |
| Dedupe | `EXISTS` on normalized keys |
| CRM write | `INSERT … ON CONFLICT DO NOTHING` in a transaction |
| Dashboard and gaps | view `gap_view`: goal, assigned, still needed, available, Leads Available %, goal met, short by |
| Markets and places | `markets` (center, radius), `places` (US Census Gazetteer) |
| Plans and approval | `scrape_plans`, `scrape_plan_cells`, `approvals` |
| Run state | `runs`, `ledger`, `seen_leads` with unique constraints |
| Queue | `cell_jobs` with `FOR UPDATE SKIP LOCKED` |
| Cache | `page_cache` with a freshness check |
| Ledgers and evals | append-only `cost_ledger`, `traces`, `eval_records`, `eval_labels` |
| Retrieval and memory | full-text search (`tsvector` with a GIN index), `lessons` |

The code targets plain PostgreSQL 16 through `DATABASE_URL`. Local development and CI use a Postgres container;
production uses Supabase's hosted Postgres. Any Postgres host works.

## 7. Decisions and trade-offs

**Claude Agent SDK over a hosted agent platform.** A hosted platform runs the loop and provides sandboxes, state,
secrets, memory, scheduling and budgets. Building those pieces here keeps every mechanism visible and testable, and
keeps cost fully controlled: tokens only, a model per role, turn caps and a ledger.

| Concern | Hosted platform provides | Common self-built choice | Here |
|---|---|---|---|
| Agent loop | Hosted loop | Hand-written loop, LangGraph, Claude Agent SDK | Hand-written once on the Claude API, then the Claude Agent SDK |
| Durable state | Sessions and events | Temporal, Inngest, Postgres checkpoints | Postgres run tables |
| Approval | Tool confirmations | Workflow interrupts, Slack bots | One plan approval and a pre-tool-use hook |
| Secrets | Vaults | Cloud secret managers | GCP Secret Manager |
| Memory | Memory stores | pgvector, Redis | Postgres `lessons` with full-text search |
| Scheduling | Scheduled deployments | cron, Cloud Scheduler | Cloud Run Jobs and Cloud Scheduler |
| Spend cap | Session budgets | Hand-rolled accounting | Cost ledger, turn caps and the budget guard |
| Tracing | Session viewer | LangSmith, Langfuse | `traces` table |

**One approval, then a guard instead of more questions.** Approving every scrape does not scale and trains people
to click through. The human approves scope and budget once; the hook enforces both on every tool call; the weekly
review catches quality problems after the fact.

**Agents only where judgment is needed.** Contact lookup has a fixed input and output, so it is a function.
Qualification is a single structured-output call by default and an agent only for hard cases.

**Public data for geography.** City lists come from the US Census Gazetteer, not from a model.

**Exact-match dedupe.** Normalized keys are deterministic and easy to test. Fuzzy name matching is left out until
data shows it is needed.

## 8. Practices carried over from running the pipeline in production

- Count raw targets in unique businesses, not result rows. A run once declared its target met while a third of the
  rows were the same businesses returned by several searches.
- Discount sample purity before committing to a full run. Small samples overstate full-run quality by 15–20
  points.
- Record which plan cell each scraper run belongs to, so results can never be attributed to the wrong market.
- Persist every run's results whatever way the run ended. An aborted run still has paid-for data.
- Cap concurrency against third-party endpoints and retry with jitter; more workers past the endpoint's ceiling
  lose rows.
- Never resend leads a team has already rejected without new evidence.

## 9. Out of scope

- CRM routing beyond new-versus-duplicate: status precedence, reactivating closed leads.
- Lead scoring and tiering.
- Multiple categories per deployment.

## 10. Cost

| Tool | Open source | Cost |
|---|---|---|
| Python, uv, ruff, pytest, respx, pre-commit, pydantic, pandas, psycopg, httpx, trafilatura | yes | free |
| PostgreSQL, Streamlit, Docker Engine | yes | free |
| Supabase (hosting only) | yes | free tier |
| Claude API and Claude Agent SDK | SDKs yes | per token |
| Apify | client yes | per run |
| Exa | client yes | per request |
| Firecrawl | yes (self-hostable); hosted API used | per credit |
| BlitzAPI | no | per lookup |
| GitHub Actions, Google Cloud | no | free tiers |

Every paid call is recorded in the cost ledger and counted against the approved budget.
