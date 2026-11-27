// Stands in for the model itself. Given a rung and a company, it hands back the recorded turns one
// at a time, in order. Each build wraps this in the shape its own SDK expects:
//
//   vercel_ai_sdk/    a LanguageModel object passed as `model:`
//   openai_sdk/       a stub client exposing responses.create / responses.parse
//   claude_agent_sdk/ a fake query() that is an async generator of SDK-shaped messages
//
// The wrapping lives in each build folder. This file knows nothing about any SDK.

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { recordedModelResponsesFolderPath } from './fixtureFileLocations.ts';
import type {
  CompanyId,
  RecordedConversation,
  RecordedModelTurn,
  ReplayModelSession,
  RungId,
} from './replayTypes.ts';
import { ReplayExhaustedError, ReplayFixtureMissingError } from './replayTypes.ts';

export function loadRecordedConversation(
  rungId: RungId,
  companyId: CompanyId,
): RecordedConversation {
  const conversationFilePath = join(recordedModelResponsesFolderPath, rungId, `${companyId}.json`);
  try {
    return JSON.parse(readFileSync(conversationFilePath, 'utf8')) as RecordedConversation;
  } catch {
    throw new ReplayFixtureMissingError(`model responses for rung "${rungId}", company "${companyId}"`);
  }
}

export function listRecordedRungIds(): RungId[] {
  return readdirSync(recordedModelResponsesFolderPath, { withFileTypes: true })
    .filter((directoryEntry) => directoryEntry.isDirectory())
    .map((directoryEntry) => directoryEntry.name as RungId)
    .sort();
}

export function listRecordedCompanyIdsForRung(rungId: RungId): CompanyId[] {
  return readdirSync(join(recordedModelResponsesFolderPath, rungId))
    .filter((fileName) => fileName.endsWith('.json'))
    .map((fileName) => fileName.replace(/\.json$/, ''))
    .sort();
}

/**
 * Opens a replay session over one recorded conversation. Call takeNextTurn() once per model call
 * your build would have made; when the recording runs out, it throws instead of inventing a turn.
 */
export function openReplayModelSession(
  rungId: RungId,
  companyId: CompanyId,
): ReplayModelSession {
  const recordedConversation = loadRecordedConversation(rungId, companyId);
  const recordedTurns: RecordedModelTurn[] = recordedConversation.modelTurns;
  let turnsTakenSoFar = 0;

  return {
    conversationId: recordedConversation.conversationId,
    rungId: recordedConversation.rungId,
    companyId: recordedConversation.companyId,
    teachingNote: recordedConversation.teachingNote,
    turnCount: recordedTurns.length,
    get turnsTaken() {
      return turnsTakenSoFar;
    },
    takeNextTurn(): RecordedModelTurn {
      if (turnsTakenSoFar >= recordedTurns.length) {
        throw new ReplayExhaustedError(recordedConversation.conversationId, recordedTurns.length);
      }
      const nextTurn = recordedTurns[turnsTakenSoFar];
      turnsTakenSoFar += 1;
      return nextTurn;
    },
    isFinished(): boolean {
      return turnsTakenSoFar >= recordedTurns.length;
    },
  };
}
