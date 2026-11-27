# Research agent, three frameworks

One research agent, built three times: on the **Claude Agent SDK**, the **Vercel AI SDK**, and the
**OpenAI SDK**. The task, the test data, and the expected answers are identical across all three,
so the only thing that changes is the framework, and you can see exactly what each one does for
you.

Built as Repository 2 of a course I wrote (Automation Academy, phase 6). It fills one enrichment
field for the [semi-automated leadgen skills](../semi-automated-leadgen-skills/) pipeline
(Repository 1).

## The task

Given one business, answer one question with cited evidence: **does this bakery already take
online orders?** The agent returns three fields:

- `supportsNoOnlineOrdering`: does the evidence support "no online ordering"? `true` or `false`.
- `researchReason`: one sentence saying why.
- `sourceUrls`: the pages the answer rests on.

Four invented Chicago bakeries test it. Two are easy; two are built to catch bad agents:

| Bakery | What the pages show | Answer |
|---|---|---|
| Bluebird Bakery | The order page says to call or stop by the counter | `true` |
| Oak Street Bakes | A working online store with a cart | `false` |
| Rye & Rosemary Bakery | An "Order Ahead" button that looks like online ordering, but the page behind it says phone or email, pay at pickup | `true` |
| Dos Rios Panaderia | Its pages never say how orders are taken; the only outside listing is stale | `false` (unproven) |

Rye & Rosemary punishes an agent that stops reading too early. Dos Rios punishes an agent that
guesses: unproven means `false`, and a person should look. That's why this needs an agent and not a
keyword match.

## The same design in every build

Each build climbs the same four rungs, one idea at a time:

| Rung | Idea | What happens |
|---|---|---|
| 0 | One model call, no tools | The model can't cite anything, which shows why the rest is needed |
| 1 | One tool | The model asks for a page, your code fetches it, the model answers from it. One page isn't enough: it gets Rye & Rosemary wrong |
| 2 | The agent loop | Repeat until the model stops asking: search, read several pages, search again, write a report. The model decides which tool and how many passes |
| 3 | The verdict | A second, tool-less call applies a written rubric to the report and returns a typed object |

Research and judgment are deliberately separate: research is expensive and reusable, judgment is
cheap and changes often. Change the rubric and you re-run only the verdict call.

## The three builds

### Claude Agent SDK (`claude_agent_sdk/`), the main build

**What it is:** Anthropic's agent harness. `query()` doesn't make a plain API call; it starts the
`claude` program as a subprocess and talks to it over a pipe. That program runs the loop, manages
context, and provides built-in tools like web search.

**How the agent is built:**
- Your tools (`fetch_url`, `search_web`) are ordinary functions, exposed to the agent through an
  in-process MCP server (`tool()` and `createSdkMcpServer()`).
- You configure the loop rather than write it: `allowedTools` decides what runs without asking,
  `maxTurns` and `maxBudgetUsd` cap the tool round trips and the dollars per lead.
- The verdict uses `outputFormat` with a JSON schema.

**What you get:** the most harness out of the box (the loop, built-in tools, permissions, budget
caps), so the least code. The extra files show how it grows: a permission gate where `send_email`
must be approved by a person, and a browser subagent.

**The trade-off:** because the model sits behind a subprocess, offline tests replace `query()`
itself with a recorded session. They prove your code around the SDK, not the SDK call itself.

### Vercel AI SDK (`vercel_ai_sdk/`)

**What it is:** an open-source TypeScript library (`ai` on npm) that makes model calls, runs tools,
and drives the loop inside your own code. It's provider-neutral: the same code can call Claude,
GPT, or others through the Vercel AI Gateway.

**How the agent is built:**
- Tools are defined with `tool()` and Zod schemas.
- `ToolLoopAgent` is a ready-made loop: it calls the model, runs the requested tools, feeds the
  results back, and stops at a step limit (`stopWhen: isStepCount(...)`).
- The verdict uses `Output.object({ schema })`, which asks for an object of that shape and
  validates it.

**What you get:** a middle ground. The library owns the loop and the message list, but you own the
tools, the model choice, and where it runs. Swapping providers is a one-line change.

**Offline tests:** the recordings plug in at the lowest point, as a fake language model (the SDK's
own test double). Everything above it, including the loop, argument checking, and schema parsing,
is the real library.

### OpenAI SDK (`openai_sdk/`)

