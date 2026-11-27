// Run rung 0 over all four fixture bakeries and print what came back.
//   npm run rung0

import { listCompanyIdsWithExpectedVerdicts } from '../../shared/replay/index.ts';
import { readAgentRunModeFromEnvironment } from '../worker/agent-query-seam.ts';
import { askOneQuestionWithNoTools } from '../worker/one-model-call.ts';

const runMode = readAgentRunModeFromEnvironment();
console.log(`Rung 0 - one model call, no tools. Mode: ${runMode}\n`);

for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
  const oneModelCallOutcome = await askOneQuestionWithNoTools({ companyId, runMode });
  console.log(`--- ${companyId}`);
  console.log(oneModelCallOutcome.answerText.trim());
  console.log(`(cost so far: $${oneModelCallOutcome.costUsd.toFixed(4)})\n`);
}

console.log('With no tools the model can only say what it cannot know. That is rung 0 working.');
