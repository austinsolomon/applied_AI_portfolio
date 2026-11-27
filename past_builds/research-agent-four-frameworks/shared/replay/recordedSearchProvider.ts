// Stands in for the live web-search tool (Exa in the Vercel build, the hosted web_search tool in the
// OpenAI build, WebSearch in the Claude Agent SDK build). Given a search query, it returns the
// recorded results. It never reaches the network.

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { recordedSearchResultsFolderPath } from './fixtureFileLocations.ts';
import type { CompanyId, RecordedSearch, RecordedSearchResult } from './replayTypes.ts';
import { ReplayFixtureMissingError } from './replayTypes.ts';

interface RecordedSearchFile {
  fixtureVersion: string;
  companyId: CompanyId;
  recordedSearches: RecordedSearch[];
}

function loadRecordedSearchFile(companyId: CompanyId): RecordedSearchFile {
  const searchFilePath = join(recordedSearchResultsFolderPath, `${companyId}.json`);
  try {
    return JSON.parse(readFileSync(searchFilePath, 'utf8')) as RecordedSearchFile;
  } catch {
    throw new ReplayFixtureMissingError(`recorded search results for company "${companyId}"`);
  }
}

/** Lower-cased, punctuation-stripped, single-spaced, so small query differences still match. */
function normaliseSearchQuery(searchQueryText: string): string {
  return searchQueryText
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

export function listRecordedSearchCompanyIds(): CompanyId[] {
  return readdirSync(recordedSearchResultsFolderPath)
    .filter((fileName) => fileName.endsWith('.json'))
    .map((fileName) => fileName.replace(/\.json$/, ''))
    .sort();
}

export function listRecordedSearchesForCompany(companyId: CompanyId): RecordedSearch[] {
  return loadRecordedSearchFile(companyId).recordedSearches;
}

/**
 * The replay search tool. `companyId` scopes the lookup, because in this fixture set a query only
 * ever concerns one company and scoping keeps the error message useful when a query misses.
 *
 * Matching is exact-after-normalising first, then a best-effort word-overlap match, so a build whose
 * recorded query text drifted by a word still replays instead of failing the whole run.
 */
export async function searchRecordedWeb(
  companyId: CompanyId,
  searchQueryText: string,
): Promise<RecordedSearchResult[]> {
  const recordedSearches = listRecordedSearchesForCompany(companyId);
  const normalisedRequestedQuery = normaliseSearchQuery(searchQueryText);

  const exactMatch = recordedSearches.find(
    (candidateSearch) =>
      normaliseSearchQuery(candidateSearch.searchQueryText) === normalisedRequestedQuery,
  );
  if (exactMatch !== undefined) {
    return exactMatch.searchResults;
  }

  const requestedQueryWords = new Set(normalisedRequestedQuery.split(' '));
  let bestMatchingSearch: RecordedSearch | undefined;
  let bestOverlapCount = 0;
  for (const candidateSearch of recordedSearches) {
    const candidateWords = normaliseSearchQuery(candidateSearch.searchQueryText).split(' ');
    const overlapCount = candidateWords.filter((word) => requestedQueryWords.has(word)).length;
    if (overlapCount > bestOverlapCount) {
      bestOverlapCount = overlapCount;
      bestMatchingSearch = candidateSearch;
    }
  }

  // Two shared words is a deliberately low bar: the company name alone clears it, and a query with
  // nothing in common with any recording still fails loudly rather than returning the wrong pages.
  if (bestMatchingSearch !== undefined && bestOverlapCount >= 2) {
    return bestMatchingSearch.searchResults;
  }

  throw new ReplayFixtureMissingError(
    `search query "${searchQueryText}" against company "${companyId}" ` +
      `(recorded queries: ${recordedSearches.map((search) => `"${search.searchQueryText}"`).join(', ')})`,
  );
}
