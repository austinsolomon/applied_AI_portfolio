// Which model does a rung call?
//
// Offline: a language model that replays the recording for that rung and that bakery.
// Live: a plain string such as 'anthropic/claude-sonnet-5'. In the AI SDK a bare string is routed
// through Vercel's AI Gateway, which is one key in front of every provider — that string IS the
// whole model picker, which is why a real product can store it in a database column.

import type { LanguageModel } from 'ai';

import { createRecordedTurnLanguageModel } from './replay/recordedTurnLanguageModel.ts';
import { isLiveModeOn, requireLiveModeKey } from './liveModeSwitch.ts';
import type { CompanyId, ReplayModelSession, RungId } from '../../shared/replay/index.ts';

/** The model ids the artifact page uses. Placeholders: take real ids from the Gateway model list. */
export const RESEARCH_MODEL_ID = 'anthropic/claude-sonnet-5';
export const JUDGE_MODEL_ID = 'anthropic/claude-haiku-4-5';

export interface SelectedLanguageModel {
  languageModel: LanguageModel;
  /** Present only offline: lets a test check that every recorded turn was used. */
  replaySession?: ReplayModelSession;
}

export function selectLanguageModelForRung(
  rungId: RungId,
  companyId: CompanyId,
  liveModelId: string,
): SelectedLanguageModel {
  if (isLiveModeOn()) {
    requireLiveModeKey('AI_GATEWAY_API_KEY');
    return { languageModel: liveModelId };
  }
  const { replayLanguageModel, replaySession } = createRecordedTurnLanguageModel(rungId, companyId);
  return { languageModel: replayLanguageModel, replaySession };
}
