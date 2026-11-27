// Rung 1's real subject: a tool is a name, a description and a parameter schema wrapped around an
// ordinary function. The schema below is what the model sees. The function underneath is what your
// code runs. The model never runs anything itself.
//
// Two notes on how this differs from the artifact page:
//
//  1. The page calls its page reader `fetch_url` and its search a hosted `web_search` tool. Here the
//     two function tools are named `fetchPageText` and `searchTheWeb`, because those are the names in
//     the shared recordings (shared/FIXTURE_CONTRACT.md §7) and all three builds must dispatch the
//     same calls.
//  2. The hosted `web_search` tool has no offline equivalent - OpenAI runs it inside their own
//     request, so there is nothing for a recording to stand in for. Offline, search is an ordinary
//     function tool that reads the recorded search results. The live path in rung 2 enables the real
//     hosted tool instead.

import {
  fetchRecordedPageTextByUrl,
  searchRecordedWeb,
  type CompanyId,
} from '../../shared/replay/index.ts';

/** The two tool names this build accepts. A name outside this list is refused, never executed. */
export const SEARCH_TOOL_NAME = 'searchTheWeb';
export const FETCH_PAGE_TOOL_NAME = 'fetchPageText';

/**
 * The page-reading tool, in the shape `responses.create({ tools })` expects.
 * `strict: true` constrains the arguments the model may produce to exactly this schema.
 */
export const fetchPageTextToolDefinition = {
  type: 'function' as const,
  name: FETCH_PAGE_TOOL_NAME,
  description: 'Read one public page of a bakery website and return its text.',
  strict: true,
  parameters: {
    type: 'object',
    properties: { pageUrl: { type: 'string', description: 'The full URL of the page to read.' } },
    required: ['pageUrl'],
    additionalProperties: false,
  },
};

/** The search tool, used offline in place of OpenAI's hosted web search. */
export const searchTheWebToolDefinition = {
  type: 'function' as const,
  name: SEARCH_TOOL_NAME,
  description: 'Search the web for pages about a bakery and return the matching results.',
  strict: true,
  parameters: {
    type: 'object',
    properties: { searchQueryText: { type: 'string', description: 'What to search for.' } },
    required: ['searchQueryText'],
    additionalProperties: false,
  },
};

/** One tool call this build actually executed, kept so a test can compare it with the recording. */
export interface ExecutedToolCall {
  toolCallId: string;
  toolName: string;
  toolArguments: Record<string, unknown>;
}

/**
 * Runs one tool the model asked for.
 *
 * The model supplies a name and a JSON string of arguments. Both are checked here before anything
 * runs: an unknown name is an error, and a wrongly typed argument is an error. A model-supplied name
 * is an allowlist lookup, never a path to arbitrary code.
 */
export async function executeResearchTool(
  toolName: string,
  rawToolArgumentsJson: string,
  companyId: CompanyId,
): Promise<unknown> {
  const toolArguments = JSON.parse(rawToolArgumentsJson) as Record<string, unknown>;

  if (toolName === FETCH_PAGE_TOOL_NAME) {
    const { pageUrl } = toolArguments;
    if (typeof pageUrl !== 'string') {
      throw new Error(`${FETCH_PAGE_TOOL_NAME} needs a string pageUrl.`);
    }
    const recordedPage = await fetchRecordedPageTextByUrl(pageUrl);
    return {
      pageUrl: recordedPage.pageUrl,
      pageTextMarkdown: recordedPage.pageTextMarkdown,
    };
  }

  if (toolName === SEARCH_TOOL_NAME) {
    const { searchQueryText } = toolArguments;
    if (typeof searchQueryText !== 'string') {
      throw new Error(`${SEARCH_TOOL_NAME} needs a string searchQueryText.`);
    }
    return { searchResults: await searchRecordedWeb(companyId, searchQueryText) };
  }

  throw new Error(`This build has no tool called "${toolName}".`);
}
