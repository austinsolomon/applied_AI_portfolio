// Rung 0 - one model call, no tools.
//
// The atomic unit. One difference from a plain API client: you start inside a harness. query()
// always runs the agent loop; rung 0 is that loop held to one turn with no tools.

import { loadCompanyById, buildResearchQuestionForCompany } from '../../shared/replay/index.ts';
import type { AgentQueryFunction, AgentRunMode } from './agent-query-seam.ts';
import { liveAgentQuery, readAgentRunModeFromEnvironment } from './agent-query-seam.ts';
import { createReplayAgentQuery } from './replay-agent-query.ts';

export const RESEARCH_MODEL_ID = 'claude-sonnet-5';

export interface OneModelCallOutcome {
  answerText: string;
  costUsd: number;
  agentSessionId: string;
  modelTurnCount: number;
}

export async function askOneQuestionWithNoTools(options: {
  companyId: string;
  runMode?: AgentRunMode;
}): Promise<OneModelCallOutcome> {
  const runMode = options.runMode ?? readAgentRunModeFromEnvironment();
  const company = loadCompanyById(options.companyId);
  const researchQuestion = buildResearchQuestionForCompany(company);

  const agentQuery: AgentQueryFunction =
    runMode === 'live'
      ? liveAgentQuery
      : createReplayAgentQuery({
          rungId: 'rung0_one_model_call',
          companyId: options.companyId,
          replayableResearchTools: [],
        });

  for await (const message of agentQuery({
    prompt: researchQuestion,
    options: {
      model: RESEARCH_MODEL_ID,
      systemPrompt:
        'You answer questions for a one-person web studio that builds ordering websites for ' +
        'bakeries. Be brief.',
      tools: [], // no built-in tools at all
      maxTurns: 1, // one model call
      settingSources: [], // ignore CLAUDE.md and settings files on this machine
    },
  })) {
    if (message.type === 'result') {
      if (message.subtype !== 'success') {
        throw new Error(`rung 0 stopped: ${message.subtype}`);
      }
      return {
        answerText: message.result ?? '',
        costUsd: message.total_cost_usd ?? 0,
        agentSessionId: message.session_id ?? '',
        modelTurnCount: message.num_turns ?? 0,
      };
    }
  }
  throw new Error('the message stream ended without a result');
}
