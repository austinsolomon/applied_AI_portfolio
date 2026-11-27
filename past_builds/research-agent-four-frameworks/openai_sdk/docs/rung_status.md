# Which rung runs, and which one only reads

| Rung | File | Status | Why |
|---|---|---|---|
| 0 | `worker/rung0_oneModelCall.ts` | **Runs offline** | One `responses.create` call, no tools. |
| 1 | `worker/rung1_oneTool.ts` | **Runs offline** | Two calls around one real function-call dispatch. |
| 2 | `worker/rung2_agentLoop.ts` | **Runs offline** | A real bounded loop; the recordings supply four turns and five tool calls for three bakeries, and five turns and six tool calls for Dos Rios Panaderia. |
| 3 | `worker/rung3_judgeVerdict.ts` | **Runs offline** | Real `responses.parse` with a real Zod schema; `output_parsed` is filled by the SDK. |
| 4 | `illustrative/rung4_serverRoute.ts` | ILLUSTRATIVE - not run | Needs an HTTP host, an auth adapter, a queue and a database. |
| 5 | `illustrative/rung5_tableSchema.sql`, `illustrative/rung5_fillTemplate.ts` | ILLUSTRATIVE - not run | Needs a Postgres database. |
| 6 | `illustrative/rung6_processCellJob.ts` | ILLUSTRATIVE - not run | Leases and checkpoints are database behaviour, not SDK behaviour. |
| 7 | `illustrative/rung7_workerPool.ts` | ILLUSTRATIVE - not run | Needs the queue, the cache and the ledger from rungs 5 and 6. |
| 8 | `illustrative/rung8_triggers.ts` | ILLUSTRATIVE - not run | Needs a public endpoint and a scheduler. |
| 9 | `illustrative/rung9_approvalGate.ts` | ILLUSTRATIVE - not run | Needs a database, a reviewer UI and a delivery provider. |
| 10 | `illustrative/rung10_browserTools.ts` | ILLUSTRATIVE - not run | Needs a browser service (Playwright) you host yourself. |

Rungs 0-3 run with no API key and no network because the `openai` client is given a `fetch` that
answers from `../shared/fixtures/recorded_model_responses/`. See the README section "How can it run
with no API key?".

Rungs 4-10 are excluded from `npm run typecheck` on purpose: they reference adapters (`db`, `queue`,
`auth`, `pages`, `browser`) that this repo deliberately does not ship, so they would not compile and
are not meant to.
