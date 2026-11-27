// Rung 0's test. Offline, no API key, no network.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  listCompanyIdsWithExpectedVerdicts,
  loadExpectedVerdict,
  loadRecordedConversation,
} from '../../shared/replay/index.ts';
import { askOneQuestionWithNoTools } from '../worker/one-model-call.ts';
import { judgeLead } from '../worker/judge-lead.ts';

const companyIds = listCompanyIdsWithExpectedVerdicts();

test('rung 0 answers every fixture bakery from the recording, using no tools', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung0_one_model_call', companyId);
    const recordedFinalTurn = recordedConversation.modelTurns.at(-1);
    assert.ok(recordedFinalTurn, `${companyId}: the recording has no turns`);

    const oneModelCallOutcome = await askOneQuestionWithNoTools({ companyId, runMode: 'offline' });

    assert.equal(oneModelCallOutcome.answerText, recordedFinalTurn.finalAnswer?.answerText);
    assert.equal(oneModelCallOutcome.modelTurnCount, 1, `${companyId}: rung 0 is one model call`);
  }
});

test('rung 0 makes no tool calls, because it has no tools', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung0_one_model_call', companyId);
    const recordedToolCalls = recordedConversation.modelTurns.flatMap(
      (recordedTurn) => recordedTurn.toolCallsRequested,
    );
    assert.deepEqual(recordedToolCalls, [], `${companyId}: rung 0 must request no tools`);
  }
});

// The verdict fields every rung in this build ends up carrying. Offline the judge replays its own
// recording, so this asserts that the verdict plumbing produces the agreed fields for every
// bakery - not that a live model read rung 0's answer.
test('the verdict for every fixture bakery matches the expected verdict', async () => {
  for (const companyId of companyIds) {
    const expectedVerdictRecord = loadExpectedVerdict(companyId);
    const oneModelCallOutcome = await askOneQuestionWithNoTools({ companyId, runMode: 'offline' });
    const qualificationVerdict = await judgeLead({
      companyId,
      researchReport: oneModelCallOutcome.answerText,
      runMode: 'offline',
    });

    assert.equal(qualificationVerdict.supportsNoOnlineOrdering, expectedVerdictRecord.expectedVerdict.supportsNoOnlineOrdering);
    assert.equal(
      qualificationVerdict.researchReason,
      expectedVerdictRecord.expectedVerdict.researchReason,
    );
  }
});
