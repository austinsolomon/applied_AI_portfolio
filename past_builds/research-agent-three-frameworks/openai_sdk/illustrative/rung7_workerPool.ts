// ILLUSTRATIVE - not run
//
// Rung 7, "Many rows: run-if, a concurrency cap, a page cache". One cell becomes a whole column:
// the same question asked of every bakery on the list, skipping rows the run-if rule rules out.
// Note the run-if rule is evaluated in code, never with eval() on a rule string.

// App adapters; the job key is unique in storage.
for (const row of selectedRows) {
  if (!evaluateRule(column.runIf, row.leadFields)) continue;
  await queue.enqueueOnce({
    workspaceId, rowId: row.id, columnId: column.id,
    revision: column.configRevision,
  });
}

const CONCURRENCY = 4;
await Promise.all(Array.from({ length: CONCURRENCY }, () => workerLoop()));

// Inside pages.readPublic: validate the URL BEFORE reading or filling the cache.
const key = normalizePublicUrl(url);
const cached = await pageCache.getFresh(key);
if (cached) return cached;
const page = await fetchPublicPageWithLimits(key);
await pageCache.put(key, page, { ttlSeconds: 3600 });
return page;
