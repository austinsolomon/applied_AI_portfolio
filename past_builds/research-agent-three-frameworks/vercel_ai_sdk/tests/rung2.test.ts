// Rung 2 test: the agent loop searches, reads several pages, searches again, then writes a report.
//
// Run it with: npm test (or: node --test tests/rung2.test.ts)

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { runResearchForCompany } from '../lib/researchAgent.ts';
import { recordedFinalAnswerText, recordedToolCallsFor } from './recordedToolCalls.ts';
import {
  loadAllCompanies,
  loadCompanyById,
  loadExpectedVerdict,
} from '../../shared/replay/index.ts';

describe('rung 2 — the agent loop', () => {
  for (const company of loadAllCompanies()) {
    test(`${company.companyName}: makes the recorded tool calls in order and reports the facts`, async () => {
      const researchRun = await runResearchForCompany(company);

      assert.deepEqual(
        researchRun.toolCallsMade,
        recordedToolCallsFor('rung2_agent_loop', company.companyId),
      );
      assert.equal(
        researchRun.researchReport,
        recordedFinalAnswerText('rung2_agent_loop', company.companyId),
      );

      // Every recorded turn was used: the loop ran neither short nor long.
      assert.ok(researchRun.replaySession !== undefined);
      assert.equal(researchRun.replaySession.turnsTaken, researchRun.replaySession.turnCount);

      // The report has to carry the facts the verdict will rest on.
      for (const expectedFactSubstring of loadExpectedVerdict(company.companyId)
        .expectedResearchFactSubstrings) {
        assert.ok(
          researchRun.researchReport.includes(expectedFactSubstring),
          `research report is missing the fact "${expectedFactSubstring}"`,
        );
      }
    });
  }

  test('Rye & Rosemary Bakery: the loop reads the page behind the Order Ahead button', async () => {
    const researchRun = await runResearchForCompany(loadCompanyById('rye_and_rosemary_bakery'));
    const pageUrlsRead = researchRun.toolCallsMade
      .filter((toolCall) => toolCall.toolName === 'fetchPageText')
      .map((toolCall) => (toolCall.toolArguments as { pageUrl: string }).pageUrl);

    assert.ok(pageUrlsRead.includes('https://rye-and-rosemary.example/order'));
    assert.ok(researchRun.researchReport.includes('Pay when you pick up'));
  });

  test('Dos Rios Panaderia: the loop reads the outside listing its narrow search found', async () => {
    const researchRun = await runResearchForCompany(loadCompanyById('dos_rios_panaderia'));
    const pageUrlsRead = researchRun.toolCallsMade
      .filter((toolCall) => toolCall.toolName === 'fetchPageText')
      .map((toolCall) => (toolCall.toolArguments as { pageUrl: string }).pageUrl);

    assert.ok(
      pageUrlsRead.includes('https://chicago-delivery-directory.example/stores/dos-rios-panaderia'),
    );
    assert.ok(researchRun.researchReport.includes('not currently accepting orders'));
  });
});
