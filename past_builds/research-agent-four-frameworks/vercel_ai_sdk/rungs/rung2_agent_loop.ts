// Rung 2 — the agent loop. Run it with: npm run rung2
//
// Search, read, repeat. The harness (ToolLoopAgent, in lib/researchAgent.ts) calls the model, runs
// whatever tools it asked for, and calls it again with the results, until the model answers in
// plain text. For Rye & Rosemary Bakery that means reading the page behind the Order Ahead button
// (phone and an emailed form, so no online ordering). For Dos Rios Panaderia it means one extra
// turn, to read a stale outside delivery listing the narrow search turned up.
//
// This is the core of the whole build: everything before it is a single model call, everything
// after it is plumbing around this loop.

import { runResearchForCompany } from '../lib/researchAgent.ts';
import { announceSelectedMode } from '../lib/liveModeSwitch.ts';
import { isBeingRunDirectly } from '../lib/isBeingRunDirectly.ts';
import { loadAllCompanies } from '../../shared/replay/index.ts';

async function runRungTwoForEveryCompany(): Promise<void> {
  announceSelectedMode('Rung 2 (agent loop)');
  for (const company of loadAllCompanies()) {
    const researchRun = await runResearchForCompany(company);
    console.log(`\n--- ${company.companyName} ---`);
    console.log(`tool calls in order: ${researchRun.toolCallsMade.map((toolCall) => toolCall.toolName).join(' -> ')}`);
    console.log(researchRun.researchReport);
  }
  console.log('\nFacts, with sources, and no verdict yet. Rung 3 applies the rubric.');
}

if (isBeingRunDirectly(import.meta.url)) {
  await runRungTwoForEveryCompany();
}
