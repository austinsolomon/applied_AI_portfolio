// Rung 3's test. Offline, no API key, no network.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  listCompanyIdsWithExpectedVerdicts,
  loadExpectedVerdict,
  loadRecordedConversation,
} from '../../shared/replay/index.ts';
import { runResearch } from '../worker/run-research.ts';
import { judgeLead, qualificationJsonSchema, qualificationSchema } from '../worker/judge-lead.ts';

const companyIds = listCompanyIdsWithExpectedVerdicts();

test('the judge returns the expected verdict for every fixture bakery', async () => {
  for (const companyId of companyIds) {
    const expectedVerdictRecord = loadExpectedVerdict(companyId);
    const researchRun = await runResearch({ companyId, runMode: 'offline' });
    const qualificationVerdict = await judgeLead({
      companyId,
      researchReport: researchRun.researchReport,
      runMode: 'offline',
    });

    assert.equal(
      qualificationVerdict.supportsNoOnlineOrdering,
      expectedVerdictRecord.expectedVerdict.supportsNoOnlineOrdering,
      `${companyId}: supportsNoOnlineOrdering`,
    );
    assert.equal(
      qualificationVerdict.researchReason,
      expectedVerdictRecord.expectedVerdict.researchReason,
      `${companyId}: researchReason`,
    );
    assert.deepEqual(
      qualificationVerdict.sourceUrls,
      expectedVerdictRecord.expectedVerdict.sourceUrls,
      `${companyId}: sourceUrls`,
    );
  }
});

// Dos Rios Panaderia's own pages never say how orders are taken, so rule 2 of the rubric cannot be
// proven either way. A true here would be a bug, not an opinion.
test('Dos Rios Panaderia is rejected, because the rubric says false when unproven', async () => {
  const researchRun = await runResearch({ companyId: 'dos_rios_panaderia', runMode: 'offline' });
  const qualificationVerdict = await judgeLead({
    companyId: 'dos_rios_panaderia',
    researchReport: researchRun.researchReport,
    runMode: 'offline',
  });
  assert.equal(qualificationVerdict.supportsNoOnlineOrdering, false);
  assert.ok(
    qualificationVerdict.researchReason.includes('unproven'),
    'the reason must say which rule could not be proven',
  );
});

// The other unclear bakery goes the other way: reading the order page proves it qualifies.
test('Rye & Rosemary Bakery settles true once the order page has been read', async () => {
  const researchRun = await runResearch({ companyId: 'rye_and_rosemary_bakery', runMode: 'offline' });
  const qualificationVerdict = await judgeLead({
    companyId: 'rye_and_rosemary_bakery',
    researchReport: researchRun.researchReport,
    runMode: 'offline',
  });
  assert.equal(qualificationVerdict.supportsNoOnlineOrdering, true);
  assert.ok(qualificationVerdict.sourceUrls.includes('https://rye-and-rosemary.example/order'));
});

test('exactly two of the four fixture bakeries settle true', async () => {
  const qualifiedCompanyIds: string[] = [];
  for (const companyId of companyIds) {
    const researchRun = await runResearch({ companyId, runMode: 'offline' });
    const qualificationVerdict = await judgeLead({
      companyId,
      researchReport: researchRun.researchReport,
      runMode: 'offline',
    });
    if (qualificationVerdict.supportsNoOnlineOrdering) qualifiedCompanyIds.push(companyId);
  }
  assert.deepEqual(qualifiedCompanyIds.sort(), ['bluebird_bakery', 'rye_and_rosemary_bakery']);
});

test('the judge call uses no tools at all', async () => {
  for (const companyId of companyIds) {
    const recordedConversation = loadRecordedConversation('rung3_judge_verdict', companyId);
    const recordedToolCalls = recordedConversation.modelTurns.flatMap(
      (recordedTurn) => recordedTurn.toolCallsRequested,
    );
    assert.deepEqual(recordedToolCalls, [], `${companyId}: rung 3 must request no tools`);
  }
});

test('the Zod schema converts to the draft-7 JSON Schema the SDK wants', () => {
  assert.equal(qualificationJsonSchema.type, 'object');
  assert.ok(qualificationJsonSchema.properties);
  const parsedVerdict = qualificationSchema.parse({
    supportsNoOnlineOrdering: true,
    researchReason: 'one sentence',
    sourceUrls: ['https://bluebird-bakery.example/order'],
  });
  assert.equal(parsedVerdict.supportsNoOnlineOrdering, true);
});
