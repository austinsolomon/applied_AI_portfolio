// ILLUSTRATIVE - not run
//
// The live version of rung 1's page-reading tool, exactly as the artifact page shows it: the same
// tool contract, with a rented scraping service inside execute().
//
// Why it is not run here: @mendable/firecrawl-js is a paid service and this build makes no paid
// calls. The runnable tool is ../lib/tools/fetchPageTextTool.ts, which reads recorded pages
// instead — everything above execute() is identical.
//
// This is the exact spot where proxies, browser fingerprinting and JavaScript rendering live, and
// none of them are Vercel's problem to solve. You rent them.

import { tool } from 'ai';
import * as zodSchema from 'zod';
import Firecrawl from '@mendable/firecrawl-js';

const firecrawlClient = new Firecrawl({ apiKey: process.env.FIRECRAWL_API_KEY });

export const fetchUrl = tool({
  description:
    'Return the visible text of a public web page as Markdown. Use it whenever you were given or have found a URL.',
  inputSchema: zodSchema.object({
    pageUrl: zodSchema.string().describe('absolute URL, including https://'),
  }),
  execute: async ({ pageUrl }) => {
    // only OUR code runs this
    const scrapedPage = await firecrawlClient.scrape(pageUrl, { formats: ['markdown'] });
    return (scrapedPage.markdown ?? 'empty page').slice(0, 20_000); // cap it: this text lands in the context window
  },
});
