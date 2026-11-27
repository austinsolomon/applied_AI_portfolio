# API corrections: the OpenAI SDK ladder

What differs between the code printed on the course's OpenAI SDK walkthrough page and the code that
actually runs in this folder.

**Versions checked against, and pinned in this folder's `package.json`:**
`openai@7.20.0`, `zod@4.6.5`, `typescript@5.9.3`, `@types/node@24.10.1`, Node v25.8.1.

Method: each rung's snippet was compiled with `tsc --strict` against the installed type definitions
(adapters the page names as yours — `pages`, `db`, `queue`, `auth`, `browser` — declared as stubs so
the SDK calls themselves could be checked), then the corrected version was run against the shared
recordings.

## Headline

**The page's API names are correct.** `responses.create`, `responses.parse`, `output_text`,
`output_parsed`, `usage`, `status`, the `function_call` / `function_call_output` item shapes,
`call_id`, `strict`, the hosted `{ type: 'web_search' }` tool, `zodTextFormat` and its
`openai/helpers/zod` import path, and the `ResponseInput` type from
`openai/resources/responses/responses` all exist in `openai@7.20.0` and behave as the page says.
Rungs 0 and 3 compile clean exactly as printed. One real correction, in rung 2, is below.

## 1. Correction: `input.push(...r.output)` does not type-check

| | |
|---|---|
| **Rung** | 2 (agent loop) |
| **Page file** | `worker/run-research.ts` |
| **Repo file** | `worker/rung2_agentLoop.ts` (same line also in `worker/rung1_oneTool.ts`) |
| **Page says** | `input.push(...r.output); // retain ALL items, including reasoning` |
| **Reality** | `error TS2345: Argument of type 'ResponseOutputItem' is not assignable to parameter of type 'ResponseInputItem'.` In `openai@7.20.0` the output-item union is not a subtype of the input-item union: `ResponseComputerToolCallOutputItem.status` may be `'failed'`, which the input-side `ComputerCallOutput.status` does not allow. |
| **Fix used** | `conversationInput.push(...(response.output as ResponseInputItem[]));` with a comment saying why. |

The page's *advice* is right — feeding every output item back, reasoning items included, is what the
API expects, and it is the thing beginners most often get wrong. It is the types that are out of
step, not the instruction. If the page is corrected, correct the line, not the lesson.

## 2. Not errors, but differences a reader will notice

These are deliberate choices in the runnable build, not mistakes on the page. Listed so nobody
"fixes" one to match the other.

| Rung | Page | Repo | Why |
|---|---|---|---|
| 1, 2 | Tools are named `fetch_url`, and search is the hosted `web_search` tool | Tools are named `fetchPageText` and `searchTheWeb`, both ordinary function tools | The shared recordings record those two names (`../shared/FIXTURE_CONTRACT.md` §7) and all three builds must dispatch the same calls. The hosted `web_search` runs inside OpenAI's own request, so there is nothing offline for a recording to stand in for. Rung 2 marks the line where the hosted tool would go. |
| 1 | `pages.readPublic(args.url)` | `fetchRecordedPageTextByUrl(pageUrl)` from `../shared/replay` | The page states `pages.readPublic` is an app adapter, not an SDK method. Offline, the adapter is the recordings. |
| 3 | Verdict schema has two fields: `supportsNoOnlineOrdering`, `researchReason` | Three fields: those two plus `sourceUrls` | The fixture contract requires the sources behind a verdict, and the expected-verdict fixtures assert them. |
| 0 | `new OpenAI({ maxRetries: 0 })` reading `OPENAI_API_KEY` from the environment | Offline path passes an explicit placeholder key and a custom `fetch` | No key exists offline. The live path in `worker/openaiClientFactory.ts` does what the page describes. |
| 1 | The snippet declares `toolResult` at module level, referring to a `requestedCall` that only exists inside the loop | The dispatch happens inside the loop | The page's block is an excerpt, not a file; it is not runnable as printed and does not claim to be. |

## 3. Checked and found correct (no change needed)

- `client.responses.create({ model, instructions, input, tools, max_output_tokens })` — all present.
- `response.status !== 'completed'`, `response.output_text`, `response.usage`, `response.id`.
- Refusal detection: `item.type === 'message' && item.content.some(part => part.type === 'refusal')`.
- `{ type: 'function_call_output', call_id, output }` as an input item.
- `tools: [{ type: 'web_search' }, fetchTool]` — `WebSearchTool.type` accepts `'web_search'`.
- `import { zodTextFormat } from 'openai/helpers/zod'` — exists, and works with Zod 4 as well as 3.
- `responses.parse({ text: { format: zodTextFormat(schema, 'lead_verdict') } })` → `output_parsed`,
  verified running for real against the recordings, not just compiled.
- `import type { ResponseInput } from 'openai/resources/responses/responses'` — correct path.
