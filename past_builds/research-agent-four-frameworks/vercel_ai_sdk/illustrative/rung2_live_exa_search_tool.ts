// ILLUSTRATIVE - not run
//
// The live version of rung 2's search tool, as the artifact page shows it. Exa is a paid search
// API, so this build ships no search provider and the runnable tool
// (../lib/tools/searchTheWebTool.ts) answers from recorded search results instead.
//
// If you wire this up yourself: add `exa-js` to package.json, set EXA_API_KEY, and swap this tool
// in where createSearchTheWebTool() is used today. Searches cost money per call.

import { tool } from 'ai';
import * as zodSchema from 'zod';
import Exa from 'exa-js';

const exaSearchClient = new Exa(process.env.EXA_API_KEY);

export const webSearch = tool({
  description: 'Search the web. Use it when you need a page you were not given.',
  inputSchema: zodSchema.object({ searchQuery: zodSchema.string() }),
  execute: async ({ searchQuery }) =>
    exaSearchClient.searchAndContents(searchQuery, { numResults: 3, summary: true }),
});
