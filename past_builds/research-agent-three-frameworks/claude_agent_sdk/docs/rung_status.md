# Which rungs run, and which are only drawings

Every rung answers one question about one bakery: does this bakery already take online orders? The
four recorded bakeries are Bluebird Bakery, Oak Street Bakes, Rye & Rosemary Bakery and Dos Rios
Panaderia, all invented, all on `.example` web addresses.

Rungs 0-3 run offline, with a test each. Rungs 4-10 are files you can read, not files you can run.
Each of them carries `ILLUSTRATIVE - not run` on its first line.

| Rung | File(s) | Status | What it would need to run |
|---|---|---|---|
| 0 | `worker/one-model-call.ts` | runnable offline, tested | nothing |
| 1 | `worker/research-tools.ts`, `worker/run-single-page-research.ts` | runnable offline, tested | nothing |
| 2 | `worker/run-research.ts` | runnable offline, tested | nothing |
| 3 | `worker/judge-lead.ts` | runnable offline, tested | nothing |
| 4 | `worker/server.ts`, `Dockerfile` | illustrative | a container host, a Postgres database, `readRequestBody`, a real API key |
| 5 | `db/schema.sql`, `lib/fill-template.ts` | illustrative | a Postgres instance; the grid UI is a separate app |
| 6 | `worker/process-cell-job.ts` | illustrative | the `cell_jobs` table, a `sql` client, the cell load/save functions |
| 7 | `lib/enqueue-column.ts`, `worker/pool.ts` | illustrative | rung 6's queue, the `page_cache` table |
| 8 | `worker/server.ts` (webhook), `worker/schedule.ts` | illustrative | rung 4's server, the `node-cron` package, a webhook secret |
| 9 | `worker/approval-gate.ts` | illustrative | a Slack app, an outreach tool server, a suppression list, a live `query()` |
| 10 | `worker/browser-subagent.ts` | illustrative | `npx` at run time, network access, and a pinned `@playwright/mcp` version |

Two notes on the offline rungs, so nobody claims more than is true:

1. Offline mode replaces `query()` with a recorded-transcript stand-in. It exercises this build's own
   code fully and the SDK not at all. See the README's "Offline mode replays a recorded agent
   session".
2. Rung 2's built-in `WebSearch` is a live-mode-only tool, because built-ins live inside the `claude`
   process. Offline, rung 2 allows this build's own `search_web` tool over the recorded searches.
