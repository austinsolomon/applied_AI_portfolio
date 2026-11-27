// ILLUSTRATIVE - not run. Rung 7: many rows at once - the concurrency cap is the number of loops,
// plus the page cache that lives inside the fetch_url handler.
//
// Needs the rung 6 queue and a Postgres database. See docs/rung_status.md.

const MAX_CELLS_IN_FLIGHT = 5; // bounded by container memory (about 1 GiB each) and provider rate limits

async function workerLoop() {
  while (true) {
    const cellJob = await claimNextCellJob();
    if (!cellJob) {
      await sleep(2_000);
      continue;
    }
    await processCellJob(cellJob);
  }
}
for (let workerIndex = 0; workerIndex < MAX_CELLS_IN_FLIGHT; workerIndex++) void workerLoop();

// inside the fetch_url handler: the page cache
const cachedPages = await sql`select page_markdown from page_cache
                              where page_url = ${pageUrl} and fetched_at > now() - interval '7 days'`;
if (cachedPages.length) return { content: [{ type: 'text', text: cachedPages[0].page_markdown }] };
