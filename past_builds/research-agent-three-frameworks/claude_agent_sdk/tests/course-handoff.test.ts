import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { loadAllCompanies, fetchRecordedPageTextByUrl } from '../../shared/replay/index.ts';
import { runResearch } from '../worker/run-research.ts';
import { judgeLead } from '../worker/judge-lead.ts';

test('evidence has pipeline ids, cited excerpts and no invented qualification', async () => {
  const evidence = JSON.parse(await readFile(new URL('../../shared/fixtures/pipeline_evidence.json', import.meta.url), 'utf8'));
  for (const company of loadAllCompanies()) {
    const item = evidence.answers.find((a: any) => a.lead_id === company.pipelineLeadId);
    assert.ok(item, company.companyName);
    const run = await runResearch({ companyId: company.companyId, runMode: 'offline' });
    const answer = await judgeLead({ companyId: company.companyId, researchReport: run.researchReport, runMode: 'offline' });
    assert.ok(answer.sourceUrls.includes(item.evidence.source_url));
    const page = await fetchRecordedPageTextByUrl(item.evidence.source_url);
    assert.ok(page.pageTextMarkdown.includes(item.evidence.quote), company.companyName + ': quoted text must occur in saved page');
    assert.equal(answer.supportsNoOnlineOrdering, item.verdict.supportsNoOnlineOrdering);
    assert.equal('qualified' in answer, false);
  }
});

test('live path builds a bounded SDK request for a supplied company without a service call', async () => {
  let request: any;
  const result = await runResearch({ companyId: 'learner-company', runMode: 'live', company: {
    companyId: 'learner-company', companyName: 'Learner Bakery', websiteDomain: 'bakery.test', cityName: 'Chicago', stateCode: 'IL', industryLabel: 'Bakery', expectedOutcomeNote: '',
  }, queryFunction: async function* (value) {
    request = value;
    yield { type: 'result', subtype: 'success', result: 'test response', session_id: 'test' };
  }});
  assert.match(request.prompt, /Learner Bakery/);
  assert.ok(request.options.allowedTools.includes('WebSearch'));
  assert.equal(request.options.maxTurns, 12);
  assert.equal(request.options.maxBudgetUsd, 0.25);
  assert.equal(result.researchReport, 'test response');
});
