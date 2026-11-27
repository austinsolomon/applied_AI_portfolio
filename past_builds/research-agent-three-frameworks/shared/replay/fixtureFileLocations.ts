// Where the fixture files live, resolved from this file's own location so that a build folder can
// import it by relative path from anywhere without caring about the process working directory.

import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const thisModuleFolder = dirname(fileURLToPath(import.meta.url));

/** .../research_agent_four_ways/shared */
export const sharedFolderPath = dirname(thisModuleFolder);

/** .../research_agent_four_ways/shared/fixtures */
export const fixturesFolderPath = join(sharedFolderPath, 'fixtures');

export const companiesFilePath = join(fixturesFolderPath, 'companies.json');
export const researchQuestionFilePath = join(fixturesFolderPath, 'research_question.json');
export const qualificationRubricFilePath = join(fixturesFolderPath, 'qualification_rubric.json');

export const recordedSearchResultsFolderPath = join(fixturesFolderPath, 'recorded_search_results');
export const recordedPageTextsFolderPath = join(fixturesFolderPath, 'recorded_page_texts');
export const recordedPageIndexFilePath = join(recordedPageTextsFolderPath, 'page_index.json');
export const recordedModelResponsesFolderPath = join(
  fixturesFolderPath,
  'recorded_model_responses',
);
export const expectedVerdictsFolderPath = join(fixturesFolderPath, 'expected_verdicts');

/** The version every fixture file must declare, so a half-updated fixture set fails loudly. */
export const EXPECTED_FIXTURE_VERSION = '2.0.0';
