// Rung 2's second tool: search, so the model no longer needs to be handed a URL.
//
// Offline, a search is answered from shared/fixtures/recorded_search_results/. Recorded search is
// per bakery, so this tool is built by a small factory that is told which bakery is being
// researched; the model still only sees a query box.
//
// Live mode would call a search API (the artifact page uses Exa). This build ships no search
// provider, so live mode says so by name rather than silently returning nothing.

import { tool } from 'ai';
import * as zodSchema from 'zod';

import { searchRecordedWeb, type CompanyId } from '../../../shared/replay/index.ts';
import { isLiveModeOn } from '../liveModeSwitch.ts';

export const SEARCH_THE_WEB_TOOL_NAME = 'searchTheWeb';

/** Builds the search tool for one bakery's recordings. */
export function createSearchTheWebTool(companyId: CompanyId) {
  return tool({
    description:
      'Search the web and get back a short list of pages, each with a title and a snippet. ' +
      'Use it when you need a page you were not given.',
    inputSchema: zodSchema.object({
      searchQueryText: zodSchema.string().describe('what you would type into a search box'),
    }),
    execute: async ({ searchQueryText }) => {
      if (isLiveModeOn()) {
        throw new Error(
          'Live search is not wired up in this teaching build: it ships no search provider. ' +
            'illustrative/rung2_live_exa_search_tool.ts shows the shape the artifact page uses (Exa). ' +
            'Unset RESEARCH_AGENT_LIVE_MODE to use the free recordings.',
        );
      }
      const recordedSearchResults = await searchRecordedWeb(companyId, searchQueryText);
      return { searchQueryText, searchResults: recordedSearchResults };
    },
  });
}
