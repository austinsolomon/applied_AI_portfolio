// Rung 0 test: a model with no tools answers from training alone, and cites nothing.
//
// Run it with: npm test (or: node --test tests/rung0.test.ts)

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { askTheModelWithNoTools } from '../rungs/rung0_one_model_call.ts';
import { recordedFinalAnswerText, recordedToolCallsFor } from './recordedToolCalls.ts';
import { loadAllCompanies } from '../../shared/replay/index.ts';

describe('rung 0 — one model call, no tools', () => {
  for (const company of loadAllCompanies()) {
    test(`${company.companyName}: replays the recorded reply and calls no tools`, async () => {
      const replyText = await askTheModelWithNoTools(company);

      assert.equal(replyText, recordedFinalAnswerText('rung0_one_model_call', company.companyId));
      assert.deepEqual(recordedToolCallsFor('rung0_one_model_call', company.companyId), []);

      // The lesson of this rung: no tool ran, so no URL is quoted anywhere in the answer.
      assert.ok(
        !replyText.includes(company.websiteDomain),
        'a model with no tools should not be citing the bakery website',
      );
    });
  }
});
