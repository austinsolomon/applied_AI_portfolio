// ILLUSTRATIVE - not run. Rung 6: a durable run per cell - claim, run the fixed steps, retry as data.
//
// Needs a Postgres database, a `sql` client, and loadCell/saveResearch/saveVerdictAndFinish/
// rescheduleJob/failCell. None of them exist in this repo. See docs/rung_status.md.

export async function claimNextCellJob() {
  const claimedJobs = await sql`
    update cell_jobs set claimed_at = now(), attempt_count = attempt_count + 1
    where job_id = (select job_id from cell_jobs
                    where run_after <= now() and (claimed_at is null or claimed_at < now() - interval '15 minutes')
                    order by job_id for update skip locked limit 1)
    returning *`;
  return claimedJobs[0]; // undefined when the queue is empty
}

export async function processCellJob(cellJob: CellJob) {
  const cell = await loadCell(cellJob.row_id, cellJob.column_id); // row data + column config, template filled
  try {
    // a finished step is never paid for twice
    const researchRun = cell.researchReport
      ? { researchReport: cell.researchReport }
      : await saveResearch(cell, await runResearch(cell.filledPrompt, cell.modelId));

    const qualificationVerdict = await judgeLead(researchRun.researchReport, cell.qualificationRubric);
    await saveVerdictAndFinish(cell, qualificationVerdict, cellJob.job_id);
  } catch (cellError) {
    if (isRateLimit(cellError)) await rescheduleJob(cellJob.job_id, '1 minute');
    else if (isBadInput(cellError)) await failCell(cell, cellJob.job_id, 'nothing to research'); // no retry
    else if (cellJob.attempt_count < 3) await rescheduleJob(cellJob.job_id, '30 seconds');
    else await failCell(cell, cellJob.job_id, String(cellError));
  }
}
