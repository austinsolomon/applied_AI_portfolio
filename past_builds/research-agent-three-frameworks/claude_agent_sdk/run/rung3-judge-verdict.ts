// Run rungs 2 and 3 back to back over all four fixture bakeries: research, then judge.
//   npm run rung3

import { listCompanyIdsWithExpectedVerdicts } from '../../shared/replay/index.ts';
import { readAgentRunModeFromEnvironment } from '../worker/agent-query-seam.ts';
import { runResearch } from '../worker/run-research.ts';
import { judgeLead } from '../worker/judge-lead.ts';

const runMode = readAgentRunModeFromEnvironment();
console.log(`Rung 3 - schema-enforced output: research, then judge. Mode: ${runMode}\n`);

for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
  const researchRun = await runResearch({ companyId, runMode });
  const qualificationVerdict = await judgeLead({
    companyId,
    researchReport: researchRun.researchReport,
    runMode,
  });
  console.log(`--- ${companyId}`);
  console.log(`  supportsNoOnlineOrdering:         ${qualificationVerdict.supportsNoOnlineOrdering}`);
  console.log(`  researchReason: ${qualificationVerdict.researchReason}`);
  console.log(`  sourceUrls:          ${qualificationVerdict.sourceUrls.join(', ')}\n`);
}

console.log('The verdict arrives as fields, not as a paragraph. That is what makes it a column.');
