// Rung 3: turn a paragraph of research into a typed verdict.
//
// Output.object({ schema }) asks the provider for an object of a given shape and then validates the
// reply against that shape. A mismatch is an error you can retry, not a broken cell.
//
// Two calls, not one. The agent in rung 2 gathers facts; this second call has NO tools and only
// applies the rubric. Change the rubric and you re-run only this cheap call — the expensive web
// research is already stored.
//
// .describe() on each field is prompt text: it is where "one sentence" and "false if unsure" go.

import { generateText, Output } from 'ai';
import * as zodSchema from 'zod';

import { JUDGE_MODEL_ID, selectLanguageModelForRung } from './modelSelection.ts';
import {
  loadQualificationRubric,
  type CompanyId,
  type QualificationVerdict,
  type ReplayModelSession,
} from '../../shared/replay/index.ts';

export const qualificationVerdictSchema = zodSchema.object({
  supportsNoOnlineOrdering: zodSchema
    .boolean()
    .describe('true only if every rule in the rubric is proven; false if unsure'),
  researchReason: zodSchema
    .string()
    .describe('one sentence, naming the facts that decided it'),
  sourceUrls: zodSchema
    .array(zodSchema.string())
    .describe('every page the verdict rests on'),
});

export interface JudgeResult {
  qualificationVerdict: QualificationVerdict;
  replaySession?: ReplayModelSession;
}

export async function judgeResearchReport(
  companyId: CompanyId,
  researchReport: string,
): Promise<JudgeResult> {
  const { languageModel, replaySession } = selectLanguageModelForRung(
    'rung3_judge_verdict',
    companyId,
    JUDGE_MODEL_ID,
  );

  // The rubric is data, not code: a learner can edit shared/fixtures/qualification_rubric.json and
  // re-run this rung alone.
  const qualificationRubricText = loadQualificationRubric().qualificationRubricText;

  const judgeRun = await generateText({
    model: languageModel,
    output: Output.object({ schema: qualificationVerdictSchema }),
    prompt:
      `RUBRIC:\n${qualificationRubricText}\n\n` +
      `RESEARCH:\n${researchReport}\n\n` +
      'Apply the rubric to the research.',
  });

  return { qualificationVerdict: judgeRun.output, replaySession };
}
