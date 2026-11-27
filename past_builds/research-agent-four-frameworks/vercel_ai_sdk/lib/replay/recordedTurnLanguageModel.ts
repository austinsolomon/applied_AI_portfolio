// The offline seam for this build.
//
// The AI SDK talks to a model through a small interface called a "language model". A real one posts
// your messages to a provider over the network. This file builds a fake one that answers from the
// recordings in ../../../shared/fixtures/ instead, using the AI SDK's own test double,
// MockLanguageModelV4 (exported from the package's "ai/test" entry point).
//
// Because the fake sits UNDER the SDK rather than replacing it, everything above it is the real
// thing: generateText, the ToolLoopAgent harness, tool-argument validation against the Zod schema,
// the step trace and Output.object's schema parsing all run for real. Only the network is gone.

import { MockLanguageModelV4 } from 'ai/test';
import type {
  LanguageModelV4Content,
  LanguageModelV4FinishReason,
  LanguageModelV4Usage,
} from '@ai-sdk/provider';

import {
  openReplayModelSession,
  type CompanyId,
  type RecordedModelTurn,
  type ReplayModelSession,
  type RungId,
} from '../../../shared/replay/index.ts';

/**
 * Token counts are part of the recording's shape, not of its content: the fixtures record what the
 * model SAID, not what it cost. These stand-in numbers keep `usage` a real object so a learner can
 * see where the per-cell cost would come from, and they are obviously fake on purpose.
 */
const REPLAY_TOKEN_USAGE: LanguageModelV4Usage = {
  inputTokens: { total: 0, noCache: 0, cacheRead: 0, cacheWrite: 0 },
  outputTokens: { total: 0, text: 0, reasoning: 0 },
};

const FINISHED_WITH_TOOL_CALLS: LanguageModelV4FinishReason = {
  unified: 'tool-calls',
  raw: 'replayed_tool_calls',
};

const FINISHED_WITH_AN_ANSWER: LanguageModelV4FinishReason = {
  unified: 'stop',
  raw: 'replayed_final_answer',
};

/**
 * The text the SDK should see for one recorded turn.
 *
 * Rung 3's final answer is a JSON object rather than prose, because that rung asks the model for a
 * typed verdict. Output.object parses the model's text, so the recorded object is handed over as
 * its JSON text and the SDK parses it exactly as it would parse a live provider's reply.
 */
function assistantTextForRecordedTurn(recordedTurn: RecordedModelTurn): string {
  const finalAnswer = recordedTurn.finalAnswer;
  if (finalAnswer !== undefined && finalAnswer.answerKind === 'json') {
    return JSON.stringify(finalAnswer.answerJson);
  }
  if (finalAnswer !== undefined && finalAnswer.answerText !== undefined) {
    return finalAnswer.answerText;
  }
  return recordedTurn.assistantText;
}

function contentPartsForRecordedTurn(recordedTurn: RecordedModelTurn): LanguageModelV4Content[] {
  const contentParts: LanguageModelV4Content[] = [];

  const assistantText = assistantTextForRecordedTurn(recordedTurn);
  if (assistantText.length > 0) {
    contentParts.push({ type: 'text', text: assistantText });
  }

  for (const recordedToolCall of recordedTurn.toolCallsRequested) {
    contentParts.push({
      type: 'tool-call',
      toolCallId: recordedToolCall.toolCallId,
      toolName: recordedToolCall.toolName,
      // The SDK expects the arguments as a JSON string, then validates them against the tool's
      // inputSchema. A recording with arguments the schema rejects would fail here, loudly.
      input: JSON.stringify(recordedToolCall.toolArguments),
    });
  }

  return contentParts;
}

export interface RecordedTurnModelHandle {
  /** Pass this as `model:` anywhere the live build would pass 'anthropic/claude-sonnet-5'. */
  replayLanguageModel: MockLanguageModelV4;
  /** The session underneath, so a test can assert every recorded turn was consumed. */
  replaySession: ReplayModelSession;
}

/**
 * Builds a language model that replays one recorded conversation: one recorded turn per model call
 * the build makes. Asking for more turns than were recorded throws ReplayExhaustedError, which is
 * the signal that the loop is running more times than the recording expects.
 */
export function createRecordedTurnLanguageModel(
  rungId: RungId,
  companyId: CompanyId,
): RecordedTurnModelHandle {
  const replaySession = openReplayModelSession(rungId, companyId);

  const replayLanguageModel = new MockLanguageModelV4({
    modelId: `replay:${rungId}:${companyId}`,
    doGenerate: async () => {
      const recordedTurn = replaySession.takeNextTurn();
      return {
        content: contentPartsForRecordedTurn(recordedTurn),
        finishReason:
          recordedTurn.stopReason === 'tool_calls'
            ? FINISHED_WITH_TOOL_CALLS
            : FINISHED_WITH_AN_ANSWER,
        usage: REPLAY_TOKEN_USAGE,
        warnings: [],
      };
    },
  });

  return { replayLanguageModel, replaySession };
}
