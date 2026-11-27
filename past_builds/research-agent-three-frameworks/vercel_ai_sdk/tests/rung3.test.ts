// Rung 3 test: the finished task. Research every bakery, judge it against the rubric, and check
// the verdict field by field against shared/fixtures/expected_verdicts/.
//
// This is the test that makes the four-way comparison mean anything: all three builds in this repo
// have to reproduce these same four verdicts, word for word.
//
// Run it with: npm test (or: node --test tests/rung3.test.ts)

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { researchThenJudgeCompany } from '../rungs/rung3_judge_verdict.ts';
import { recordedToolCallsFor } from './recordedToolCalls.ts';
import { loadAllCompanies, loadExpectedVerdict } from '../../shared/replay/index.ts';

describe('rung 3 — research, then judge', () => {
  for (const company of loadAllCompanies()) {
    test(`${company.companyName}: returns the expected verdict`, async () => {
      const { qualificationVerdict, toolCallsMade } = await researchThenJudgeCompany(
        company.companyId,
      );
      const { expectedVerdict } = loadExpectedVerdict(company.companyId);

      assert.equal(qualificationVerdict.supportsNoOnlineOrdering, expectedVerdict.supportsNoOnlineOrdering);
      assert.equal(qualificationVerdict.researchReason, expectedVerdict.researchReason);
      assert.deepEqual(qualificationVerdict.sourceUrls, expectedVerdict.sourceUrls);

      // The judge call itself uses no tools; the research that fed it used exactly the recorded ones.
      assert.deepEqual(recordedToolCallsFor('rung3_judge_verdict', company.companyId), []);
      assert.deepEqual(toolCallsMade, recordedToolCallsFor('rung2_agent_loop', company.companyId));
    });
  }

  test('the two unclear bakeries come out opposite ways', async () => {
    // Rye & Rosemary Bakery: the Order Ahead button only leads to a phone number and an emailed
    // form, so reading further proves the ordering method and the verdict is true.
    const ryeAndRosemaryRun = await researchThenJudgeCompany('rye_and_rosemary_bakery');
    assert.equal(ryeAndRosemaryRun.qualificationVerdict.supportsNoOnlineOrdering, true);

    // Dos Rios Panaderia: no page ever says how orders are taken, so rule 2 stays unproven, and
    // the rubric says unproven means false.
    const dosRiosRun = await researchThenJudgeCompany('dos_rios_panaderia');
    assert.equal(dosRiosRun.qualificationVerdict.supportsNoOnlineOrdering, false);
    assert.ok(dosRiosRun.qualificationVerdict.researchReason.includes('unproven'));
  });

  test('two bakeries settle true and two settle false', async () => {
    const qualifiedCompanyIds: string[] = [];
    for (const company of loadAllCompanies()) {
      const { qualificationVerdict } = await researchThenJudgeCompany(company.companyId);
      if (qualificationVerdict.supportsNoOnlineOrdering) {
        qualifiedCompanyIds.push(company.companyId);
      }
    }
    assert.deepEqual(qualifiedCompanyIds, ['bluebird_bakery', 'rye_and_rosemary_bakery']);
  });
});
