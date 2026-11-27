# Rung status — the research agent on the Vercel AI SDK

Eleven rungs. The first four run on your machine. The rest are shown, not run, because each one
needs something this repo deliberately does not include: a deployed Next.js app, a Postgres
database, Vercel's Workflow runtime, or a paid service.

| Rung | What it adds | File | Status | What it would need to run |
|---|---|---|---|---|
| 0 | One model call, no tools | `rungs/rung0_one_model_call.ts` | **runs offline** | nothing |
| 1 | One tool: read a page | `rungs/rung1_one_tool.ts`, `lib/tools/fetchPageTextTool.ts` | **runs offline** | nothing |
| 2 | The agent loop: search, read, repeat | `rungs/rung2_agent_loop.ts`, `lib/researchAgent.ts`, `lib/tools/searchTheWebTool.ts` | **runs offline** | nothing |
| 3 | A typed verdict from a rubric | `rungs/rung3_judge_verdict.ts`, `lib/judgeLead.ts` | **runs offline** | nothing |
| 4 | Somewhere to run it | `illustrative/rung4_run_cell_route.ts` | illustrative | a Next.js app deployed to Vercel |
| 5 | The table: rows, columns, cells | `illustrative/rung5_table_schema.sql`, `illustrative/rung5_fill_template.ts` | illustrative | a Postgres database (Neon) |
| 6 | A durable run per cell | `illustrative/rung6_run_cell_workflow.ts` | illustrative | Vercel's Workflow runtime (`workflow` package) + rungs 4 and 5 |
| 7 | Many rows: run-if, concurrency, cache | `illustrative/rung7_run_column_route.ts` | illustrative | rung 6, plus a page-cache table |
| 8 | Triggers: webhook, schedule, button | `illustrative/rung8_triggers.ts` | illustrative | rung 6, Vercel cron, Vercel BotID (`botid`) |
| 9 | A human gate before sending | `illustrative/rung9_human_gate.ts` | illustrative | rung 6, a Slack app, an email sender |
| 10 | A browser tool for pages that need clicking | `illustrative/rung10_browse_page_tool.ts` | illustrative | a hosted browser (Browserbase, Firecrawl interact) or a Vercel Sandbox |

Two more illustrative files show the paid, live versions of the tools rungs 1 and 2 run offline:
`illustrative/rung1_live_firecrawl_tool.ts` and `illustrative/rung2_live_exa_search_tool.ts`.

Every illustrative file starts with the line `// ILLUSTRATIVE - not run` (or `-- ILLUSTRATIVE - not
run` in the SQL file), so you can always tell at a glance which kind of file you are reading.

## What the tests cover

`npm test` runs four test files, one per runnable rung, over all four fixture bakeries (Bluebird
Bakery, Oak Street Bakes, Rye & Rosemary Bakery and Dos Rios Panaderia), 21 checks in all:

- **rung 0** — the reply matches the recording, no tool was called, and no source is cited.
- **rung 1** — exactly one `fetchPageText` call, with the recorded URL, and the answer comes from
  that page. For Rye & Rosemary Bakery that page is the home page with the Order Ahead button, and
  the order page behind it is never read.
- **rung 2** — the tool calls happen in the recorded order with the recorded arguments (five for
  most bakeries, six for Dos Rios Panaderia, which reads one outside listing), every recorded turn
  is consumed, and the report contains the facts the verdict will rest on.
- **rung 3** — `supportsNoOnlineOrdering`, `researchReason` and `sourceUrls` match
  `../shared/fixtures/expected_verdicts/` exactly, for all four bakeries, and the research that fed
  the judge made exactly the recorded tool calls. Two bakeries settle `true` and two settle `false`,
  and the two unclear ones come out opposite ways: Rye & Rosemary `true`, Dos Rios `false`.

Only rung 3 produces a verdict, because only rung 3 applies the rubric. That is the point of the
ladder: rung 0 cannot answer at all, rung 1 answers from one page, rung 2 gathers the facts, and
rung 3 turns them into the fields a spreadsheet can hold.
