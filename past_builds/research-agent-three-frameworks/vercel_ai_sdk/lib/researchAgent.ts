// Rung 2: the agent loop.
//
// ToolLoopAgent is the AI SDK's ready-made harness. It owns the message list: it calls the model,
// runs whatever tools the model asked for, appends the results, and calls the model again — until
// the model answers in plain text instead of asking for a tool, or the stop rule is hit.
//
// The model decides WHICH tool and HOW MANY passes. Your code only executes. That decision is the
// whole difference between an agent (this rung) and a workflow (rung 6), where the order is fixed
// in code.
//
// The instructions deliberately say "report the facts, do not judge". Judging moves to rung 3 so
// the rubric can change without paying for the web research again.

import { ToolLoopAgent, isStepCount } from 'ai';

import { fetchPageTextTool } from './tools/fetchPageTextTool.ts';
import { createSearchTheWebTool } from './tools/searchTheWebTool.ts';
import { RESEARCH_MODEL_ID, selectLanguageModelForRung } from './modelSelection.ts';
import {
  buildResearchQuestionForCompany,
  type CompanyRecord,
  type ReplayModelSession,
} from '../../shared/replay/index.ts';

export const RESEARCH_AGENT_INSTRUCTIONS =
  'You research one bakery for a web studio that builds ordering websites. Use searchTheWeb to ' +
  'find pages and fetchPageText to read them. Report the facts you found and the URL each one ' +
  'came from. Do not judge the lead.';

/** At most this many model calls for one bakery. Every harness has this ceiling under some name. */
export const MAX_RESEARCH_STEPS = 12;

export interface ResearchRunResult {
  researchReport: string;
  /** The trace: every tool call and every tool result, in order. This is how a wrong cell is debugged. */
  agentStepTrace: unknown[];
  toolCallsMade: { toolName: string; toolArguments: unknown }[];
  replaySession?: ReplayModelSession;
}

export async function runResearchForCompany(company: CompanyRecord): Promise<ResearchRunResult> {
  const { languageModel, replaySession } = selectLanguageModelForRung(
    'rung2_agent_loop',
    company.companyId,
    RESEARCH_MODEL_ID,
  );

  const researchAgent = new ToolLoopAgent({
    model: languageModel,
    instructions: RESEARCH_AGENT_INSTRUCTIONS,
    tools: {
      searchTheWeb: createSearchTheWebTool(company.companyId),
      fetchPageText: fetchPageTextTool,
    },
    stopWhen: isStepCount(MAX_RESEARCH_STEPS),
  });

  const agentRun = await researchAgent.generate({
    prompt: buildResearchQuestionForCompany(company),
  });

  return {
    researchReport: agentRun.text,
    agentStepTrace: agentRun.steps,
    toolCallsMade: agentRun.steps.flatMap((completedStep) =>
      completedStep.toolCalls.map((toolCall) => ({
        toolName: toolCall.toolName,
        toolArguments: toolCall.input,
      })),
    ),
    replaySession,
  };
}
