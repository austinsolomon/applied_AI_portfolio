// Run rung 2 over all four fixture bakeries and print the trace and the report.
//   npm run rung2

import { listCompanyIdsWithExpectedVerdicts } from '../../shared/replay/index.ts';
import { readAgentRunModeFromEnvironment } from '../worker/agent-query-seam.ts';
import { runResearch } from '../worker/run-research.ts';

const runMode = readAgentRunModeFromEnvironment();
console.log(`Rung 2 - the agent loop: search, read, repeat. Mode: ${runMode}\n`);

for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
  const researchRun = await runResearch({ companyId, runMode });
  console.log(`--- ${companyId}`);
  for (const executedToolCall of researchRun.executedToolCalls) {
    console.log(`  tool run: ${executedToolCall.mcpToolName} ${JSON.stringify(executedToolCall.toolArguments)}`);
  }
  console.log(`  model turns kept in the trace: ${researchRun.agentStepTrace.length}`);
  console.log(researchRun.researchReport.trim().split('\n').slice(0, 6).join('\n'));
  console.log('  ...\n');
}

console.log('Nobody wrote that sequence down in advance. The model chose each step; the harness walked it.');
