// Offline mode: a stand-in for query() with the same call shape.
//
// It reads one recorded conversation (one rung, one bakery) and yields SDK-shaped messages:
// an 'assistant' message per recorded turn, a 'user' message per tool result, and a final 'result'
// message. Between the two it RUNS THE TOOLS FOR REAL - the same handler bodies live mode uses -
// against the shared recordings.
//
// What this does exercise: prompt assembly, tool wiring, the tool handlers, the message-reading
// code, schema validation, the judge.
// What it does not exercise: the SDK itself, the stdio boundary, or a real `claude` binary.
// That is a weaker offline guarantee than the other two builds in this repo have.

import { openReplayModelSession } from '../../shared/replay/index.ts';
import type { RungId } from '../../shared/replay/index.ts';
import type { AgentQueryFunction, AgentSdkMessage } from './agent-query-seam.ts';
import { RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME } from './research-tools.ts';
import type { ReplayableResearchTool } from './research-tools.ts';

export interface ReplayAgentQuerySettings {
  rungId: RungId;
  companyId: string;
  /** Empty for rungs 0 and 3, which request no tools. */
  replayableResearchTools: ReplayableResearchTool[];
}

/** Every cost figure offline is zero, because nothing was bought. */
const OFFLINE_COST_USD = 0;

export function createReplayAgentQuery(settings: ReplayAgentQuerySettings): AgentQueryFunction {
  return function replayAgentQuery(request) {
    return replayRecordedSession(settings, request.options.outputFormat !== undefined);
  };
}

async function* replayRecordedSession(
  settings: ReplayAgentQuerySettings,
  callerAskedForStructuredOutput: boolean,
): AsyncGenerator<AgentSdkMessage> {
  const replaySession = openReplayModelSession(settings.rungId, settings.companyId);
  const offlineSessionId = `offline-replay:${replaySession.conversationId}`;
  let modelTurnCount = 0;

  yield {
    type: 'system',
    subtype: 'init',
    session_id: offlineSessionId,
  };

  while (!replaySession.isFinished()) {
    const recordedTurn = replaySession.takeNextTurn();
    modelTurnCount += 1;

    yield {
      type: 'assistant',
      session_id: offlineSessionId,
      message: {
        role: 'assistant',
        content: [
          { type: 'text', text: recordedTurn.assistantText },
          ...recordedTurn.toolCallsRequested.map((recordedToolCall) => ({
            type: 'tool_use',
            id: recordedToolCall.toolCallId,
            name: toMcpToolName(recordedToolCall.toolName),
            input: recordedToolCall.toolArguments,
          })),
        ],
      },
    };

    for (const recordedToolCall of recordedTurn.toolCallsRequested) {
      const mcpToolName = toMcpToolName(recordedToolCall.toolName);
      const matchingTool = settings.replayableResearchTools.find(
        (replayableTool) => replayableTool.mcpToolName === mcpToolName,
      );
      if (!matchingTool) {
        throw new Error(
          `The recording asks for ${mcpToolName}, which this rung did not wire up. ` +
            'Either the rung is missing a tool or the wrong recording was opened.',
        );
      }
      const toolResultText = await matchingTool.runToolCall(recordedToolCall.toolArguments);

      yield {
        type: 'user',
        session_id: offlineSessionId,
        message: {
          role: 'user',
          content: [
            {
              type: 'tool_result',
              tool_use_id: recordedToolCall.toolCallId,
              content: [{ type: 'text', text: toolResultText }],
            },
          ],
        },
      };
    }

    if (recordedTurn.stopReason === 'final_answer') {
      const recordedFinalAnswer = recordedTurn.finalAnswer;
      if (!recordedFinalAnswer) {
        throw new Error(`Recording ${replaySession.conversationId} ended without a final answer.`);
      }
      const finalAnswerText =
        recordedFinalAnswer.answerKind === 'text'
          ? (recordedFinalAnswer.answerText ?? '')
          : JSON.stringify(recordedFinalAnswer.answerJson ?? {});

      yield {
        type: 'result',
        subtype: 'success',
        session_id: offlineSessionId,
        result: finalAnswerText,
        num_turns: modelTurnCount,
        total_cost_usd: OFFLINE_COST_USD,
        structured_output:
          callerAskedForStructuredOutput && recordedFinalAnswer.answerKind === 'json'
            ? recordedFinalAnswer.answerJson
            : undefined,
      };
      return;
    }
  }

  throw new Error(
    `Recording ${replaySession.conversationId} ran out of turns without a final answer. ` +
      'The recording and this build disagree about how the rung ends.',
  );
}

function toMcpToolName(recordedToolName: string): string {
  const mcpToolName = RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME[recordedToolName];
  if (!mcpToolName) {
    throw new Error(`The recording names a tool this build does not know: ${recordedToolName}`);
  }
  return mcpToolName;
}
