// Rung 2: the agent loop, for every fixture bakery.

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  loadAllCompanies,
  loadExpectedVerdict,
  loadRecordedConversation,
} from '../../shared/replay/index.ts';
import { runResearchLoop } from '../worker/rung2_agentLoop.ts';
import { readRecordedFinalAnswerText, readRecordedToolCalls } from './recordedToolCallReader.ts';

describe('rung 2 - the agent loop', () => {
  for (const company of loadAllCompanies()) {
    test(`researches ${company.companyName} in the recorded number of steps`, async () => {
      const agentLoopResult = await runResearchLoop(company.companyId);
      const recordedToolCalls = readRecordedToolCalls('rung2_agent_loop', company.companyId);

      assert.deepEqual(
        agentLoopResult.toolCallsMade,
        recordedToolCalls,
        'search and page reads happen in the recorded order, with the recorded arguments',
      );
      assert.equal(
        agentLoopResult.roundsUsed,
        loadRecordedConversation('rung2_agent_loop', company.companyId).modelTurns.length,
        'one round per recorded model turn: the model, not the code, decided how many',
      );
      assert.equal(
        agentLoopResult.researchReport,
        readRecordedFinalAnswerText('rung2_agent_loop', company.companyId),
      );

      // The report has to contain the facts the verdict will rest on.
      for (const expectedFactSubstring of loadExpectedVerdict(company.companyId)
        .expectedResearchFactSubstrings) {
        assert.ok(
          agentLoopResult.researchReport.includes(expectedFactSubstring),
          `the research report should mention "${expectedFactSubstring}"`,
        );
      }
    });
  }
});
