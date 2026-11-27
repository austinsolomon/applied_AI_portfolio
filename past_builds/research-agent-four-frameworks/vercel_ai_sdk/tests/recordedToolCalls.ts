// Shared by the four rung tests: "which tool calls does the recording say should happen?"
//
// Every test compares this list against the tool calls the AI SDK actually made, in order. If the
// build wired a tool under the wrong name, passed the wrong arguments, or skipped a step, the two
// lists stop matching.

import {
  loadRecordedConversation,
  type CompanyId,
  type RungId,
} from '../../shared/replay/index.ts';

export interface ComparableToolCall {
  toolName: string;
  toolArguments: unknown;
}

export function recordedToolCallsFor(rungId: RungId, companyId: CompanyId): ComparableToolCall[] {
  return loadRecordedConversation(rungId, companyId).modelTurns.flatMap((recordedTurn) =>
    recordedTurn.toolCallsRequested.map((recordedToolCall) => ({
      toolName: recordedToolCall.toolName,
      toolArguments: recordedToolCall.toolArguments,
    })),
  );
}

export function recordedFinalAnswerText(rungId: RungId, companyId: CompanyId): string {
  const recordedTurns = loadRecordedConversation(rungId, companyId).modelTurns;
  const finalTurn = recordedTurns[recordedTurns.length - 1];
  const finalAnswerText = finalTurn.finalAnswer?.answerText;
  if (finalAnswerText === undefined) {
    throw new Error(`Recording ${rungId}:${companyId} has no final answer text.`);
  }
  return finalAnswerText;
}
