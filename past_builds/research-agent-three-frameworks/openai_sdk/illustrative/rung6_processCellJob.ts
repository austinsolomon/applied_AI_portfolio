// ILLUSTRATIVE - not run
//
// Rung 6, "A durable run per cell". What makes a worker crash-safe is not the model client: it is
// the lease (only one worker owns this job) and the checkpoint (finished research is not redone).
// `db` and `withHeartbeat` are your adapters.

// Application pseudocode: db methods enforce leases atomically.
const job = await db.claimReadyJob({ leaseSeconds: 120 });
if (job) {
  // withHeartbeat renews the lease until the callback finishes.
  await withHeartbeat(job, async () => {
    try {
      let evidence = job.researchCheckpoint;
      if (!evidence) {
        evidence = await research(job.prompt);
        await db.checkpointResearch(job.id, job.leaseToken, evidence);
      }
      const result = await judge(evidence.report, job.rubric);
      // One transaction: verify lease + revision, save cell, finish job.
      await db.completeIfCurrent(job, evidence, result);
    } catch (error) {
      await db.retryOrFailIfLeaseOwned(job, error, { maxAttempts: 3 });
    }
  });
}
