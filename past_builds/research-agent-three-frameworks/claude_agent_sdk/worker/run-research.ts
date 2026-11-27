// Rung 2 - the agent loop: search, read, repeat.
//
// This is the research agent's core, and it is the part the SDK already is. You do not build the
// loop; you set its limits and collect its trace.
//
// One mode difference, stated plainly: WebSearch is a BUILT-IN tool, which means it runs inside the
// `claude` process. Offline there is no such process, so offline mode allows a custom search_web
// tool of this build's own (see research-tools.ts) that reads the recorded search results. Live
// mode allows the built-in, exactly as the artifact page shows.

import { loadCompanyById, buildResearchQuestionForCompany } from '../../shared/replay/index.ts';
import type { CompanyRecord } from '../../shared/replay/index.ts';
import type { AgentQueryFunction, AgentRunMode } from './agent-query-seam.ts';
import { liveAgentQuery, readAgentRunModeFromEnvironment } from './agent-query-seam.ts';
import { createReplayAgentQuery } from './replay-agent-query.ts';
import { createResearchToolSet } from './research-tools.ts';
import type { ExecutedToolCall } from './research-tools.ts';
import { RESEARCH_MODEL_ID } from './one-model-call.ts';

export const BUILT_IN_WEB_SEARCH_TOOL_NAME = 'WebSearch';
export const MAX_RESEARCH_TURNS = 12;
export const MAX_RESEARCH_BUDGET_USD = 0.25;

export class ResearchStopped extends Error {
  // Plain fields, not TypeScript "parameter properties": Node runs these files by stripping types,
  // and stripping cannot rewrite `constructor(public stopReason: string)` into an assignment.
  readonly stopReason: string;
  readonly agentSessionId: string;

  constructor(stopReason: string, agentSessionId: string) {
    super(`research stopped: ${stopReason}`);
    this.name = 'ResearchStopped';
    this.stopReason = stopReason;
    this.agentSessionId = agentSessionId;
  }
}

export interface ResearchRunOutcome {
  researchReport: string;
  agentStepTrace: unknown[];
  executedToolCalls: ExecutedToolCall[];
  costUsd: number;
  agentSessionId: string;
}

export async function runResearch(options: {
  companyId: string;
  runMode?: AgentRunMode;
  company?: CompanyRecord;
  queryFunction?: AgentQueryFunction;
}): Promise<ResearchRunOutcome> {
  const runMode = options.runMode ?? readAgentRunModeFromEnvironment();
  const company = options.company ?? loadCompanyById(options.companyId);
  const filledPrompt = buildResearchQuestionForCompany(company);

  const researchToolSet = createResearchToolSet({
    runMode,
    companyIdForReplay: options.companyId,
    includeSearchTool: runMode === 'offline',
  });

  const builtInToolNames = runMode === 'live' ? [BUILT_IN_WEB_SEARCH_TOOL_NAME] : [];
  const allowedToolNames = [...builtInToolNames, ...researchToolSet.allowedResearchToolNames];

  const agentQuery: AgentQueryFunction =
    options.queryFunction ?? (runMode === 'live'
      ? liveAgentQuery
      : createReplayAgentQuery({
          rungId: 'rung2_agent_loop',
          companyId: options.companyId,
          replayableResearchTools: researchToolSet.replayableResearchTools,
        }));

  const agentStepTrace: unknown[] = [];

  for await (const message of agentQuery({
    prompt: filledPrompt,
    options: {
      model: RESEARCH_MODEL_ID,
      systemPrompt:
        'You research one bakery for a web studio that builds ordering websites. Search to find ' +
        'pages and fetch_url to read them. Report the facts you found and the URL each one came ' +
        'from. Do not judge the lead.',
      tools: builtInToolNames, // the only built-in kept, and only in live mode
      mcpServers: { research: researchToolSet.researchToolServer },
      allowedTools: allowedToolNames,
      maxTurns: MAX_RESEARCH_TURNS, // ceiling one: tool round trips
      maxBudgetUsd: MAX_RESEARCH_BUDGET_USD, // ceiling two: dollars for this cell
      settingSources: [],
    },
  })) {
    if (message.type === 'assistant' || message.type === 'user') agentStepTrace.push(message.message);
    if (message.type === 'result') {
      if (message.subtype !== 'success') {
        throw new ResearchStopped(message.subtype ?? 'unknown', message.session_id ?? '');
      }
      return {
        researchReport: message.result ?? '',
        agentStepTrace,
        executedToolCalls: researchToolSet.executedToolCalls,
        costUsd: message.total_cost_usd ?? 0,
        agentSessionId: message.session_id ?? '',
      };
    }
  }
  throw new Error('the message stream ended without a result');
}
