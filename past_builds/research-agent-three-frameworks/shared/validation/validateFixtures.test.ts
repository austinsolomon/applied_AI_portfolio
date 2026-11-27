// Tests for the shared fixtures and the replay modules.
//
//   cd shared && npm test
//
// Uses Node's own test runner. No test framework to install, no network, no API keys.

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import {
  buildResearchQuestionForCompany,
  fetchRecordedPageTextByUrl,
  listCompanyIdsWithExpectedVerdicts,
  listRecordedRungIds,
  listRecordedSearchesForCompany,
  loadAllCompanies,
  loadCompanyById,
  loadExpectedVerdict,
  loadQualificationRubric,
  loadResearchQuestion,
  openReplayModelSession,
  ReplayExhaustedError,
  ReplayFixtureMissingError,
  searchRecordedWeb,
} from '../replay/index.ts';
import { validateFixtures } from './validateFixtures.ts';

describe('the fixture set as a whole', () => {
  it('passes every validation check', () => {
    const foundProblems = validateFixtures();
    const problemReport = foundProblems
      .map((problem) => `[${problem.problemArea}] ${problem.problemDescription}`)
      .join('\n');
    assert.equal(foundProblems.length, 0, `expected no fixture problems, got:\n${problemReport}`);
  });

  it('holds exactly the four documented companies', () => {
    const companyIds = loadAllCompanies().map((company) => company.companyId);
    assert.deepEqual(companyIds, [
      'bluebird_bakery',
      'oak_street_bakes',
      'rye_and_rosemary_bakery',
      'dos_rios_panaderia',
    ]);
  });

  it('records all four rungs for every company', () => {
    assert.deepEqual(listRecordedRungIds(), [
      'rung0_one_model_call',
      'rung1_one_tool',
      'rung2_agent_loop',
      'rung3_judge_verdict',
    ]);
    assert.equal(listCompanyIdsWithExpectedVerdicts().length, 4);
  });
});

describe('the research question and the rubric', () => {
  it('asks the one question the whole repository is about', () => {
    assert.equal(loadResearchQuestion().researchQuestionId, 'takes_online_orders');
    const filledQuestion = buildResearchQuestionForCompany(loadCompanyById('bluebird_bakery'));
    assert.equal(
      filledQuestion,
      'Bakery: Bluebird Bakery, Chicago IL (bluebird-bakery.example). Does this bakery already take online orders? ' +
        'Find: how customers place an order today, how many locations it runs, and who owns or manages it. ' +
        'Cite the page you took each fact from.',
    );
  });

  it('judges with the bakery rubric, which says false when a rule is unproven', () => {
    const qualificationRubric = loadQualificationRubric();
    assert.equal(qualificationRubric.qualificationRubricId, 'bakery_ordering_evidence_v2');
    for (const ruleHeading of ['supportsNoOnlineOrdering', 'not a customer-fit decision', '20–1,000 reviews', 'unproven']) {
      assert.ok(qualificationRubric.qualificationRubricText.includes(ruleHeading));
    }
  });

  it('fills every placeholder for every company', () => {
    for (const company of loadAllCompanies()) {
      const filledQuestion = buildResearchQuestionForCompany(company);
      assert.ok(filledQuestion.includes(company.companyName));
      assert.ok(filledQuestion.includes(company.websiteDomain));
      assert.ok(!filledQuestion.includes('{{'));
    }
  });

  it('names the three verdict fields the builds must return', () => {
    assert.deepEqual(loadQualificationRubric().verdictFieldNames, [
      'supportsNoOnlineOrdering',
      'researchReason',
      'sourceUrls',
    ]);
  });
});

