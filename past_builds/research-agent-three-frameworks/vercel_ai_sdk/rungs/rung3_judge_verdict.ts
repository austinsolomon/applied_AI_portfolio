// Rung 3 — research, then judge. Run it with: npm run rung3
//
// Rung 2 produced a paragraph of facts. This rung turns it into two fields a spreadsheet can hold:
// supportsNoOnlineOrdering (true or false) and researchReason (one sentence), plus the pages the verdict
// rests on. It is a second model call with no tools, and it is cheap, so the rubric can change
// without paying for the web research again.
//
// Watch Dos Rios Panaderia: its own pages never say how orders are taken, so rule 2 ("no online
// ordering today") is unproven, and the rubric's "false when unproven" rule makes the answer
// false. That is the rung doing its job.

import { runResearchForCompany } from '../lib/researchAgent.ts';
import { judgeResearchReport } from '../lib/judgeLead.ts';
import { announceSelectedMode } from '../lib/liveModeSwitch.ts';
import { isBeingRunDirectly } from '../lib/isBeingRunDirectly.ts';
import { loadAllCompanies, type QualificationVerdict } from '../../shared/replay/index.ts';

export interface ResearchThenJudgeResult {
  researchReport: string;
  qualificationVerdict: QualificationVerdict;
  toolCallsMade: { toolName: string; toolArguments: unknown }[];
}

/** The whole task, end to end, for one bakery: research with tools, then judge without them. */
export async function researchThenJudgeCompany(companyId: string): Promise<ResearchThenJudgeResult> {
  const company = loadAllCompanies().find(
    (candidateCompany) => candidateCompany.companyId === companyId,
  );
  if (company === undefined) {
    throw new Error(`No fixture company with id "${companyId}".`);
  }

  const researchRun = await runResearchForCompany(company);
  const judgeRun = await judgeResearchReport(company.companyId, researchRun.researchReport);

  return {
    researchReport: researchRun.researchReport,
    qualificationVerdict: judgeRun.qualificationVerdict,
    toolCallsMade: researchRun.toolCallsMade,
  };
}

async function runRungThreeForEveryCompany(): Promise<void> {
  announceSelectedMode('Rung 3 (research, then judge)');
  for (const company of loadAllCompanies()) {
    const { qualificationVerdict } = await researchThenJudgeCompany(company.companyId);
    console.log(`\n--- ${company.companyName} ---`);
    console.log(`supportsNoOnlineOrdering: ${qualificationVerdict.supportsNoOnlineOrdering}`);
    console.log(`researchReason: ${qualificationVerdict.researchReason}`);
    console.log(`sourceUrls: ${qualificationVerdict.sourceUrls.join(', ')}`);
  }
  console.log('\nThat is the finished task. Rungs 4 to 10 are about running it for a whole list.');
}

if (isBeingRunDirectly(import.meta.url)) {
  await runRungThreeForEveryCompany();
}
