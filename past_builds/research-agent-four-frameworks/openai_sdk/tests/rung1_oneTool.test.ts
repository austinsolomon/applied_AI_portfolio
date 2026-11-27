// Rung 1: one tool, used once, for every fixture bakery.

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { loadAllCompanies, loadCompanyById } from '../../shared/replay/index.ts';
import { runOneTool } from '../worker/rung1_oneTool.ts';
import { readRecordedFinalAnswerText, readRecordedToolCalls } from './recordedToolCallReader.ts';

describe('rung 1 - one tool', () => {
  for (const company of loadAllCompanies()) {
    test(`reads one page for ${company.companyName} and answers from it`, async () => {
      const oneToolResult = await runOneTool(company.companyId);

      assert.deepEqual(
        oneToolResult.toolCallsMade,
        readRecordedToolCalls('rung1_one_tool', company.companyId),
        'the tool calls the build made match the recording exactly',
      );
      assert.equal(
        oneToolResult.answerText,
        readRecordedFinalAnswerText('rung1_one_tool', company.companyId),
      );
      assert.ok(
        oneToolResult.answerText.includes(loadCompanyById(company.companyId).websiteDomain),
        'the answer now cites the page it came from',
      );
    });
  }
});
