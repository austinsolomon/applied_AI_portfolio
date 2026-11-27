// Checks that every fixture file is present, well shaped and internally consistent.
//
// Run it:   npm run validate   (from shared/)
//    or:    node --experimental-strip-types shared/validation/validateFixtures.ts
//
// It reads only. It never writes, and it never touches the network. Exit code 0 means every check
// passed; exit code 1 prints one line per problem.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import {
  EXPECTED_FIXTURE_VERSION,
  companiesFilePath,
  expectedVerdictsFolderPath,
  qualificationRubricFilePath,
  recordedModelResponsesFolderPath,
  recordedPageIndexFilePath,
  recordedPageTextsFolderPath,
  recordedSearchResultsFolderPath,
  researchQuestionFilePath,
} from '../replay/fixtureFileLocations.ts';
import {
  buildResearchQuestionForCompany,
  listRecordedCompanyIdsForRung,
  listRecordedPages,
  listRecordedRungIds,
  listRecordedSearchesForCompany,
  loadAllCompanies,
  loadExpectedVerdict,
  loadQualificationRubric,
  loadRecordedConversation,
  loadResearchQuestion,
} from '../replay/index.ts';
import type { CompanyId, RungId } from '../replay/replayTypes.ts';

const EXPECTED_RUNG_IDS: RungId[] = [
  'rung0_one_model_call',
  'rung1_one_tool',
  'rung2_agent_loop',
  'rung3_judge_verdict',
];

const MINIMUM_PAGE_WORD_COUNT = 300;
const MAXIMUM_PAGE_WORD_COUNT = 800;
// A page on somebody else's site (the stale delivery listing the Dos Rios search turns up) is
// allowed to be short, because a dead directory listing really is short.
const MINIMUM_OUTSIDE_PAGE_WORD_COUNT = 80;
const MINIMUM_PAGES_PER_COMPANY = 3;
const REQUIRED_DOMAIN_SUFFIX = '.example';
const EXPECTED_QUALIFYING_COMPANY_COUNT = 2;

export interface FixtureProblem {
  problemArea: string;
  problemDescription: string;
}

function countWords(pageMarkdown: string): number {
  return pageMarkdown.split(/\s+/).filter((word) => word.length > 0).length;
}

/** The host part of a URL, or an empty string when the text is not a URL at all. */
function hostNameOfUrl(urlText: string): string {
  try {
    return new URL(urlText).hostname;
  } catch {
    return '';
  }
}

function checkFixtureVersion(
  problems: FixtureProblem[],
  problemArea: string,
  fixtureVersion: unknown,
): void {
  if (fixtureVersion !== EXPECTED_FIXTURE_VERSION) {
    problems.push({
      problemArea,
      problemDescription: `fixtureVersion is ${JSON.stringify(fixtureVersion)}, expected "${EXPECTED_FIXTURE_VERSION}"`,
    });
  }
}

