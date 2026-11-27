// Rung 1's test. Offline, no API key, no network.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  listCompanyIdsWithExpectedVerdicts,
  loadExpectedVerdict,
  loadRecordedConversation,
} from '../../shared/replay/index.ts';
import { researchFromOnePage } from '../worker/run-single-page-research.ts';
import { judgeLead } from '../worker/judge-lead.ts';
import { RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME } from '../worker/research-tools.ts';

const companyIds = listCompanyIdsWithExpectedVerdicts();

test('rung 1 runs exactly the tool calls the recording expects', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung1_one_tool', companyId);
    const expectedToolCalls = recordedConversation.modelTurns
      .flatMap((recordedTurn) => recordedTurn.toolCallsRequested)
      .map((recordedToolCall) => ({
        mcpToolName: RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME[recordedToolCall.toolName],
        toolArguments: recordedToolCall.toolArguments,
      }));

    const singlePageResearch = await researchFromOnePage({ companyId, runMode: 'offline' });

    assert.deepEqual(singlePageResearch.executedToolCalls, expectedToolCalls, companyId);
    assert.equal(expectedToolCalls.length, 1, `${companyId}: rung 1 reads one page`);
  }
});

test('rung 1 returns the recorded answer and cites the page it opened', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung1_one_tool', companyId);
    const recordedFinalTurn = recordedConversation.modelTurns.at(-1);
    const singlePageResearch = await researchFromOnePage({ companyId, runMode: 'offline' });

    assert.equal(singlePageResearch.answerText, recordedFinalTurn?.finalAnswer?.answerText, companyId);
    const openedPageUrl = singlePageResearch.executedToolCalls[0].toolArguments.pageUrl;
    assert.ok(
      singlePageResearch.answerText.includes(new URL(openedPageUrl).host),
      `${companyId}: the answer should name the site it was read from`,
    );
  }
});

// The reason Rye & Rosemary Bakery is in the set: its home page shows an Order Ahead button, and
// only the order page behind it says orders are phoned in or emailed on a form. One page is not
// enough, and rung 2 is what fixes it.
test('one page is not enough for Rye & Rosemary Bakery: rung 1 never reaches the order page', async () => {
  const singlePageResearch = await researchFromOnePage({
    companyId: 'rye_and_rosemary_bakery',
    runMode: 'offline',
  });
  const openedPageUrls = singlePageResearch.executedToolCalls.map(
    (executedToolCall) => executedToolCall.toolArguments.pageUrl,
  );

  assert.equal(openedPageUrls.length, 1);
  assert.equal(new URL(openedPageUrls[0]).pathname, '/', 'rung 1 opens the home page only');
  assert.ok(
    !singlePageResearch.answerText.includes('emailing our order form'),
    'the fact that settles this bakery is on the order page, which rung 1 never opens',
  );
});

// Offline the judge replays its own recording, so this asserts the verdict plumbing, not that a
// live model judged rung 1's answer.
test('the verdict for every fixture bakery matches the expected verdict', async () => {
  for (const companyId of companyIds) {
    const expectedVerdictRecord = loadExpectedVerdict(companyId);
    const singlePageResearch = await researchFromOnePage({ companyId, runMode: 'offline' });
    const qualificationVerdict = await judgeLead({
      companyId,
      researchReport: singlePageResearch.answerText,
      runMode: 'offline',
    });

    assert.equal(qualificationVerdict.supportsNoOnlineOrdering, expectedVerdictRecord.expectedVerdict.supportsNoOnlineOrdering);
    assert.equal(
      qualificationVerdict.researchReason,
      expectedVerdictRecord.expectedVerdict.researchReason,
    );
  }
});
