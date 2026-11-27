# The research agent on the OpenAI SDK

This folder is one of three builds of the same small program. Give it a bakery and one research
question:

> Does this bakery already take online orders?

It finds and reads evidence, and returns three things:

- `supportsNoOnlineOrdering` — true or false
- `researchReason` — one sentence saying why
- `sourceUrls` — the pages the answer rests on

`supportsNoOnlineOrdering: true` means the evidence supports no online ordering. A false answer is either contradicted or unproven; read the reason. A person inspects the evidence before Repo 1 applies its original four fit checks.

The other two folders next door do exactly the same job with different tools, over the same four
example bakeries. That is the point: the task is fixed, so what changes between the folders is the
toolkit, and you can see it.

## The four bakeries

All four are invented, and all four are in Chicago, Illinois. One clearly qualifies, one clearly does
not, and two are deliberately unclear.

| Bakery | `companyId` | What the pages show | `supportsNoOnlineOrdering` |
|---|---|---|---|
| Bluebird Bakery | `bluebird_bakery` | One shop. The order page says to call or stop by the counter. | `true` |
| Oak Street Bakes | `oak_street_bakes` | Two shops. The order page is a working online store with a cart. | `false` |
| Rye & Rosemary Bakery | `rye_and_rosemary_bakery` | An "Order Ahead" button that looks like online ordering. The page behind it says to phone or email a form, and pay at pickup. | `true` |
| Dos Rios Panaderia | `dos_rios_panaderia` | Its own pages never say how orders are taken. The only outside listing is undated and inactive. | `false` |

The last two are why this needs an agent and not a keyword match. An agent that stops at the Rye &
Rosemary home page gets it wrong. And at Dos Rios the honest answer is "unproven", so the verdict is
`false` and a person should look.

This build uses the official OpenAI TypeScript library — the package called `openai` — and its
Responses API.

## What you need

Node.js version 22.6 or newer. Check with:

```
node --version
```

Nothing else. No OpenAI account, no API key, no credit card, no internet connection.

## Set it up

From inside this folder:

```
npm install
```

That downloads the `openai` library and a few helpers. It does not contact OpenAI.

## Run the first rung

```
npm run rung0
```

Rung 0 is one model call with no tools at all. You will see the model asked about each of the four
bakeries, and answering — honestly — that it does not know and has nothing to cite. That answer is
the reason the other rungs exist.

## Run all four

```
npm run rungs
```

That runs rungs 0, 1, 2 and 3 in order:

| Rung | Command | What you see |
|---|---|---|
| 0 | `npm run rung0` | One model call, no tools. It cannot answer. |
| 1 | `npm run rung1` | One tool, used once. The model asks for a page of the bakery's website, your code reads it, the model answers from it. Now every fact has a URL behind it. |
| 2 | `npm run rung2` | The agent loop. The model searches for the bakery, reads its pages, searches again about online ordering, then writes a report — and nobody wrote that sequence down in advance. Dos Rios Panaderia takes one more round than the others, because the model follows a search result to an outside listing. |
| 3 | `npm run rung3` | The whole agent: research, then a second tool-less call that turns the report into the two verdict fields a spreadsheet can hold. |

And the tests:

```
npm test
```

Twenty-four checks. They assert the verdict for every bakery, and that the tool calls this build
made are exactly the ones the recordings expect.

There is also `npm run typecheck`, which checks this code against the type definitions of the
`openai` version actually installed. That is how we caught the places where the walkthrough page's
code needed correcting; they are listed in `docs/api_corrections_openai_sdk.md`.

## How can it run with no API key?

Because it is not talking to OpenAI. It is replaying recordings.

The `openai` library is an HTTP client: every call ends up calling `fetch`. The library lets you
supply your own `fetch`. So `worker/recordedResponsesTransport.ts` hands it one that never touches
the network and answers from the recorded model responses in `../shared/fixtures/` instead.

Everything above that one substitution is the real thing. The real library builds the real request,
parses the reply, aggregates `output_text`, and in rung 3 really does validate the answer against the
schema and fill `output_parsed`. The loop is a real loop, the tool dispatch is real dispatch. Only
the wire is recorded.

Two consequences worth knowing:

- **The recordings decide what the model "says".** Editing the prompt text in a rung will not change
  the answer offline. That is what makes the tests stable and the comparison between the three builds
  fair.
- **Asking for one more turn than was recorded is an error, not a shrug.** The replay layer throws
  `ReplayExhaustedError`, which means your loop ran more times than the recording expects.

One honest difference from the walkthrough page: OpenAI has a *hosted* web-search tool, which runs
inside OpenAI's own servers. There is nothing for a recording to stand in for, so offline the search
step is an ordinary function tool reading the recorded search results, named `searchTheWeb`. Rung 2
says so in a comment at the line where the hosted tool would otherwise go.

## Rungs 4 to 10

The ladder continues past rung 3 — somewhere to run it, a database, crash recovery, scale, triggers,
a human approval gate, a browser tool. Those live in `illustrative/`, and **every one of those files
starts with the line `ILLUSTRATIVE - not run`**. They are there to read. They refer to services you
would build for yourself (a queue, a database, a page reader, a browser host), so they are not
imported by anything here, not type-checked and not tested.

## Live mode

Live mode means: stop replaying, and send the requests to OpenAI for real.

**It is off by default, and it costs money.** Every request to OpenAI is billed to whoever owns the
key. Nothing in this repo turns it on for you; there is no `--live` flag wired into the rung scripts.
To use it you would call `createLiveClient()` from `worker/openaiClientFactory.ts` in place of
`createOfflineReplayClient()`, in your own copy of a rung.

It needs two environment variables, listed in `.env.example`. Nothing here reads a `.env` file, so
set them in the terminal you run your live copy from (`export OPENAI_API_KEY=...` on macOS or Linux,
`$env:OPENAI_API_KEY = "..."` in Windows PowerShell):

- `OPENAI_API_KEY` — your own key from your own OpenAI account
- `OPENAI_MODEL` — which model to use, chosen by you, because price and capability differ

Even in live mode the four example bakeries are invented and their domains end in `.example`, a name
reserved for documentation, so the pages cannot be fetched. A live run would answer about bakeries
that do not exist. Live mode is here to show you the seam, not to research anything real.

## The files

```
package.json                            pinned versions, one per build folder
tsconfig.json                           type checking only; there is no build step
.env.example                            the two variables live mode would need
worker/
  openaiClientFactory.ts                where an OpenAI client is built: offline, or live
  recordedResponsesTransport.ts         the offline seam - a fetch that answers from recordings
  researchTools.ts                      the two tool schemas and the dispatcher that runs them
  rung0_oneModelCall.ts                 rung 0
  rung1_oneTool.ts                      rung 1
  rung2_agentLoop.ts                    rung 2
  rung3_judgeVerdict.ts                 rung 3, and the end-to-end research-then-judge run
tests/                                  one test file per rung
illustrative/                           rungs 4-10, read-only, never executed
docs/rung_status.md                     which rung runs, which is illustrative, and why
docs/api_corrections_openai_sdk.md      where the walkthrough page's code differs from this code
```

The recordings themselves are shared with the other two builds and live in `../shared/`. Nothing in
this folder writes to them; the contract they follow is `../shared/FIXTURE_CONTRACT.md`.
