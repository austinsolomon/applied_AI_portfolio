// Rung 1 test: the model asks for one page, the build fetches it, the model answers from it.
//
// Run it with: npm test (or: node --test tests/rung1.test.ts)

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { readOnePageThenAnswer } from '../rungs/rung1_one_tool.ts';
import { recordedFinalAnswerText, recordedToolCallsFor } from './recordedToolCalls.ts';
import { loadAllCompanies, loadCompanyById } from '../../shared/replay/index.ts';

describe('rung 1 — one tool: fetch a page', () => {
  for (const company of loadAllCompanies()) {
    test(`${company.companyName}: makes exactly the recorded tool call and answers from the page`, async () => {
      const rungOneResult = await readOnePageThenAnswer(company);

      assert.deepEqual(
        rungOneResult.toolCallsMade,
        recordedToolCallsFor('rung1_one_tool', company.companyId),
      );
      assert.equal(
        rungOneResult.answerText,
        recordedFinalAnswerText('rung1_one_tool', company.companyId),
      );

      // One tool, used once: exactly one page was read, and it was a page of this bakery's site.
      assert.equal(rungOneResult.toolCallsMade.length, 1);
      assert.equal(rungOneResult.toolCallsMade[0].toolName, 'fetchPageText');
      assert.ok(rungOneResult.answerText.includes(company.websiteDomain));
    });
  }

  test('Rye & Rosemary Bakery: reads only the home page, so it never sees the order page', async () => {
    const ryeAndRosemaryBakery = loadCompanyById('rye_and_rosemary_bakery');
    const rungOneResult = await readOnePageThenAnswer(ryeAndRosemaryBakery);

    // The home page is all this rung reads, so the answer rests on the button and never sees the
    // order page behind it. Rung 2 is what reads that page and gets this bakery right.
    assert.deepEqual(rungOneResult.toolCallsMade, [
      {
        toolName: 'fetchPageText',
        toolArguments: { pageUrl: `https://${ryeAndRosemaryBakery.websiteDomain}/` },
      },
    ]);
    assert.ok(rungOneResult.answerText.includes('Order Ahead'));
    assert.ok(!rungOneResult.answerText.includes('emailing our order form'));
  });
});
