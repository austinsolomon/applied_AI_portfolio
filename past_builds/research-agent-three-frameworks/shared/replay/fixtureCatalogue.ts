// Reads the fixture files that describe the task itself: the companies, the one research question,
// and the one qualification rubric. Every build reads the same three things from here, so all three
// builds provably run the same task.

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import {
  companiesFilePath,
  expectedVerdictsFolderPath,
  qualificationRubricFilePath,
  researchQuestionFilePath,
} from './fixtureFileLocations.ts';
import type {
  CompanyId,
  CompanyRecord,
  ExpectedVerdictRecord,
  QualificationRubricRecord,
  ResearchQuestionRecord,
} from './replayTypes.ts';
import { ReplayFixtureMissingError } from './replayTypes.ts';

function readJsonFile(filePath: string): Record<string, unknown> {
  return JSON.parse(readFileSync(filePath, 'utf8')) as Record<string, unknown>;
}

/** All four fictitious bakeries, in fixture order. */
export function loadAllCompanies(): CompanyRecord[] {
  return readJsonFile(companiesFilePath).companies as CompanyRecord[];
}

export function loadCompanyById(companyId: CompanyId): CompanyRecord {
  const matchingCompany = loadAllCompanies().find(
    (candidateCompany) => candidateCompany.companyId === companyId,
  );
  if (matchingCompany === undefined) {
    throw new ReplayFixtureMissingError(`company "${companyId}" in companies.json`);
  }
  return matchingCompany;
}

export function loadResearchQuestion(): ResearchQuestionRecord {
  return readJsonFile(researchQuestionFilePath) as unknown as ResearchQuestionRecord;
}

export function loadQualificationRubric(): QualificationRubricRecord {
  return readJsonFile(qualificationRubricFilePath) as unknown as QualificationRubricRecord;
}

/**
 * Fills {{companyName}}, {{cityName}}, {{stateCode}} and {{websiteDomain}} in the research question
 * template. This is the same {{column}} substitution a research table does when it builds the
 * prompt for one cell.
 */
export function buildResearchQuestionForCompany(company: CompanyRecord): string {
  const { researchQuestionTemplate } = loadResearchQuestion();
  return researchQuestionTemplate
    .replaceAll('{{companyName}}', company.companyName)
    .replaceAll('{{cityName}}', company.cityName)
    .replaceAll('{{stateCode}}', company.stateCode)
    .replaceAll('{{websiteDomain}}', company.websiteDomain);
}

export function loadExpectedVerdict(companyId: CompanyId): ExpectedVerdictRecord {
  const expectedVerdictFilePath = join(expectedVerdictsFolderPath, `${companyId}.json`);
  try {
    return readJsonFile(expectedVerdictFilePath) as unknown as ExpectedVerdictRecord;
  } catch {
    throw new ReplayFixtureMissingError(`expected verdict for company "${companyId}"`);
  }
}

/** Company ids that have an expected-verdict file, which is what the rung tests iterate over. */
export function listCompanyIdsWithExpectedVerdicts(): CompanyId[] {
  return readdirSync(expectedVerdictsFolderPath)
    .filter((fileName) => fileName.endsWith('.json'))
    .map((fileName) => fileName.replace(/\.json$/, ''))
    .sort();
}
