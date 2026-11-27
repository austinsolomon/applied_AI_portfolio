# Repository 2 — Research agent built on the Claude Agent SDK

**Phase 6 of Automation Academy.** You already ran Source → Enrich → Qualify →
Outreach in Repository 1. Now zoom into one missing Enrich field: does the bakery
already take online orders? The required implementation uses **Claude Agent SDK**,
a library that lets your program use Claude's tool-selection loop.

An **agent** is a program that asks a model to choose its next action from the
information so far. A **tool** is a function your program lets the model request.
A **skill** is saved instructions for a task. A skill can call this agent; these
are different building blocks, not successive levels of autonomy.

## The connection you already used in Outreach

The Outreach tower introduced a tool request and a returned result. Repository 1
then exposed its checked-email and meeting actions through a local MCP server.
Here the same connection pattern serves research: `fetch_url` receives a page URL
and returns text. The agent uses that text to choose whether another read is needed.

`claude_agent_sdk/worker/research-tools.ts` defines the tools with `tool()` and
`createSdkMcpServer()`. `run-research.ts` connects that server through
`mcpServers`. Live search uses the SDK's built-in `WebSearch`; the offline replay
uses the recorded `search_web` handler. MCP connects tools; the model and program
determine the next action. Research does not automatically accept an answer,
qualify the bakery or send a message.

## Start here: the free local rehearsal

You need Node.js 22.6 or newer (`node --version`). Install it from nodejs.org if
needed. Download the ZIP from the course and unzip it, or use the personal copy
command on your course account page. Both give you a folder called
`research-agent-four-ways`; you do not need a GitHub account. From the folder
that holds it, run:

```bash
cd research-agent-four-ways
npm ci
npm test
npm run demo
```

The `cd` puts you in the root folder that contains this README and package.json;
run the npm commands there. `npm ci` downloads the pinned libraries once. After
installation, `npm test` and `npm run demo` are offline and need no account or
service key. Demo always chooses offline mode even if your computer already has
provider credentials.

The required learning path is one Claude Agent SDK build. Vercel/OpenAI and
advanced hosting designs are optional references, described in `OPTIONAL.md`.

## What you should see

```text
OFFLINE REHEARSAL: saved model choices and saved pages. No live model, no network.
Bluebird Bakery: 5 tool calls; supportsNoOnlineOrdering=true
Oak Street Bakes: 5 tool calls; supportsNoOnlineOrdering=false
Rye & Rosemary Bakery: 5 tool calls; supportsNoOnlineOrdering=true
Dos Rios Panaderia: 6 tool calls; supportsNoOnlineOrdering=false
```

Each answer also prints a sentence explaining the evidence. The full answer file
is `out/research_agent_answers.json`. These are invented course bakeries and
saved example conversations, not a fresh investigation of real businesses.

`supportsNoOnlineOrdering` means “the evidence supports no online ordering.”
False has two possible meanings: the bakery has online ordering, or the evidence
is insufficient. Read `researchReason` and `platform_kind`; never turn uncertainty
into “no ordering.” These answers do not qualify a lead or send a message.

## Trace one bakery

Rye's home page says **Order Ahead**. That phrase is not proof of a checkout.
The next page says to call or email a form and pay at pickup. The agent chooses
to read that page and returns the source. Dos Rios's pages do not settle the
question, so it stays unresolved. Both behaviors matter more than a fluent answer.

Read these files in order:

1. `claude_agent_sdk/worker/research-tools.ts`: ordinary functions exposed as tools.
2. `claude_agent_sdk/worker/run-research.ts`: the SDK call, tools and turn limits.
3. `claude_agent_sdk/worker/judge-lead.ts`: turns the report into three required fields.
4. `scripts/demo.ts`: saves evidence using the same lead IDs as Repository 1.

The older `rung0`–`rung3` commands remain available inside `claude_agent_sdk`.
A **rung** just means a step in that optional explanation. You do not need eleven
levels of infrastructure to complete your first agent.

## Connect the result to Repository 1

Keep the two repository folders, `leadgen-skills-pipeline` and
`research-agent-four-ways`, next to each other. Run Repository 1's normal offline
pipeline first, then from its root folder run:

```bash
python3 -m pipeline.import_research --answers ../research-agent-four-ways/out/research_agent_answers.json
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" --reviewed-by "Your name" --confirm
python3 -m pipeline.qualify --offline
```

Use `py` instead of `python3` on Windows if that is your Python command. The import
attaches evidence to unresolved records. It does not overwrite already checked
values or approve its own answer. Read the source excerpt, then confirm it.
Rye becomes the tenth qualified record; Dos Rios stays in review. The current
baseline is 9 qualified, 12 rejected, 2 review → 8 emails → 5 deliveries →
2 replies → 1 confirmed meeting → 0 customers. Checking Rye does not send another
email or create another meeting.

## What offline verification proves

Offline mode replays saved model choices in place of the SDK's `query()` call.
The real tool handlers, evidence files, output validation and import path run.
The SDK's live model/subprocess loop does **not** run. Therefore offline tests
verify our surrounding code and data; they cannot prove live authentication or
fresh model behavior. The fictional domains end in `.example` and cannot be
researched on the live internet.

## Getting updates

If you used the copy command, open your course account page, get a fresh command
for this repository and run the one under **Update my copy**, from the folder
that holds `research-agent-four-ways`. It keeps your own changes. A plain
`git pull` will not work, because your copy carries no access token. If you
started from the ZIP, download the ZIP again.

## Optional: research a real bakery with the actual SDK

This uses your own Claude access and can cost money. We have not made a paid
call to test it. Install dependencies first. Set `ANTHROPIC_API_KEY` in your local
environment or configure authentication as described in the official SDK docs.
Then explicitly choose a real public website and opt into a live run:

```bash
npm run research:live -- --allow-paid --company "Your bakery" --website "https://your-bakery.com"
```

Do not copy the sample domain literally. The command rejects fictional `.example`
fixtures. It uses the real SDK `query()`, built-in WebSearch and our page-reading
tool. The research is capped at 12 turns/$0.25, with a separate answer check capped
at 3 turns/$0.10. These are code limits, not a promise about any provider's bill.
It saves `out/live-research.json` for inspection; it never sends outreach or writes
a checked pipeline value. A plain page fetch cannot read every JavaScript-driven
site. An inaccessible or ambiguous page must remain unresolved.

Official references: [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview)
and [TypeScript API](https://platform.claude.com/docs/en/agent-sdk/typescript).
The lockfile pins the actual installed dependencies; `npm ci` reproduces them.
