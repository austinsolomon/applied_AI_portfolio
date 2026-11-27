// Run rung 1 over all four fixture bakeries and print what came back.
//   npm run rung1

import { listCompanyIdsWithExpectedVerdicts } from '../../shared/replay/index.ts';
import { readAgentRunModeFromEnvironment } from '../worker/agent-query-seam.ts';
import { researchFromOnePage } from '../worker/run-single-page-research.ts';

const runMode = readAgentRunModeFromEnvironment();
console.log(`Rung 1 - one tool: fetch a page. Mode: ${runMode}\n`);

for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
  const singlePageResearch = await researchFromOnePage({ companyId, runMode });
  console.log(`--- ${companyId}`);
  for (const executedToolCall of singlePageResearch.executedToolCalls) {
    console.log(`  tool run: ${executedToolCall.mcpToolName} ${JSON.stringify(executedToolCall.toolArguments)}`);
  }
  console.log(singlePageResearch.answerText.trim());
  console.log('');
}

console.log('One tool, so one page. The model asked; this build\'s handler opened it.');
console.log('Rye & Rosemary Bakery needs its order page too. One page cannot see it; rung 2 can.');
