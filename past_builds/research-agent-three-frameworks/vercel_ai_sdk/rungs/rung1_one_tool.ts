// Rung 1 — one tool: fetch a page. Run it with: npm run rung1
//
// The model still cannot open a link. What it CAN do is say "please call fetchPageText with this
// URL". The AI SDK runs the tool's execute function, hands the page text back, and the model
// answers from it. Now every fact has a URL behind it.
//
// Watch Rye & Rosemary Bakery: its home page has an Order Ahead button, so an agent that reads
// only that one page decides the bakery already takes online orders. It does not. One page is not
// enough, and rung 2 is what finds the page behind the button.
//
// Two steps, on purpose. Step 1 is the model asking for the page; step 2 is the model reading it
// and answering. (The artifact page shows only step 1, to make the point that a plain generateText
// call stops as soon as a tool has run: its `text` is empty and nothing has been read yet.)

import { generateText, isStepCount } from 'ai';

import { fetchPageTextTool } from '../lib/tools/fetchPageTextTool.ts';
import { RESEARCH_MODEL_ID, selectLanguageModelForRung } from '../lib/modelSelection.ts';
import { announceSelectedMode } from '../lib/liveModeSwitch.ts';
import { isBeingRunDirectly } from '../lib/isBeingRunDirectly.ts';
import {
  buildResearchQuestionForCompany,
  loadAllCompanies,
  type CompanyRecord,
} from '../../shared/replay/index.ts';

/** At most two model calls: ask for the page, then answer from it. */
export const MAX_RUNG_ONE_STEPS = 2;

export interface RungOneResult {
  answerText: string;
  toolCallsMade: { toolName: string; toolArguments: unknown }[];
}

export async function readOnePageThenAnswer(company: CompanyRecord): Promise<RungOneResult> {
  const { languageModel } = selectLanguageModelForRung(
    'rung1_one_tool',
    company.companyId,
    RESEARCH_MODEL_ID,
  );

  const toolCallResult = await generateText({
    model: languageModel,
    tools: { fetchPageText: fetchPageTextTool },
    stopWhen: isStepCount(MAX_RUNG_ONE_STEPS),
    prompt: buildResearchQuestionForCompany(company),
  });

  return {
    answerText: toolCallResult.text,
    toolCallsMade: toolCallResult.steps.flatMap((completedStep) =>
      completedStep.toolCalls.map((toolCall) => ({
        toolName: toolCall.toolName,
        toolArguments: toolCall.input,
      })),
    ),
  };
}

async function runRungOneForEveryCompany(): Promise<void> {
  announceSelectedMode('Rung 1 (one tool)');
  for (const company of loadAllCompanies()) {
    const rungOneResult = await readOnePageThenAnswer(company);
    console.log(`\n--- ${company.companyName} ---`);
    console.log('tool calls:', JSON.stringify(rungOneResult.toolCallsMade));
    console.log(rungOneResult.answerText);
  }
  console.log('\nOne page each. Rung 2 lets the model keep going until it has enough.');
}

if (isBeingRunDirectly(import.meta.url)) {
  await runRungOneForEveryCompany();
}
