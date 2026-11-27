// Rung 0 — one model call. Run it with: npm run rung0
//
// Send text to a model, read the reply. Every later rung is this call with something added.
// The model here has no tools, so it can only answer from what it absorbed in training. Ask it
// "does this bakery already take online orders?" about a small Chicago bakery and that is nothing:
// the honest recorded answer says so and cites no page. That is the whole point of this rung: it
// shows why the others exist.

import { generateText } from 'ai';

import { RESEARCH_MODEL_ID, selectLanguageModelForRung } from '../lib/modelSelection.ts';
import { announceSelectedMode } from '../lib/liveModeSwitch.ts';
import { isBeingRunDirectly } from '../lib/isBeingRunDirectly.ts';
import {
  buildResearchQuestionForCompany,
  loadAllCompanies,
  type CompanyRecord,
} from '../../shared/replay/index.ts';

export async function askTheModelWithNoTools(company: CompanyRecord): Promise<string> {
  const { languageModel } = selectLanguageModelForRung(
    'rung0_one_model_call',
    company.companyId,
    RESEARCH_MODEL_ID,
  );

  const { text: replyText, usage: tokenUsage } = await generateText({
    model: languageModel,
    prompt: buildResearchQuestionForCompany(company),
  });

  // usage comes back on every call: tokens in and out. Stored per row, that is the cost of the row.
  void tokenUsage;
  return replyText;
}

async function runRungZeroForEveryCompany(): Promise<void> {
  announceSelectedMode('Rung 0 (one model call)');
  for (const company of loadAllCompanies()) {
    const replyText = await askTheModelWithNoTools(company);
    console.log(`\n--- ${company.companyName} ---`);
    console.log(replyText);
  }
  console.log('\nNo tools, so no sources. Rung 1 gives the model one tool.');
}

if (isBeingRunDirectly(import.meta.url)) {
  await runRungZeroForEveryCompany();
}
