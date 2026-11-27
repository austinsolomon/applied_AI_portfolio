// Rung 0 - one model call, no tools.
//
// The smallest thing this SDK does: send a question, read the reply. The question is the one the
// whole repo asks about each of the four fixture bakeries: "Does this bakery already take online
// orders?" The point of this rung is what the answer looks like when the model has no way to look
// anything up. It says, honestly, that it cannot answer and cites nothing. Every later rung exists
// because of this answer.
//
// Run it:   npm run rung0

import {
  buildResearchQuestionForCompany,
  loadAllCompanies,
  loadCompanyById,
  type CompanyId,
} from '../../shared/replay/index.ts';
import { createOfflineReplayClient } from './openaiClientFactory.ts';

export interface OneModelCallResult {
  companyId: CompanyId;
  researchQuestion: string;
  answerText: string;
  /** Always empty at this rung: with no tools there is nothing to cite. */
  toolCallsMade: [];
}

export async function runOneModelCall(companyId: CompanyId): Promise<OneModelCallResult> {
  const company = loadCompanyById(companyId);
  const researchQuestion = buildResearchQuestionForCompany(company);

  const { openaiClient, modelId } = createOfflineReplayClient('rung0_one_model_call', companyId);

  const response = await openaiClient.responses.create({
    model: modelId,
    instructions: 'You answer questions for a web designer who builds online-ordering websites for local ' +
      'bakeries. Be brief and say what you do not know.',
    input: researchQuestion,
    max_output_tokens: 400,
  });

  if (response.status !== 'completed') {
    throw new Error(`The model call did not complete: ${response.status}`);
  }

  return {
    companyId,
    researchQuestion,
    answerText: response.output_text,
    toolCallsMade: [],
  };
}

async function printEveryBakery(): Promise<void> {
  for (const company of loadAllCompanies()) {
    const oneModelCallResult = await runOneModelCall(company.companyId);
    console.log(`\n=== ${company.companyName} (${company.companyId}) ===`);
    console.log(`Question: ${oneModelCallResult.researchQuestion}`);
    console.log(`Answer:   ${oneModelCallResult.answerText}`);
  }
  console.log('\nNo tools, no sources. That is the lesson of rung 0.\n');
}

if (import.meta.filename === process.argv[1]) {
  await printEveryBakery();
}
