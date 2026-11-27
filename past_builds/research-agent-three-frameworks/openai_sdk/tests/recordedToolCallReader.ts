// A small helper the four rung tests share: what tool calls does the recording say should happen?
//
// Every test compares the calls the build actually made against this list. If a build stops calling
// a tool, or starts calling an extra one, that is the assertion that fails.

import { loadRecordedConversation, type CompanyId, type RungId } from '../../shared/replay/index.ts';

export interface ExpectedToolCall {
  toolCallId: string;
  toolName: string;
  toolArguments: Record<string, string>;
}

export function readRecordedToolCalls(rungId: RungId, companyId: CompanyId): ExpectedToolCall[] {
  const recordedConversation = loadRecordedConversation(rungId, companyId);
  return recordedConversation.modelTurns.flatMap((recordedTurn) =>
    recordedTurn.toolCallsRequested.map((recordedToolCall) => ({
      toolCallId: recordedToolCall.toolCallId,
      toolName: recordedToolCall.toolName,
      toolArguments: recordedToolCall.toolArguments,
    })),
  );
}

export function readRecordedFinalAnswerText(rungId: RungId, companyId: CompanyId): string {
  const recordedConversation = loadRecordedConversation(rungId, companyId);
  const finalTurn = recordedConversation.modelTurns[recordedConversation.modelTurns.length - 1];
  const finalAnswerText = finalTurn.finalAnswer?.answerText;
  if (finalAnswerText === undefined) {
    throw new Error(`${rungId}/${companyId} has no recorded final answer text.`);
  }
  return finalAnswerText;
}
