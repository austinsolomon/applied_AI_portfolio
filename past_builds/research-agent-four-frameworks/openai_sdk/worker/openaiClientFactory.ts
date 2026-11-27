// Where the `openai` client is built. Two ways to build one:
//
//   createOfflineReplayClient(...)  - the default for rungs 0-3. No key, no network, no money.
//   createLiveClient()              - only when the learner opts in. Real key, real requests, real cost.
//
// Both return the same real `OpenAI` object from the `openai` package, so the code in the rungs is
// identical either way.

import OpenAI from 'openai';
import {
  createRecordedResponsesTransport,
  type RecordedResponsesTransport,
} from './recordedResponsesTransport.ts';
import type { CompanyId, RungId } from '../../shared/replay/index.ts';

/**
 * The model id the offline rungs report. Nothing is sent anywhere, so this is a label, not a choice.
 * The live path reads OPENAI_MODEL from the environment instead.
 */
export const OFFLINE_REPLAY_MODEL_ID = 'recorded-fixture-model';

/** The SDK insists on an API key string. Offline it is never used, so it says so out loud. */
const OFFLINE_PLACEHOLDER_API_KEY = 'offline-replay-no-key-needed';

export interface OfflineReplayClient {
  openaiClient: OpenAI;
  transport: RecordedResponsesTransport;
  modelId: string;
}

/** A real OpenAI client whose wire is the recordings for one rung and one bakery. */
export function createOfflineReplayClient(
  rungId: RungId,
  companyId: CompanyId,
): OfflineReplayClient {
  const transport = createRecordedResponsesTransport(rungId, companyId);
  const openaiClient = new OpenAI({
    apiKey: OFFLINE_PLACEHOLDER_API_KEY,
    fetch: transport.replayFetch,
    maxRetries: 0, // the worker owns retries; a replayed call must never be retried
  });
  return { openaiClient, transport, modelId: OFFLINE_REPLAY_MODEL_ID };
}

/**
 * A real OpenAI client that reaches the real API. Costs money. Nothing in this repo calls it: the
 * learner swaps it in by hand, and the README says so in plain words.
 */
export function createLiveClient(): { openaiClient: OpenAI; modelId: string } {
  const apiKey = process.env.OPENAI_API_KEY;
  const modelId = process.env.OPENAI_MODEL;
  if (!apiKey) {
    throw new Error('Live mode needs OPENAI_API_KEY in the environment. See .env.example.');
  }
  if (!modelId) {
    throw new Error('Live mode needs OPENAI_MODEL in the environment. See .env.example.');
  }
  return { openaiClient: new OpenAI({ apiKey, maxRetries: 0 }), modelId };
}
