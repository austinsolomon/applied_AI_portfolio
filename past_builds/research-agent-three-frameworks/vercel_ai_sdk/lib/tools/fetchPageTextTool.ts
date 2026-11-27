// Rung 1: one tool, so the model can read a web page.
//
// A tool is three things: a `description` the model reads when it decides whether to use the tool,
// an `inputSchema` that says what arguments are allowed, and an `execute` function that only YOUR
// code ever runs. The model never fetches anything itself; it replies "please call fetchPageText
// with this URL", and the AI SDK runs `execute` and hands the result back to the model.
//
// Offline (the default) `execute` reads a recorded page from shared/fixtures/recorded_page_texts/.
// In live mode it fetches the real URL. A production build would rent a scraping service here
// (Firecrawl, Exa contents, a hosted browser) because proxies, anti-bot handling and JavaScript
// rendering all live inside this one function. See illustrative/rung1_live_firecrawl_tool.ts.

import { tool } from 'ai';
import * as zodSchema from 'zod';

import {
  fetchRecordedPageTextByUrl,
  MAX_RECORDED_PAGE_TEXT_CHARACTERS,
} from '../../../shared/replay/index.ts';
import { isLiveModeOn } from '../liveModeSwitch.ts';

export const FETCH_PAGE_TEXT_TOOL_NAME = 'fetchPageText';

async function readPageTextLive(pageUrl: string): Promise<string> {
  const liveResponse = await fetch(pageUrl);
  const livePageText = await liveResponse.text();
  return livePageText.slice(0, MAX_RECORDED_PAGE_TEXT_CHARACTERS);
}

async function readPageTextFromRecording(pageUrl: string): Promise<string> {
  const recordedPage = await fetchRecordedPageTextByUrl(pageUrl);
  return recordedPage.pageTextMarkdown;
}

/**
 * The page-reading tool. Its name matches the tool name in the recordings (`fetchPageText`), so a
 * recorded tool call lands on this exact tool.
 */
export const fetchPageTextTool = tool({
  description:
    'Return the visible text of a public web page as Markdown. Use it whenever you were given, ' +
    'or have found, a URL and need the words on that page.',
  inputSchema: zodSchema.object({
    pageUrl: zodSchema.string().describe('absolute URL, including https://'),
  }),
  execute: async ({ pageUrl }) => {
    // Cap what comes back: every character of it lands in the context window and is paid for again
    // on every later step of the loop.
    const pageTextMarkdown = isLiveModeOn()
      ? await readPageTextLive(pageUrl)
      : await readPageTextFromRecording(pageUrl);
    return { pageUrl, pageTextMarkdown };
  },
});