describe('the recorded search provider', () => {
  it('returns recorded results for a recorded query', async () => {
    const searchResults = await searchRecordedWeb(
      'bluebird_bakery',
      'Bluebird Bakery Chicago IL online orders locations owner',
    );
    assert.equal(searchResults.length, 3);
    assert.equal(searchResults[0].resultUrl, 'https://bluebird-bakery.example/');
  });

  it('records one broad and one ordering-focused query for every company', () => {
    for (const company of loadAllCompanies()) {
      const recordedQueryTexts = listRecordedSearchesForCompany(company.companyId).map(
        (recordedSearch) => recordedSearch.searchQueryText,
      );
      assert.equal(recordedQueryTexts.length, 2, `${company.companyId} should have two searches`);
      assert.equal(
        recordedQueryTexts[0],
        `${company.companyName} Chicago IL online orders locations owner`,
      );
    }
  });

  it('still matches a query whose wording drifted a little', async () => {
    const searchResults = await searchRecordedWeb(
      'rye_and_rosemary_bakery',
      'Rye and Rosemary order ahead',
    );
    assert.ok(searchResults.some((result) => result.resultUrl.endsWith('/order')));
  });

  it('only the narrow Dos Rios search turns up the stale outside listing', async () => {
    const outsideListingUrl = 'https://chicago-delivery-directory.example/stores/dos-rios-panaderia';
    const broadResults = await searchRecordedWeb(
      'dos_rios_panaderia',
      'Dos Rios Panaderia Chicago IL online orders locations owner',
    );
    const narrowResults = await searchRecordedWeb(
      'dos_rios_panaderia',
      'Dos Rios Panaderia Chicago delivery order online',
    );
    assert.ok(broadResults.every((result) => result.resultUrl !== outsideListingUrl));
    assert.ok(narrowResults.some((result) => result.resultUrl === outsideListingUrl));
  });

  it('throws rather than inventing results for an unrelated query', async () => {
    await assert.rejects(
      () => searchRecordedWeb('bluebird_bakery', 'zzz totally unrelated query'),
      ReplayFixtureMissingError,
    );
  });
});

describe('the recorded page text provider', () => {
  it('returns page text for a recorded URL, with or without a trailing slash', async () => {
    const withSlash = await fetchRecordedPageTextByUrl('https://bluebird-bakery.example/order/');
    const withoutSlash = await fetchRecordedPageTextByUrl('https://bluebird-bakery.example/order');
    assert.equal(withSlash.pageTextMarkdown, withoutSlash.pageTextMarkdown);
    assert.ok(withSlash.pageTextMarkdown.includes('call us or stop by'));
  });

  it('keeps the Rye & Rosemary trap: the button is on the home page, the truth is one click in', async () => {
    const homePage = await fetchRecordedPageTextByUrl('https://rye-and-rosemary.example/');
    const orderPage = await fetchRecordedPageTextByUrl('https://rye-and-rosemary.example/order');
    assert.ok(homePage.pageTextMarkdown.includes('Order Ahead'));
    assert.ok(orderPage.pageTextMarkdown.includes('emailing our order form'));
    assert.ok(orderPage.pageTextMarkdown.includes('Pay when you pick up'));
  });

  it('throws for a URL nobody recorded', async () => {
    await assert.rejects(
      () => fetchRecordedPageTextByUrl('https://bluebird-bakery.example/pricing'),
      ReplayFixtureMissingError,
    );
  });
});

