// Rung 2's test. Offline, no API key, no network.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  listCompanyIdsWithExpectedVerdicts,
  loadExpectedVerdict,
  loadRecordedConversation,
} from '../../shared/replay/index.ts';
import { runResearch } from '../worker/run-research.ts';
import { judgeLead } from '../worker/judge-lead.ts';
import { RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME } from '../worker/research-tools.ts';

const companyIds = listCompanyIdsWithExpectedVerdicts();

test('rung 2 runs exactly the tool calls the recording expects, in order', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung2_agent_loop', companyId);
    const expectedToolCalls = recordedConversation.modelTurns
      .flatMap((recordedTurn) => recordedTurn.toolCallsRequested)
      .map((recordedToolCall) => ({
        mcpToolName: RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME[recordedToolCall.toolName],
        toolArguments: recordedToolCall.toolArguments,
      }));

    const researchRun = await runResearch({ companyId, runMode: 'offline' });

    assert.deepEqual(researchRun.executedToolCalls, expectedToolCalls, companyId);
    assert.ok(expectedToolCalls.length > 1, `${companyId}: rung 2 takes more than one step`);
  }
});

test('rung 2 reports the facts the fixtures say it must find', async () => {
  for (const companyId of companyIds) {
    const expectedVerdictRecord = loadExpectedVerdict(companyId);
    const researchRun = await runResearch({ companyId, runMode: 'offline' });

    for (const expectedFactSubstring of expectedVerdictRecord.expectedResearchFactSubstrings) {
      assert.ok(
        researchRun.researchReport.includes(expectedFactSubstring),
        `${companyId}: the research report is missing "${expectedFactSubstring}"`,
      );
    }
  }
});

test('rung 2 keeps a trace of every model turn and tool result', async () => {
  const researchRun = await runResearch({ companyId: 'bluebird_bakery', runMode: 'offline' });
  assert.ok(researchRun.agentStepTrace.length >= 4, 'the trace is how you debug a wrong cell');
});

test('the loop reads past the Order Ahead button on Rye & Rosemary Bakery\'s home page', async () => {
  const researchRun = await runResearch({ companyId: 'rye_and_rosemary_bakery', runMode: 'offline' });
  const openedPagePaths = researchRun.executedToolCalls
    .filter((executedToolCall) => executedToolCall.toolArguments.pageUrl !== undefined)
    .map((executedToolCall) => new URL(executedToolCall.toolArguments.pageUrl).pathname);

  assert.ok(openedPagePaths.includes('/order'), 'the order page is where the answer is');
  assert.ok(researchRun.researchReport.includes('emailing our order form'));
});

// The end-to-end path a real cell runs: rung 2's report, then rung 3's judge.
test('research then judge produces the expected verdict for every fixture bakery', async () => {
  for (const companyId of companyIds) {
    const expectedVerdictRecord = loadExpectedVerdict(companyId);
    const researchRun = await runResearch({ companyId, runMode: 'offline' });
    const qualificationVerdict = await judgeLead({
      companyId,
      researchReport: researchRun.researchReport,
      runMode: 'offline',
    });

    assert.equal(qualificationVerdict.supportsNoOnlineOrdering, expectedVerdictRecord.expectedVerdict.supportsNoOnlineOrdering);
    assert.equal(
      qualificationVerdict.researchReason,
      expectedVerdictRecord.expectedVerdict.researchReason,
    );
  }
});
