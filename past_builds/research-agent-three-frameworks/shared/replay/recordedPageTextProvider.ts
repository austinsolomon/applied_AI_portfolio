// Stands in for the live page-fetch tool (Firecrawl in the Vercel and Claude Agent SDK builds, the
// pages.readPublic adapter in the OpenAI build). Given a URL, it returns the recorded Markdown text
// of that page. It never reaches the network.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  recordedPageIndexFilePath,
  recordedPageTextsFolderPath,
} from './fixtureFileLocations.ts';
import type { CompanyId, RecordedPageText } from './replayTypes.ts';
import { MAX_RECORDED_PAGE_TEXT_CHARACTERS, ReplayFixtureMissingError } from './replayTypes.ts';

interface RecordedPageIndexEntry {
  pageUrl: string;
  pageTextFixtureId: string;
  pageTextFilePath: string;
  companyId: CompanyId;
}

interface RecordedPageIndexFile {
  fixtureVersion: string;
  indexNote: string;
  recordedPages: RecordedPageIndexEntry[];
}

function loadRecordedPageIndex(): RecordedPageIndexEntry[] {
  const indexFile = JSON.parse(
    readFileSync(recordedPageIndexFilePath, 'utf8'),
  ) as RecordedPageIndexFile;
  return indexFile.recordedPages;
}

/** Trailing slashes and the protocol are the two things builds most often differ on. */
function normalisePageUrl(pageUrl: string): string {
  return pageUrl.trim().toLowerCase().replace(/\/+$/, '').replace(/^https?:\/\//, '');
}

function readPageMarkdown(indexEntry: RecordedPageIndexEntry): RecordedPageText {
  const markdownFilePath = join(recordedPageTextsFolderPath, indexEntry.pageTextFilePath);
  const fullMarkdown = readFileSync(markdownFilePath, 'utf8');
  return {
    pageUrl: indexEntry.pageUrl,
    pageTextFixtureId: indexEntry.pageTextFixtureId,
    companyId: indexEntry.companyId,
    pageTextMarkdown: fullMarkdown.slice(0, MAX_RECORDED_PAGE_TEXT_CHARACTERS),
  };
}

export function listRecordedPages(): RecordedPageIndexEntry[] {
  return loadRecordedPageIndex();
}

/** The replay page-fetch tool. Resolves a URL to its recorded Markdown. */
export async function fetchRecordedPageTextByUrl(pageUrl: string): Promise<RecordedPageText> {
  const normalisedRequestedUrl = normalisePageUrl(pageUrl);
  const matchingEntry = loadRecordedPageIndex().find(
    (candidateEntry) => normalisePageUrl(candidateEntry.pageUrl) === normalisedRequestedUrl,
  );
  if (matchingEntry === undefined) {
    throw new ReplayFixtureMissingError(`page text for URL "${pageUrl}"`);
  }
  return readPageMarkdown(matchingEntry);
}

/** Same thing, keyed by the fixture id a recorded search result carries. */
export async function fetchRecordedPageTextById(
  pageTextFixtureId: string,
): Promise<RecordedPageText> {
  const matchingEntry = loadRecordedPageIndex().find(
    (candidateEntry) => candidateEntry.pageTextFixtureId === pageTextFixtureId,
  );
  if (matchingEntry === undefined) {
    throw new ReplayFixtureMissingError(`page text for fixture id "${pageTextFixtureId}"`);
  }
  return readPageMarkdown(matchingEntry);
}

export function listRecordedPagesForCompany(companyId: CompanyId): RecordedPageIndexEntry[] {
  return loadRecordedPageIndex().filter(
    (candidateEntry) => candidateEntry.companyId === companyId,
  );
}
