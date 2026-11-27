// ILLUSTRATIVE - not run
//
// Rung 7 — many rows at once: run-if, a concurrency cap and a page cache.
//
// Why it is not run here: it depends on rung 6's durable workflow, on a database of rows, and on a
// Postgres page cache. None of those exist in this offline build.
//
// The three money levers, in order of how much they save:
//   run-if      — decide BEFORE spending. A row with no website can never be researched.
//   concurrency — a cap set by your providers' rate limits, not by Vercel.
//   page cache  — the same bakery page read once a week instead of once a row.

import { start } from 'workflow/api';
import { runCell } from '@/workflows/run-cell';

const MAX_RUNS_IN_FLIGHT = 5; // set by provider rate limits, not by Vercel

export async function POST(request: Request) {
  const { columnId } = await request.json();
  const eligibleRowIds = await rowsPassingRunIf(columnId); // run-if: filter BEFORE spending anything

  for (
    let batchStartIndex = 0;
    batchStartIndex < eligibleRowIds.length;
    batchStartIndex += MAX_RUNS_IN_FLIGHT
  ) {
    const rowIdBatch = eligibleRowIds.slice(batchStartIndex, batchStartIndex + MAX_RUNS_IN_FLIGHT);
    const startedRuns = await Promise.all(rowIdBatch.map((rowId) => start(runCell, [rowId, columnId])));
    await Promise.all(startedRuns.map((startedRun) => startedRun.returnValue)); // finish this batch first
  }
  return Response.json({ startedRunCount: eligibleRowIds.length });
}

// inside the fetch tool's execute(): the page cache
const cachedPages = await sql`select page_markdown from page_cache
                              where page_url = ${pageUrl} and fetched_at > now() - interval '7 days'`;
if (cachedPages.length) return cachedPages[0].page_markdown;