export function validateFixtures(): FixtureProblem[] {
  const problems: FixtureProblem[] = [];

  // --- companies.json ---------------------------------------------------------------------------
  const allCompanies = loadAllCompanies();
  const allCompanyIds: CompanyId[] = allCompanies.map((company) => company.companyId);

  if (allCompanies.length !== 4) {
    problems.push({
      problemArea: 'companies.json',
      problemDescription: `expected 4 companies, found ${allCompanies.length}`,
    });
  }
  if (new Set(allCompanyIds).size !== allCompanyIds.length) {
    problems.push({ problemArea: 'companies.json', problemDescription: 'companyId values are not unique' });
  }
  for (const company of allCompanies) {
    const companyArea = `companies.json / ${company.companyId}`;
    if (!/^[a-z0-9_]+$/.test(company.companyId)) {
      problems.push({
        problemArea: companyArea,
        problemDescription: 'companyId must be lower case letters, digits and underscores only',
      });
    }
    if (!company.websiteDomain.endsWith(REQUIRED_DOMAIN_SUFFIX)) {
      problems.push({
        problemArea: companyArea,
        problemDescription: `websiteDomain "${company.websiteDomain}" must end in ${REQUIRED_DOMAIN_SUFFIX} so no fixture can point at a real business`,
      });
    }
    for (const requiredFieldName of [
      'companyName',
      'cityName',
      'stateCode',
      'industryLabel',
      'expectedOutcomeNote',
    ] as const) {
      if (typeof company[requiredFieldName] !== 'string' || company[requiredFieldName].length === 0) {
        problems.push({
          problemArea: companyArea,
          problemDescription: `${requiredFieldName} is missing or empty`,
        });
      }
    }
  }

  // --- the three task files declare the version too ---------------------------------------------
  for (const [taskFileName, taskFilePath] of [
    ['companies.json', companiesFilePath],
    ['research_question.json', researchQuestionFilePath],
    ['qualification_rubric.json', qualificationRubricFilePath],
  ] as const) {
    checkFixtureVersion(
      problems,
      taskFileName,
      (JSON.parse(readFileSync(taskFilePath, 'utf8')) as { fixtureVersion: string }).fixtureVersion,
    );
  }

  // --- research_question.json -------------------------------------------------------------------
  const researchQuestion = loadResearchQuestion();
  for (const placeholderName of researchQuestion.templatePlaceholderNames) {
    if (!researchQuestion.researchQuestionTemplate.includes(`{{${placeholderName}}}`)) {
      problems.push({
        problemArea: 'research_question.json',
        problemDescription: `templatePlaceholderNames lists "${placeholderName}" but the template does not contain {{${placeholderName}}}`,
      });
    }
  }
  for (const company of allCompanies) {
    const filledQuestion = buildResearchQuestionForCompany(company);
    if (filledQuestion.includes('{{')) {
      problems.push({
        problemArea: 'research_question.json',
        problemDescription: `a placeholder was left unfilled for ${company.companyId}: ${filledQuestion}`,
      });
    }
  }

  // --- qualification_rubric.json ----------------------------------------------------------------
  const qualificationRubric = loadQualificationRubric();
  if (qualificationRubric.qualificationRubricText.length < 200) {
    problems.push({
      problemArea: 'qualification_rubric.json',
      problemDescription: 'qualificationRubricText is suspiciously short; the judge needs real rules to apply',
    });
  }
  for (const requiredVerdictFieldName of ['supportsNoOnlineOrdering', 'researchReason', 'sourceUrls']) {
    if (!qualificationRubric.verdictFieldNames.includes(requiredVerdictFieldName)) {
      problems.push({
        problemArea: 'qualification_rubric.json',
        problemDescription: `verdictFieldNames is missing "${requiredVerdictFieldName}"`,
      });
    }
  }

  // --- recorded_page_texts/ ---------------------------------------------------------------------
  const recordedPages = listRecordedPages();
  checkFixtureVersion(
    problems,
    'recorded_page_texts/page_index.json',
    (JSON.parse(readFileSync(recordedPageIndexFilePath, 'utf8')) as { fixtureVersion: string })
      .fixtureVersion,
  );
  const recordedPageUrls = new Set<string>();
  const pageCountByCompanyId = new Map<CompanyId, number>();
  const ownSitePageCountByCompanyId = new Map<CompanyId, number>();

  for (const pageEntry of recordedPages) {
    const pageArea = `recorded_page_texts/${pageEntry.pageTextFilePath}`;
    if (recordedPageUrls.has(pageEntry.pageUrl)) {
      problems.push({ problemArea: pageArea, problemDescription: `duplicate pageUrl ${pageEntry.pageUrl}` });
    }
    recordedPageUrls.add(pageEntry.pageUrl);

    if (!allCompanyIds.includes(pageEntry.companyId)) {
      problems.push({
        problemArea: pageArea,
        problemDescription: `companyId "${pageEntry.companyId}" is not in companies.json`,
      });
    }
    if (!hostNameOfUrl(pageEntry.pageUrl).endsWith(REQUIRED_DOMAIN_SUFFIX)) {
      problems.push({
        problemArea: pageArea,
        problemDescription: `pageUrl "${pageEntry.pageUrl}" does not use a ${REQUIRED_DOMAIN_SUFFIX} domain`,
      });
    }

    const markdownFilePath = join(recordedPageTextsFolderPath, pageEntry.pageTextFilePath);
    let pageMarkdown = '';
    try {
      pageMarkdown = readFileSync(markdownFilePath, 'utf8');
    } catch {
      problems.push({ problemArea: pageArea, problemDescription: 'the Markdown file is missing' });
      continue;
    }
    const pageWordCount = countWords(pageMarkdown);
    const owningCompany = allCompanies.find(
      (candidateCompany) => candidateCompany.companyId === pageEntry.companyId,
    );
    const pageIsOnTheCompanyOwnSite =
      owningCompany !== undefined &&
      hostNameOfUrl(pageEntry.pageUrl) === owningCompany.websiteDomain;
    const minimumWordCountForThisPage = pageIsOnTheCompanyOwnSite
      ? MINIMUM_PAGE_WORD_COUNT
      : MINIMUM_OUTSIDE_PAGE_WORD_COUNT;
    if (pageWordCount < minimumWordCountForThisPage || pageWordCount > MAXIMUM_PAGE_WORD_COUNT) {
      problems.push({
        problemArea: pageArea,
        problemDescription: `page is ${pageWordCount} words; this page must be ${minimumWordCountForThisPage}-${MAXIMUM_PAGE_WORD_COUNT}`,
      });
    }
    if (pageIsOnTheCompanyOwnSite) {
      ownSitePageCountByCompanyId.set(
        pageEntry.companyId,
        (ownSitePageCountByCompanyId.get(pageEntry.companyId) ?? 0) + 1,
      );
    }
    if (!pageMarkdown.includes(pageEntry.pageUrl)) {
      problems.push({
        problemArea: pageArea,
        problemDescription: 'the Markdown file does not state its own "Source URL:" line',
      });
    }
    pageCountByCompanyId.set(
      pageEntry.companyId,
      (pageCountByCompanyId.get(pageEntry.companyId) ?? 0) + 1,
    );
  }

  for (const companyId of allCompanyIds) {
    const ownSitePageCount = ownSitePageCountByCompanyId.get(companyId) ?? 0;
    if (ownSitePageCount < MINIMUM_PAGES_PER_COMPANY) {
      problems.push({
        problemArea: `recorded_page_texts/${companyId}`,
        problemDescription: `${ownSitePageCount} recorded pages on the bakery's own site (${pageCountByCompanyId.get(companyId) ?? 0} in all); at least ${MINIMUM_PAGES_PER_COMPANY} are required`,
      });
    }
  }

  // Every .md file on disk must be listed in page_index.json, or a build can never reach it.
  for (const companyFolderName of readdirSync(recordedPageTextsFolderPath)) {
    const companyFolderPath = join(recordedPageTextsFolderPath, companyFolderName);
    if (!statSync(companyFolderPath).isDirectory()) continue;
    for (const markdownFileName of readdirSync(companyFolderPath)) {
      const relativeFilePath = `${companyFolderName}/${markdownFileName}`;
      if (!recordedPages.some((pageEntry) => pageEntry.pageTextFilePath === relativeFilePath)) {
        problems.push({
          problemArea: 'recorded_page_texts/page_index.json',
          problemDescription: `${relativeFilePath} exists on disk but is not listed in the index`,
        });
      }
    }
  }

  // --- recorded_search_results/ -----------------------------------------------------------------
  const recordedSearchFileNames = readdirSync(recordedSearchResultsFolderPath).filter((fileName) =>
    fileName.endsWith('.json'),
  );
  if (recordedSearchFileNames.length !== allCompanyIds.length) {
    problems.push({
      problemArea: 'recorded_search_results/',
      problemDescription: `${recordedSearchFileNames.length} files for ${allCompanyIds.length} companies; there must be exactly one per company`,
    });
  }
  for (const companyId of allCompanyIds) {
    const searchArea = `recorded_search_results/${companyId}.json`;
    let recordedSearches;
    try {
      recordedSearches = listRecordedSearchesForCompany(companyId);
    } catch {
      problems.push({ problemArea: searchArea, problemDescription: 'the file is missing or unreadable' });
      continue;
    }
    checkFixtureVersion(
      problems,
      searchArea,
      (JSON.parse(readFileSync(join(recordedSearchResultsFolderPath, `${companyId}.json`), 'utf8')) as {
        fixtureVersion: string;
      }).fixtureVersion,
    );
    if (recordedSearches.length === 0) {
      problems.push({ problemArea: searchArea, problemDescription: 'no recorded searches' });
    }
    for (const recordedSearch of recordedSearches) {
      if (recordedSearch.searchResults.length === 0) {
        problems.push({
          problemArea: searchArea,
          problemDescription: `recorded search "${recordedSearch.searchQueryText}" has no results`,
        });
      }
      for (const searchResult of recordedSearch.searchResults) {
        if (!recordedPageUrls.has(searchResult.resultUrl)) {
          problems.push({
            problemArea: searchArea,
            problemDescription: `result URL ${searchResult.resultUrl} has no recorded page text, so an agent that follows it hits a dead end`,
          });
        }
        if (
          !recordedPages.some(
            (pageEntry) => pageEntry.pageTextFixtureId === searchResult.pageTextFixtureId,
          )
        ) {
          problems.push({
            problemArea: searchArea,
            problemDescription: `pageTextFixtureId "${searchResult.pageTextFixtureId}" is not in page_index.json`,
          });
        }
      }
    }
  }

  // --- recorded_model_responses/ ----------------------------------------------------------------
  const recordedRungIds = listRecordedRungIds();
  for (const expectedRungId of EXPECTED_RUNG_IDS) {
    if (!recordedRungIds.includes(expectedRungId)) {
      problems.push({
        problemArea: 'recorded_model_responses/',
        problemDescription: `rung folder "${expectedRungId}" is missing`,
      });
    }
  }

  for (const rungId of recordedRungIds) {
    const companyIdsForRung = listRecordedCompanyIdsForRung(rungId);
    for (const companyId of allCompanyIds) {
      const conversationArea = `recorded_model_responses/${rungId}/${companyId}.json`;
      if (!companyIdsForRung.includes(companyId)) {
        problems.push({ problemArea: conversationArea, problemDescription: 'the recording is missing' });
        continue;
      }
      const recordedConversation = loadRecordedConversation(rungId, companyId);
      checkFixtureVersion(problems, conversationArea, recordedConversation.fixtureVersion);

      if (recordedConversation.rungId !== rungId || recordedConversation.companyId !== companyId) {
        problems.push({
          problemArea: conversationArea,
          problemDescription: 'rungId or companyId inside the file does not match its path',
        });
      }
      if (recordedConversation.modelTurns.length === 0) {
        problems.push({ problemArea: conversationArea, problemDescription: 'no model turns' });
        continue;
      }

      recordedConversation.modelTurns.forEach((modelTurn, expectedTurnIndex) => {
        const turnArea = `${conversationArea} turn ${expectedTurnIndex}`;
        if (modelTurn.turnIndex !== expectedTurnIndex) {
          problems.push({
            problemArea: turnArea,
            problemDescription: `turnIndex is ${modelTurn.turnIndex}; turns must be contiguous from 0`,
          });
        }
        const isLastTurn = expectedTurnIndex === recordedConversation.modelTurns.length - 1;
        if (isLastTurn) {
          if (modelTurn.stopReason !== 'final_answer' || modelTurn.finalAnswer === undefined) {
            problems.push({
              problemArea: turnArea,
              problemDescription: 'the last turn must have stopReason "final_answer" and a finalAnswer',
            });
          }
          if (modelTurn.toolCallsRequested.length !== 0) {
            problems.push({
              problemArea: turnArea,
              problemDescription: 'the last turn must not request tools',
            });
          }
        } else if (
          modelTurn.stopReason !== 'tool_calls' ||
          modelTurn.toolCallsRequested.length === 0
        ) {
          problems.push({
            problemArea: turnArea,
            problemDescription: 'every turn before the last must have stopReason "tool_calls" and at least one tool call',
          });
        }

        for (const toolCall of modelTurn.toolCallsRequested) {
          if (toolCall.toolName === 'fetchPageText') {
            const requestedPageUrl = toolCall.toolArguments.pageUrl;
            if (requestedPageUrl === undefined) {
              problems.push({
                problemArea: turnArea,
                problemDescription: 'a fetchPageText call has no pageUrl argument',
              });
            } else if (!recordedPageUrls.has(requestedPageUrl)) {
              problems.push({
                problemArea: turnArea,
                problemDescription: `fetchPageText asks for ${requestedPageUrl}, which has no recorded page text`,
              });
            }
          } else if (toolCall.toolName === 'searchTheWeb') {
            const requestedQueryText = toolCall.toolArguments.searchQueryText;
            if (requestedQueryText === undefined) {
              problems.push({
                problemArea: turnArea,
                problemDescription: 'a searchTheWeb call has no searchQueryText argument',
              });
            } else if (
              !listRecordedSearchesForCompany(companyId).some(
                (recordedSearch) => recordedSearch.searchQueryText === requestedQueryText,
              )
            ) {
              problems.push({
                problemArea: turnArea,
                problemDescription: `searchTheWeb asks for "${requestedQueryText}", which is not a recorded query for this company`,
              });
            }
          } else {
            problems.push({
              problemArea: turnArea,
              problemDescription: `unknown toolName "${toolCall.toolName}"`,
            });
          }
        }
      });

      // Rung 0 has no tools at all; rung 3 is the tool-less judge call.
      if (rungId === 'rung0_one_model_call' || rungId === 'rung3_judge_verdict') {
        const anyToolWasCalled = recordedConversation.modelTurns.some(
          (modelTurn) => modelTurn.toolCallsRequested.length > 0,
        );
        if (anyToolWasCalled) {
          problems.push({
            problemArea: conversationArea,
            problemDescription: `${rungId} must call no tools`,
          });
        }
      }

      if (rungId === 'rung2_agent_loop') {
        const researchReportText = recordedConversation.modelTurns.at(-1)?.finalAnswer?.answerText;
        if (researchReportText === undefined) {
          problems.push({
            problemArea: conversationArea,
            problemDescription: 'the agent-loop rung must end in a finalAnswer with answerKind "text"',
          });
        } else {
          let expectedFactSubstrings: string[] = [];
          try {
            expectedFactSubstrings = loadExpectedVerdict(companyId).expectedResearchFactSubstrings;
          } catch {
            // The missing expected-verdict file is reported once, by the rung 3 check below.
          }
          for (const expectedFactSubstring of expectedFactSubstrings) {
            if (!researchReportText.includes(expectedFactSubstring)) {
              problems.push({
                problemArea: conversationArea,
                problemDescription: `the research report does not contain "${expectedFactSubstring}", which expected_verdicts/${companyId}.json says it must (the match is exact and case-sensitive)`,
              });
            }
          }
        }
      }

      if (rungId === 'rung3_judge_verdict') {
        const finalTurn = recordedConversation.modelTurns.at(-1);
        const verdictFromRecording = finalTurn?.finalAnswer?.answerJson;
        if (finalTurn?.finalAnswer?.answerKind !== 'json' || verdictFromRecording === undefined) {
          problems.push({
            problemArea: conversationArea,
            problemDescription: 'the judge rung must end in a finalAnswer with answerKind "json"',
          });
        } else {
          let expectedVerdictRecord;
          try {
            expectedVerdictRecord = loadExpectedVerdict(companyId);
          } catch {
            problems.push({
              problemArea: `expected_verdicts/${companyId}.json`,
              problemDescription: 'the file is missing or unreadable',
            });
            continue;
          }
          if (expectedVerdictRecord.qualificationRubricId !== qualificationRubric.qualificationRubricId) {
            problems.push({
              problemArea: `expected_verdicts/${companyId}.json`,
              problemDescription: `qualificationRubricId is "${expectedVerdictRecord.qualificationRubricId}", but qualification_rubric.json is "${qualificationRubric.qualificationRubricId}"`,
            });
          }
          checkFixtureVersion(
            problems,
            `expected_verdicts/${companyId}.json`,
            expectedVerdictRecord.fixtureVersion,
          );
          const expectedVerdict = expectedVerdictRecord.expectedVerdict;
          if (verdictFromRecording.supportsNoOnlineOrdering !== expectedVerdict.supportsNoOnlineOrdering) {
            problems.push({
              problemArea: conversationArea,
              problemDescription: `recorded supportsNoOnlineOrdering (${String(verdictFromRecording.supportsNoOnlineOrdering)}) disagrees with expected_verdicts/${companyId}.json (${String(expectedVerdict.supportsNoOnlineOrdering)})`,
            });
          }
          if (verdictFromRecording.researchReason !== expectedVerdict.researchReason) {
            problems.push({
              problemArea: conversationArea,
              problemDescription: `recorded researchReason disagrees with expected_verdicts/${companyId}.json`,
            });
          }
          if (
            JSON.stringify(verdictFromRecording.sourceUrls) !==
            JSON.stringify(expectedVerdict.sourceUrls)
          ) {
            problems.push({
              problemArea: conversationArea,
              problemDescription: `recorded sourceUrls disagree with expected_verdicts/${companyId}.json`,
            });
          }
          for (const sourceUrl of expectedVerdict.sourceUrls) {
            if (!recordedPageUrls.has(sourceUrl)) {
              problems.push({
                problemArea: `expected_verdicts/${companyId}.json`,
                problemDescription: `sourceUrls contains ${sourceUrl}, which has no recorded page text`,
              });
            }
          }
        }
      }
    }
  }

  // --- the fixture set has to teach something ---------------------------------------------------
  const expectedVerdictFileNames = readdirSync(expectedVerdictsFolderPath).filter((fileName) =>
    fileName.endsWith('.json'),
  );
  if (expectedVerdictFileNames.length !== allCompanyIds.length) {
    problems.push({
      problemArea: 'expected_verdicts/',
      problemDescription: `${expectedVerdictFileNames.length} files for ${allCompanyIds.length} companies`,
    });
  }
  const qualifyingCompanyCount = allCompanyIds.filter((companyId) => {
    try {
      return loadExpectedVerdict(companyId).expectedVerdict.supportsNoOnlineOrdering;
    } catch {
      return false; // already reported above as a missing file
    }
  }).length;
  if (qualifyingCompanyCount !== EXPECTED_QUALIFYING_COMPANY_COUNT) {
    problems.push({
      problemArea: 'expected_verdicts/',
      problemDescription: `${qualifyingCompanyCount} companies are expected to qualify; the fixture set is designed around exactly ${EXPECTED_QUALIFYING_COMPANY_COUNT} (one clear qualify and one that only qualifies once the order page is read)`,
    });
  }

  return problems;
}

// This file only exports. runFixtureValidation.ts is the command-line entry point.
