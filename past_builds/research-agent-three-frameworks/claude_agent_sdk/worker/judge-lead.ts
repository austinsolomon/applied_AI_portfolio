// Rung 3 - schema-enforced output: research, then judge.
//
// Two calls, not one. The agent in rung 2 returns facts. This call, with no tools, applies the
// rubric and returns a typed object. Change the rubric and you re-run only this call; the expensive
// research is already stored.

import { loadQualificationRubric } from '../../shared/replay/index.ts';
import type { QualificationVerdict } from '../../shared/replay/index.ts';
import * as zodSchema from 'zod';

import type { AgentQueryFunction, AgentRunMode } from './agent-query-seam.ts';
import { liveAgentQuery, readAgentRunModeFromEnvironment } from './agent-query-seam.ts';
import { createReplayAgentQuery } from './replay-agent-query.ts';

/** A small model: no tools, short input. */
export const JUDGE_MODEL_ID = 'claude-haiku-4-5';

export const qualificationSchema = zodSchema.z.object({
  supportsNoOnlineOrdering: zodSchema.z
    .boolean()
    .describe('true only if the evidence supports no online ordering; false if contradicted or unsure'),
  researchReason: zodSchema.z
    .string()
    .describe('one sentence, citing the fact that decided it'),
  sourceUrls: zodSchema.z
    .array(zodSchema.z.string())
    .describe('every page the verdict rests on'),
});

export const qualificationJsonSchema = zodSchema.z.toJSONSchema(qualificationSchema, {
  target: 'draft-7',
});

export interface JudgeOutcome extends QualificationVerdict {
  judgeCostUsd: number;
}

export async function judgeLead(options: {
  companyId: string;
  researchReport: string;
  qualificationRubricText?: string;
  runMode?: AgentRunMode;
}): Promise<JudgeOutcome> {
  const runMode = options.runMode ?? readAgentRunModeFromEnvironment();
  const qualificationRubricText =
    options.qualificationRubricText ?? loadQualificationRubric().qualificationRubricText;

  const agentQuery: AgentQueryFunction =
    runMode === 'live'
      ? liveAgentQuery
      : createReplayAgentQuery({
          rungId: 'rung3_judge_verdict',
          companyId: options.companyId,
          replayableResearchTools: [],
        });

  for await (const message of agentQuery({
    prompt:
      `RUBRIC:\n${qualificationRubricText}\n\nRESEARCH:\n${options.researchReport}\n\n` +
      'Apply the rubric to the research.',
    options: {
      model: JUDGE_MODEL_ID,
      maxTurns: 3,
      maxBudgetUsd: 0.10,
      tools: [],
      outputFormat: { type: 'json_schema', schema: qualificationJsonSchema },
      settingSources: [],
    },
  })) {
    if (message.type === 'result' && message.subtype === 'success' && message.structured_output) {
      const qualificationVerdict = qualificationSchema.parse(message.structured_output);
      return { ...qualificationVerdict, judgeCostUsd: message.total_cost_usd ?? 0 };
    }
    if (message.type === 'result') {
      throw new Error(`judge failed: ${message.subtype}`);
    }
  }
  throw new Error('the message stream ended without a result');
}