**What it is:** OpenAI's official TypeScript client (`openai`), using the Responses API. It's an
HTTP client, not an agent framework.

**How the agent is built:**
- You write the loop yourself: a `for` loop calls `responses.create`, finds the `function_call`
  items in the reply, runs each tool, appends a `function_call_output`, and goes again until the
  model stops asking or a round limit is hit.
- Tools are plain function definitions you dispatch by name.
- The verdict uses `responses.parse` with `zodTextFormat`, which validates the reply against a Zod
  schema.

**What you get:** the least magic and the most control. Every step of the loop is visible in your
own code, which makes it the clearest build for understanding what an agent loop actually is.

**Offline tests:** the client lets you supply your own `fetch`, so a recorded transport answers
instead of the network. The real library still builds requests and parses replies. One honest
difference: OpenAI's hosted web-search tool runs on OpenAI's servers, so offline it's replaced by
an ordinary function tool reading recorded search results.

## Side by side

| | Claude Agent SDK | Vercel AI SDK | OpenAI SDK |
|---|---|---|---|
| Kind of library | Agent harness (runs `claude` as a subprocess) | Agent library in your process | HTTP client for one provider |
| Who writes the loop | The SDK | The SDK (`ToolLoopAgent`) | You (`for` loop over `responses.create`) |
| Tools | Functions on an in-process MCP server, plus built-ins like web search | `tool()` with Zod schemas | Function definitions you dispatch yourself |
| Limits | `maxTurns`, `maxBudgetUsd`, `allowedTools` | `stopWhen` step limit | Your own round limit |
| Structured verdict | `outputFormat` (JSON schema) | `Output.object` (Zod) | `responses.parse` + `zodTextFormat` |
| Model choice | Claude | Any provider via AI Gateway | OpenAI |
| Offline seam | Replaces `query()` | Fake language model | Recorded `fetch` transport |
| Code you write | Least | Medium | Most |

The lesson across all three: the agent is the same idea everywhere (a model choosing tools in a
loop until it can answer). The frameworks differ in how much of that loop they own, and in what
you give up for it.

## Shared pieces (`shared/`)

- **Fixtures:** the recorded pages, search results, and model replies, described in
  `shared/FIXTURE_CONTRACT.md`. All bakery domains end in `.example`, so they can never be
  researched live by accident.
- **Replay:** the one module every build imports to read the recordings. Asking for one more model
  turn than was recorded throws an error instead of inventing an answer.
- **Validation:** tests that every build reproduces the same verdicts with the same tool calls.

## Run it

Requires Node.js 22.6+. Everything below is offline and free: no key, no account.

```bash
npm ci
npm test            # Claude build + shared validation
npm run demo        # Claude build on all four bakeries; writes out/research_agent_answers.json

cd vercel_ai_sdk && npm install && npm run rungs && npm test
cd openai_sdk && npm install && npm run rungs && npm test
```

Expected demo output:

```text
Bluebird Bakery: 5 tool calls; supportsNoOnlineOrdering=true
Oak Street Bakes: 5 tool calls; supportsNoOnlineOrdering=false
Rye & Rosemary Bakery: 5 tool calls; supportsNoOnlineOrdering=true
Dos Rios Panaderia: 6 tool calls; supportsNoOnlineOrdering=false
```

**Live mode** (off by default, costs money): the Claude build can research a real business, capped
at 12 turns / $0.25 for research and 3 turns / $0.10 for the verdict:

```bash
npm run research:live -- --allow-paid --company "Your bakery" --website "https://your-bakery.com"
```

The Vercel build goes live with `RESEARCH_AGENT_LIVE_MODE=on` and an AI Gateway key; the OpenAI
build by switching to its live client in your own copy of a rung. Each folder's README has details.

## Beyond rung 3

Rungs 4 to 10 (a server route, a database table of jobs, durable runs, a worker pool, triggers, a
human approval gate, a browser tool) are sketched in each build's `illustrative/` folder or extra
worker files. They're marked `ILLUSTRATIVE - not run`: design notes, not running services. See each
build's `docs/rung_status.md` and `OPTIONAL.md`.

## Feeding results into the pipeline

Keep this folder beside Repository 1 and run its `pipeline.import_research`,
`pipeline.review_research`, and `pipeline.qualify` commands. The imported answer only attaches
evidence; a person confirms it before it counts, and research never qualifies a lead or sends a
message.

Licensed for personal use only (see `LICENSE`).
