// ILLUSTRATIVE - not run
//
// Rung 4, "Somewhere to run it". This file is a sketch, not part of the offline build: `auth`, `db`
// and `queue` are adapters you would write against your own host, and nothing here is imported by
// any runnable rung or test. Read it; do not expect it to execute.
//
// The idea: a short HTTP request accepts the work and returns a job id. A long-lived worker does the
// research, because research takes longer than a web request should.

// Architecture sketch: auth, db and queue are application adapters.
export async function POST(request: Request) {
  const user = await auth.requireUser(request);
  const { rowId, columnId } = await request.json();
  const cell = await db.requireOwnedCell(user.workspaceId, rowId, columnId);
  const job = await queue.enqueueOnce({
    workspaceId: user.workspaceId, cellId: cell.id,
    revision: cell.configRevision,
  });
  return Response.json({ jobId: job.id }, { status: 202 });
}

// Long-lived worker; rung 6 supplies leases and checkpoint recovery.
await queue.consume(async job => {
  const config = await db.loadCellConfig(job);
  const evidence = await research(config.filledPrompt);
  const result = await judge(evidence.report, config.rubric);
  await db.saveCompletedCell(job, evidence, result);
});
