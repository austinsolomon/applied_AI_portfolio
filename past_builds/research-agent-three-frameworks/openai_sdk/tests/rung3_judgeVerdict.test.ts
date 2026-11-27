// Rung 3: research, then judge - the verdict every build must reproduce.
//
// This is the test that makes the four-way comparison mean anything: the same four bakeries, the
// same question ("Does this bakery already take online orders?"), the same rubric, the same
// supportsNoOnlineOrdering and researchReason.

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { loadAllCompanies, loadExpectedVerdict } from '../../shared/replay/index.ts';
import { researchThenJudge, runJudge } from '../worker/rung3_judgeVerdict.ts';
import { readRecordedToolCalls } from './recordedToolCallReader.ts';

describe('rung 3 - the typed verdict', () => {
  for (const company of loadAllCompanies()) {
    test(`returns the expected verdict for ${company.companyName}`, async () => {
      const { verdict } = await researchThenJudge(company.companyId);
      const { expectedVerdict } = loadExpectedVerdict(company.companyId);

      assert.equal(verdict.supportsNoOnlineOrdering, expectedVerdict.supportsNoOnlineOrdering);
      assert.equal(verdict.researchReason, expectedVerdict.researchReason);
      assert.deepEqual(verdict.sourceUrls, expectedVerdict.sourceUrls);
    });

    test(`judges ${company.companyName} without using any tool`, async () => {
      const judgeResult = await runJudge(company.companyId, 'a stored research report');
      assert.deepEqual(
        judgeResult.toolCallsMade,
        readRecordedToolCalls('rung3_judge_verdict', company.companyId),
        'the judge call has no tools, and the recording agrees',
      );
    });
  }

  test('the clear pair: Bluebird Bakery settles true, Oak Street Bakes settles false', async () => {
    const bluebirdRun = await researchThenJudge('bluebird_bakery');
    const oakStreetRun = await researchThenJudge('oak_street_bakes');
    assert.equal(bluebirdRun.verdict.supportsNoOnlineOrdering, true);
    assert.equal(oakStreetRun.verdict.supportsNoOnlineOrdering, false);
  });

  test('the Order Ahead button at Rye & Rosemary Bakery is read through', async () => {
    const { researchReport, verdict } = await researchThenJudge('rye_and_rosemary_bakery');
    assert.equal(verdict.supportsNoOnlineOrdering, true);
    assert.ok(
      verdict.sourceUrls.some((sourceUrl) => sourceUrl.includes('rye-and-rosemary.example/order')),
      'the verdict rests on the order page behind the button, not on the home page',
    );
    assert.ok(researchReport.includes('Pay when you pick up'));
  });

  test('the unproven rule at Dos Rios Panaderia is rejected, not guessed at', async () => {
    const { verdict } = await researchThenJudge('dos_rios_panaderia');
    assert.equal(verdict.supportsNoOnlineOrdering, false);
    assert.match(verdict.researchReason, /unproven/);
  });

  test('the set stays balanced: two bakeries settle true and two settle false', () => {
    const qualifiedCompanyIds = loadAllCompanies()
      .filter((company) => loadExpectedVerdict(company.companyId).expectedVerdict.supportsNoOnlineOrdering)
      .map((company) => company.companyId)
      .sort();
    assert.deepEqual(qualifiedCompanyIds, ['bluebird_bakery', 'rye_and_rosemary_bakery']);
    assert.equal(loadAllCompanies().length, 4);
  });
});
