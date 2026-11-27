// ILLUSTRATIVE - not run. Rung 7: run-if happens here, before anything is spent.
//
// Needs a Postgres database, a `sql` client and rowsPassingRunIf(). See docs/rung_status.md.

export async function enqueueColumn(columnId: number) {
  const eligibleRowIds = await rowsPassingRunIf(columnId);
  await sql`insert into cell_jobs (row_id, column_id)
            select row_id, ${columnId} from unnest(${eligibleRowIds}::bigint[]) as row_id`;
  return eligibleRowIds.length;
}
