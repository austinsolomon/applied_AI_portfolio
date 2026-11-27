// Rung 3 - research, then judge.
//
// The loop in rung 2 produced a written report. A report is not a column in a table. This rung turns
// it into two fields a spreadsheet can hold: supportsNoOnlineOrdering (a boolean) and researchReason (one
// sentence), plus the URLs the verdict rests on.
//
// It is a second model call with no tools at all. That is the point: the expensive research is
// already stored, so changing the rubric re-runs only this call.
//
// `responses.parse` is the real SDK method doing the real work here: it sends the schema, then
// validates and fills `output_parsed` from the reply. We are not hand-parsing JSON.
//
// Run it:   npm run rung3

import { zodTextFormat } from 'openai/helpers/zod';
import * as zodSchema from 'zod';
import {
  loadAllCompanies,
  loadQualificationRubric,
  type CompanyId,
  type QualificationVerdict,
} from '../../shared/replay/index.ts';
import { createOfflineReplayClient } from './openaiClientFactory.ts';
import { runResearchLoop } from './rung2_agentLoop.ts';

/** The two verdict fields the table stores, plus the sources behind them. */
const qualificationVerdictSchema = zodSchema.object({
  supportsNoOnlineOrdering: zodSchema.boolean(),
  researchReason: zodSchema.string(),
  sourceUrls: zodSchema.array(zodSchema.string()),
});

export interface JudgeResult {
  companyId: CompanyId;
  verdict: QualificationVerdict;
  /** Always empty: the judge has no tools, on purpose. */
  toolCallsMade: [];
}

export async function runJudge(
  companyId: CompanyId,
  researchReport: string,
): Promise<JudgeResult> {
  const qualificationRubric = loadQualificationRubric();
  const { openaiClient, modelId } = createOfflineReplayClient('rung3_judge_verdict', companyId);

  const judgeResponse = await openaiClient.responses.parse({
    model: modelId,
    instructions:
      'Apply the rubric to the evidence. Explain the verdict in one sentence. ' +
      'If a rule is unproven, return false.',
    input: JSON.stringify({
      qualificationRubricText: qualificationRubric.qualificationRubricText,
      researchReport,
    }),
    text: { format: zodTextFormat(qualificationVerdictSchema, 'lead_verdict') },
    max_output_tokens: 600,
  });

  if (judgeResponse.status !== 'completed') {
    throw new Error(`The judge call did not complete: ${judgeResponse.status}`);
  }
  if (!judgeResponse.output_parsed) {
    throw new Error('The judge returned nothing the schema could parse.');
  }

  return { companyId, verdict: judgeResponse.output_parsed, toolCallsMade: [] };
}

/** The whole agent, end to end: rung 2 researches, rung 3 judges. */
export async function researchThenJudge(companyId: CompanyId): Promise<{
  researchReport: string;
  verdict: QualificationVerdict;
}> {
  const agentLoopResult = await runResearchLoop(companyId);
  const judgeResult = await runJudge(companyId, agentLoopResult.researchReport);
  return { researchReport: agentLoopResult.researchReport, verdict: judgeResult.verdict };
}

async function printEveryBakery(): Promise<void> {
  for (const company of loadAllCompanies()) {
    const { verdict } = await researchThenJudge(company.companyId);
    console.log(`\n=== ${company.companyName} (${company.companyId}) ===`);
    console.log(`supportsNoOnlineOrdering:         ${verdict.supportsNoOnlineOrdering}`);
    console.log(`researchReason: ${verdict.researchReason}`);
    console.log(`sourceUrls:          ${verdict.sourceUrls.join(', ')}`);
  }
  console.log('\nTwo fields a table can hold, and the sources behind them.\n');
}

if (import.meta.filename === process.argv[1]) {
  await printEveryBakery();
}