describe('the replay model session', () => {
  it('hands back rung 2 turns in order and then reports it is finished', () => {
    const replaySession = openReplayModelSession('rung2_agent_loop', 'bluebird_bakery');
    assert.equal(replaySession.turnCount, 4);

    const firstTurn = replaySession.takeNextTurn();
    assert.equal(firstTurn.stopReason, 'tool_calls');
    assert.equal(firstTurn.toolCallsRequested[0].toolName, 'searchTheWeb');

    const secondTurn = replaySession.takeNextTurn();
    assert.equal(secondTurn.toolCallsRequested.length, 3);
    assert.ok(
      secondTurn.toolCallsRequested.every((toolCall) => toolCall.toolName === 'fetchPageText'),
    );

    replaySession.takeNextTurn();
    const finalTurn = replaySession.takeNextTurn();
    assert.equal(finalTurn.stopReason, 'final_answer');
    assert.equal(finalTurn.finalAnswer?.answerKind, 'text');
    assert.ok(finalTurn.finalAnswer?.answerText?.includes('HOW ORDERS ARE TAKEN:'));
    assert.ok(finalTurn.finalAnswer?.answerText?.includes('call us or stop by'));

    assert.equal(replaySession.isFinished(), true);
    assert.equal(replaySession.turnsTaken, 4);
  });

  it('takes one extra turn for Dos Rios, to read the outside listing the narrow search found', () => {
    const replaySession = openReplayModelSession('rung2_agent_loop', 'dos_rios_panaderia');
    assert.equal(replaySession.turnCount, 5);
    const requestedPageUrls: string[] = [];
    while (!replaySession.isFinished()) {
      for (const toolCall of replaySession.takeNextTurn().toolCallsRequested) {
        if (toolCall.toolName === 'fetchPageText') requestedPageUrls.push(toolCall.toolArguments.pageUrl);
      }
    }
    assert.ok(
      requestedPageUrls.includes('https://chicago-delivery-directory.example/stores/dos-rios-panaderia'),
    );
  });

  it('throws when a build loops more times than the recording expects', () => {
    const replaySession = openReplayModelSession('rung0_one_model_call', 'bluebird_bakery');
    replaySession.takeNextTurn();
    assert.throws(() => replaySession.takeNextTurn(), ReplayExhaustedError);
  });

  it('throws for a rung and company that were never recorded', () => {
    assert.throws(
      () => openReplayModelSession('rung2_agent_loop', 'not_a_real_company'),
      ReplayFixtureMissingError,
    );
  });
});

describe('the verdicts the three builds must reproduce', () => {
  it('settles true for the bakery that says it does not take orders online', () => {
    const { expectedVerdict } = loadExpectedVerdict('bluebird_bakery');
    assert.equal(expectedVerdict.supportsNoOnlineOrdering, true);
    assert.ok(expectedVerdict.sourceUrls.includes('https://bluebird-bakery.example/order'));
  });

  it('settles false for the bakery with a working cart and checkout', () => {
    const { expectedVerdict } = loadExpectedVerdict('oak_street_bakes');
    assert.equal(expectedVerdict.supportsNoOnlineOrdering, false);
    assert.match(expectedVerdict.researchReason, /already takes online orders/i);
  });

  it('settles true for Rye & Rosemary only because the page behind the button was read', () => {
    const { expectedVerdict } = loadExpectedVerdict('rye_and_rosemary_bakery');
    assert.equal(expectedVerdict.supportsNoOnlineOrdering, true);
    assert.ok(expectedVerdict.sourceUrls.includes('https://rye-and-rosemary.example/order'));
  });

  it('settles false for Dos Rios on the false-when-unproven rule, and says which rule', () => {
    const { expectedVerdict } = loadExpectedVerdict('dos_rios_panaderia');
    assert.equal(expectedVerdict.supportsNoOnlineOrdering, false);
    assert.match(expectedVerdict.researchReason, /The ordering method is unproven/);
  });

  it('judges every company against the one rubric on disk', () => {
    const { qualificationRubricId } = loadQualificationRubric();
    for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
      assert.equal(loadExpectedVerdict(companyId).qualificationRubricId, qualificationRubricId);
    }
  });

  it('matches the judge rung recording for every company', () => {
    for (const companyId of listCompanyIdsWithExpectedVerdicts()) {
      const replaySession = openReplayModelSession('rung3_judge_verdict', companyId);
      const judgeTurn = replaySession.takeNextTurn();
      const recordedVerdict = judgeTurn.finalAnswer?.answerJson;
      const { expectedVerdict } = loadExpectedVerdict(companyId);
      assert.deepEqual(recordedVerdict, { ...expectedVerdict } as Record<string, unknown>);
      assert.equal(loadCompanyById(companyId).companyId, companyId);
    }
  });
});
