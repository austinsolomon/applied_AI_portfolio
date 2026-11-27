// Rung 1 - one tool: fetch a page.
//
// The model still cannot open a page. It asks for fetch_url, this build's handler runs, and the
// harness calls the model again with the page text. Rungs 1 and 2 are one motion on this SDK: the
// second model call is made by the harness, not by you.

import { loadCompanyById, buildResearchQuestionForCompany } from '../../shared/replay/index.ts';
import type { AgentQueryFunction, AgentRunMode } from './agent-query-seam.ts';
import { liveAgentQuery, readAgentRunModeFromEnvironment } from './agent-query-seam.ts';
import { createReplayAgentQuery } from './replay-agent-query.ts';
import { createResearchToolSet } from './research-tools.ts';
import type { ExecutedToolCall } from './research-tools.ts';
import { RESEARCH_MODEL_ID } from './one-model-call.ts';

export interface SinglePageResearchOutcome {
  answerText: string;
  executedToolCalls: ExecutedToolCall[];
  costUsd: number;
  agentSessionId: string;
}

export async function researchFromOnePage(options: {
  companyId: string;
  runMode?: AgentRunMode;
}): Promise<SinglePageResearchOutcome> {
  const runMode = options.runMode ?? readAgentRunModeFromEnvironment();
  const company = loadCompanyById(options.companyId);
  const researchQuestion = buildResearchQuestionForCompany(company);

  const researchToolSet = createResearchToolSet({
    runMode,
    companyIdForReplay: options.companyId,
    includeSearchTool: false, // rung 1 has exactly one tool
  });

  const agentQuery: AgentQueryFunction =
    runMode === 'live'
      ? liveAgentQuery
      : createReplayAgentQuery({
          rungId: 'rung1_one_tool',
          companyId: options.companyId,
          replayableResearchTools: researchToolSet.replayableResearchTools,
        });

  for await (const message of agentQuery({
    prompt: researchQuestion,
    options: {
      model: RESEARCH_MODEL_ID,
      systemPrompt:
        'You research one bakery for a web studio that builds ordering websites. Open the pages ' +
        'you are given with fetch_url and report what they say, citing the URL for each fact.',
      tools: [], // no built-ins; the agent has exactly one tool
      mcpServers: { research: researchToolSet.researchToolServer },
      allowedTools: researchToolSet.allowedResearchToolNames, // runs without a permission prompt
      maxTurns: 3,
      settingSources: [],
    },
  })) {
    if (message.type === 'result') {
      if (message.subtype !== 'success') {
        throw new Error(`rung 1 stopped: ${message.subtype}`);
      }
      return {
        answerText: message.result ?? '',
        executedToolCalls: researchToolSet.executedToolCalls,
        costUsd: message.total_cost_usd ?? 0,
        agentSessionId: message.session_id ?? '',
      };
    }
  }
  throw new Error('the message stream ended without a result');
}
