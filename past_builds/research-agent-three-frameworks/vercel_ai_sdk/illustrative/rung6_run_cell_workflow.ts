// ILLUSTRATIVE - not run
//
// Rung 6 — a durable run per cell, using Vercel's Workflow SDK (the `workflow` package).
//
// Why it is not run here: the 'use workflow' and 'use step' directives are compiled and hosted by
// Vercel's Workflow runtime. Outside that runtime they are ordinary strings and nothing is durable,
// so running this file would teach the opposite of what it says.
//
// The lesson: a workflow is a FIXED sequence written in code — load, research, judge, save. The
// model chooses nothing about the order. Compare rung 2, where the model chooses everything. Each
// 'use step' function has its result recorded, so a crash, a timeout or a deploy halfway through
// resumes from the last finished step instead of starting over (and paying again).

import { FatalError, RetryableError } from 'workflow';

export async function runCell(rowId: number, columnId: number) {
  'use workflow'; // durable: survives crashes, timeouts, deploys

  const cellJob = await loadCell(rowId, columnId); // row data + column config, template filled
  const researchResult = await researchStep(cellJob.filledPrompt, cellJob.modelId);
  const qualificationVerdict = await judgeStep(
    researchResult.researchReport,
    cellJob.qualificationRubric,
  );
  await saveCell(rowId, columnId, researchResult, qualificationVerdict);
}

async function researchStep(filledPrompt: string, modelId: string) {
  'use step'; // result recorded; retried 3x on a throw
  try {
    const agentRun = await buildResearchAgent(modelId).generate({ prompt: filledPrompt });
    return {
      researchReport: agentRun.text,
      agentStepTrace: agentRun.steps,
      tokensUsed: agentRun.usage.totalTokens,
    };
  } catch (researchError) {
    if (isRateLimit(researchError)) throw new RetryableError('rate limited', { retryAfter: '1m' });
    if (isBadInput(researchError)) throw new FatalError('nothing to research'); // no retry
    throw researchError; // anything else: default retries
  }
}

// app/api/run-cell/route.ts, once rung 6 exists: queue the run and answer immediately.
import { start } from 'workflow/api';

export async function POST(request: Request) {
  const { rowId, columnId } = await request.json();
  await start(runCell, [rowId, columnId]);
  return Response.json({ runStatus: 'started' });
}
