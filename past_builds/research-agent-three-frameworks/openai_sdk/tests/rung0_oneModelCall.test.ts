// Rung 0: one model call, no tools, for every fixture bakery.

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { loadAllCompanies } from '../../shared/replay/index.ts';
import { runOneModelCall } from '../worker/rung0_oneModelCall.ts';
import { readRecordedFinalAnswerText, readRecordedToolCalls } from './recordedToolCallReader.ts';

describe('rung 0 - one model call', () => {
  for (const company of loadAllCompanies()) {
    test(`answers for ${company.companyName} with no tools and no sources`, async () => {
      const oneModelCallResult = await runOneModelCall(company.companyId);

      assert.ok(
        oneModelCallResult.researchQuestion.includes('Does this bakery already take online orders?'),
        'every rung asks the same question about every bakery',
      );
      assert.ok(oneModelCallResult.researchQuestion.includes(company.companyName));
      assert.equal(
        oneModelCallResult.answerText,
        readRecordedFinalAnswerText('rung0_one_model_call', company.companyId),
      );
      assert.deepEqual(
        oneModelCallResult.toolCallsMade,
        readRecordedToolCalls('rung0_one_model_call', company.companyId),
        'rung 0 makes no tool calls, and the recording agrees',
      );
      assert.ok(
        !oneModelCallResult.answerText.includes('http'),
        'with no tools the model has no URL to cite',
      );
    });
  }
});
