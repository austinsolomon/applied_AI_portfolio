# The research agent on the Vercel AI SDK

This folder is one of three builds of the same small program. The program takes one bakery and one
research question, **does this bakery already take online orders?** It reads evidence on the web
and fills in three fields:

- `supportsNoOnlineOrdering` — does the evidence support no online ordering, true or false?
- `researchReason` — one sentence saying why.
- `sourceUrls` — the pages the answer rests on.

Jordan sells $500 online-ordering websites. This agent researches one field for the same bakery list. It does not qualify the full lead or start outreach.

The other two folders next door (`../claude_agent_sdk/` and `../openai_sdk/`) do exactly the same
job with different tools, so you can see what changes and what does not.

This build uses the **Vercel AI SDK**: an open-source TypeScript library, on npm as `ai`, that makes
model calls, runs tools and drives the loop. It is a library that runs inside your own code, not a
service you sign up for.

## You need

- **Node.js 22.6 or newer.** Check with `node -v`. Node runs the TypeScript files here directly, so
  there is no build step.
- Nothing else. No API key, no account, no payment method.

## Run it

```
npm install          # downloads the AI SDK and Zod, once
npm run rung0        # the first step of the ladder
```

Then the rest:

```
npm run rung1
npm run rung2
npm run rung3
npm run rungs        # all four, in order
npm test             # the tests: one file per rung, 21 checks
```

Everything above is free and works with no internet connection after `npm install`.

## What you are watching

Four rungs of a ladder, each one adding a single idea to the one before it.

- **Rung 0 — one model call.** Ask the model about a small Chicago bakery. It has no tools, so it
  can only answer from what it absorbed during training, and it honestly says it cannot cite
  anything. This rung exists to show why the others are needed.
- **Rung 1 — one tool.** The model still cannot open a link. What it can do is say "please call
  `fetchPageText` with this URL". Your code runs the tool and hands the text back. Now every fact
  has a page behind it. One page is not always enough: Rye & Rosemary Bakery's home page has an
  Order Ahead button, and this rung wrongly decides the bakery already takes online orders.
- **Rung 2 — the agent loop.** Repeat rung 1 until the model stops asking. It searches, opens three
  pages, searches once more, then writes a report. This time it reads the page behind Rye &
  Rosemary's button: call the shop or email a form, pay at pickup. No online ordering after all.
  *The model* decides which tool and how many passes; your code only executes. That is what makes
  it an agent.
- **Rung 3 — the verdict.** A second model call, with no tools, applies a written rubric to the
  report and returns a typed object. Research is expensive and reusable; judgement is cheap and
  changes often, so they are kept apart. Change the rubric and you re-run only this call.

Rungs 4 to 10 — a URL to run it at, the grid of rows and columns, durable runs, batching, triggers,
a human approval gate and a browser tool — are in `illustrative/`. Every one of those files begins
with `ILLUSTRATIVE - not run`, because each needs a deployed app, a database, or a paid service.
`docs/rung_status.md` lists all eleven with what each would need.

## The four bakeries

| Bakery | Its website | What happens | `supportsNoOnlineOrdering` |
|---|---|---|---|
| Bluebird Bakery | `bluebird-bakery.example` | One shop, two named owners, and an order page that says "call us or stop by". | `true` |
| Oak Street Bakes | `oak-street-bakes.example` | An Order Online button that opens a working cart and checkout. | `false` |
| Rye & Rosemary Bakery | `rye-and-rosemary.example` | An Order Ahead button that only leads to a phone number and an emailed form. | `true` |
| Dos Rios Panaderia | `dos-rios-panaderia.example` | Its own pages never say how orders are taken, and the one outside listing is stale. | `false` |

One clearly qualifies, one clearly does not, and two are deliberately unclear. The two unclear ones
fail in opposite directions. Rye & Rosemary punishes an agent that stops reading too early. Dos Rios
punishes an agent that guesses: nothing proves how it takes orders, and the rubric says unproven
means `false`.

In the code a bakery is called a "company" (`companyId`, `loadAllCompanies()`), because the same
agent would work on any kind of business.

## How it runs with no API key

The four bakeries are invented, and every one of their web addresses ends in `.example`, an ending
reserved for documentation that can never belong to a real business. Their pages, the search
results and the model's replies were all recorded once and stored in `../shared/fixtures/`.
`../shared/FIXTURE_CONTRACT.md` describes them.

The recordings are plugged in at the lowest possible point: `lib/replay/recordedTurnLanguageModel.ts`
builds a fake **language model** using the AI SDK's own test double, and hands it to the SDK where a
real provider would go. Everything above that line is the real library doing real work — the
`ToolLoopAgent` harness, the tool argument checking, the step trace, the schema parsing in rung 3.
Only the network is gone. If the code asks for one more model turn than was recorded, the replay
throws instead of inventing an answer.

## Live mode (off by default, and it costs money)

Live mode sends real requests to real paid services, once per bakery, every time you run a rung.
It is off unless you turn it on yourself.

To turn it on, set two variables in the terminal you run the rungs from. No script here reads a
`.env` file, so copying `.env.example` to `.env` changes nothing; `.env.example` is only the list of
variables and what each one is for. On macOS or Linux:

```
export RESEARCH_AGENT_LIVE_MODE=on
export AI_GATEWAY_API_KEY=...        # your Vercel AI Gateway key
npm run rung0
```

In Windows PowerShell, use `$env:RESEARCH_AGENT_LIVE_MODE = "on"` and
`$env:AI_GATEWAY_API_KEY = "..."`. The settings last until you close that terminal.
`AI_GATEWAY_API_KEY` is a Vercel AI Gateway key, which is one key in front of every model provider.
Rung 1 and rung 2 will read pages with a plain `fetch()`; a real product rents a scraping service
for that instead (`FIRECRAWL_API_KEY`, shown in `illustrative/rung1_live_firecrawl_tool.ts`).

Live search is deliberately **not** wired up: this build ships no search provider, so in live mode
rung 2's search tool stops with a message saying exactly that, rather than quietly returning
nothing. `illustrative/rung2_live_exa_search_tool.ts` shows the shape if you want to add one.

Every run prints one line saying which mode it used, so you never have to guess.

## The files

```
lib/
  modelSelection.ts                   offline recording or live model: the choice, in one place
  liveModeSwitch.ts                   the live-mode switch and the one line it prints
  isBeingRunDirectly.ts               lets each rung file be both a script and an import
  replay/recordedTurnLanguageModel.ts the fake language model that replays a recording
  tools/fetchPageTextTool.ts          rung 1's tool: read a page
  tools/searchTheWebTool.ts           rung 2's tool: search
  researchAgent.ts                    rung 2: the agent loop
  judgeLead.ts                        rung 3: the rubric and the typed verdict
rungs/                                one runnable file per rung, 0 to 3
tests/                                one test file per rung, 0 to 3
illustrative/                         rungs 4 to 10, shown but not run
docs/rung_status.md                   all eleven rungs, and what each would need to run
```

Versions are pinned exactly in `package.json` (`ai` 7.0.107, `zod` 4.6.5), so this folder keeps
working the same way later. Where the code here reads differently from the walkthrough page in the
course, `docs/rung_status.md` and the comments in each file say why.
