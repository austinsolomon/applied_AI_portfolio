// ILLUSTRATIVE - not run
//
// Rung 10 — a browser tool, for pages that only show their content after a click.
//
// Why it is not run here: runInBrowser() would be a hosted browser (Browserbase, Firecrawl's
// interact endpoint) or a Playwright script running in a Vercel Sandbox. All of those are paid,
// networked services, and this build makes no paid calls.
//
// Notice how little changes: the agent from rung 2 gains one more tool and nothing else in the
// system moves. That is the point of the tool contract.

import { tool } from 'ai';
import * as zodSchema from 'zod';

export const browsePage = tool({
  description:
    'Open a page in a real browser and follow one instruction, e.g. "click Load more twice, return the list". ' +
    'Use only when fetchPageText returned a page that is missing the content.',
  inputSchema: zodSchema.object({
    pageUrl: zodSchema.string(),
    browserInstruction: zodSchema.string(),
  }),
  execute: async ({ pageUrl, browserInstruction }) => {
    // Either: start a Vercel Sandbox, run a Playwright script there, return the text.
    // Or: call a hosted browser (Browserbase, Firecrawl interact) that also handles proxies.
    return runInBrowser(pageUrl, browserInstruction);
  },
});

// rung 2's agent, one tool richer:
//   tools: { searchTheWeb, fetchPageText, browsePage }
