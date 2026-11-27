// Rung 1 - one tool: fetch a page. Plus the search tool rung 2 needs.
//
// A tool is a name, a description, an input shape and an ordinary function. The model never opens a
// page itself; it asks, and this code runs.
//
// Both tools are defined ONCE here, with tool() and createSdkMcpServer, exactly as the artifact page
// shows. The same handler bodies run in both modes:
//   offline - the handler reads shared/fixtures/ through shared/replay/index.ts
//   live    - the handler makes a real network call (and search is the SDK's built-in WebSearch)

import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk';
import * as zodSchema from 'zod';

import {
  MAX_RECORDED_PAGE_TEXT_CHARACTERS,
  fetchRecordedPageTextByUrl,
  searchRecordedWeb,
} from '../../shared/replay/index.ts';
import type { AgentRunMode } from './agent-query-seam.ts';

/** The MCP server name. Every tool it holds is addressed as mcp__research__<toolName>. */
export const RESEARCH_MCP_SERVER_NAME = 'research';

export const FETCH_PAGE_MCP_TOOL_NAME = `mcp__${RESEARCH_MCP_SERVER_NAME}__fetch_url`;
export const SEARCH_WEB_MCP_TOOL_NAME = `mcp__${RESEARCH_MCP_SERVER_NAME}__search_web`;

/**
 * The recordings name the two tools "fetchPageText" and "searchTheWeb" (see
 * shared/FIXTURE_CONTRACT.md section 7). This build names them the way the artifact page does. One
 * table, two entries, so nothing has to guess.
 */
export const RECORDED_TOOL_NAME_TO_MCP_TOOL_NAME: Record<string, string> = {
  fetchPageText: FETCH_PAGE_MCP_TOOL_NAME,
  searchTheWeb: SEARCH_WEB_MCP_TOOL_NAME,
};

/** One tool call this build actually ran, in order. Tests compare this against the recording. */
export interface ExecutedToolCall {
  mcpToolName: string;
  toolArguments: Record<string, string>;
}

/**
 * A tool the offline stand-in can run directly, because offline there is no `claude` subprocess to
 * run it for us. It calls the SAME handler body the SDK would have called.
 */
export interface ReplayableResearchTool {
  mcpToolName: string;
  runToolCall(toolArguments: Record<string, string>): Promise<string>;
}

export interface ResearchToolSet {
  /** Pass this to query() in options.mcpServers. */
  researchToolServer: ReturnType<typeof createSdkMcpServer>;
  /** Pass these to options.allowedTools so they run without a permission prompt. */
  allowedResearchToolNames: string[];
  /** Used by offline mode only. */
  replayableResearchTools: ReplayableResearchTool[];
  /** Every tool call that has run, in order. */
  executedToolCalls: ExecutedToolCall[];
}

async function readPageAsToolText(
  runMode: AgentRunMode,
  pageUrl: string,
): Promise<string> {
  if (runMode === 'offline') {
    const recordedPage = await fetchRecordedPageTextByUrl(pageUrl);
    return recordedPage.pageTextMarkdown;
  }
  // Live mode: a plain fetch, with no proxy rotation and no JavaScript rendering. A production
  // build rents that from a scrape provider (Firecrawl, Exa, Browserbase) - see the README.
  const target = new URL(pageUrl);
  if (!['http:', 'https:'].includes(target.protocol)) throw new Error('Research needs an http or https page.');
  const livePageResponse = await fetch(pageUrl, { headers: { accept: 'text/html,text/plain' }, signal: AbortSignal.timeout(15000) });
  if (!livePageResponse.ok) throw new Error(`Page request failed: HTTP ${livePageResponse.status}`);
  const livePageText = await livePageResponse.text();
  return livePageText.slice(0, MAX_RECORDED_PAGE_TEXT_CHARACTERS);
}

async function searchWebAsToolText(
  runMode: AgentRunMode,
  companyIdForReplay: string,
  searchQueryText: string,
): Promise<string> {
  if (runMode !== 'offline') {
    throw new Error(
      'In live mode this build does not use a custom search tool: it allows the SDK built-in ' +
        'WebSearch instead. See README.md, "Live mode".',
    );
  }
  const recordedSearchResults = await searchRecordedWeb(companyIdForReplay, searchQueryText);
  return recordedSearchResults
    .map(
      (searchResult, resultPosition) =>
        `${resultPosition + 1}. ${searchResult.resultTitle}\n   ${searchResult.resultUrl}\n   ${searchResult.resultSnippet}`,
    )
    .join('\n');
}

/**
 * Builds the tools for one bakery's run.
 *
 * companyIdForReplay (for example 'bluebird_bakery') is only used offline, to pick that bakery's
 * recorded search results. A live search tool would not need it: it would ask the real web.
 */
export function createResearchToolSet(options: {
  runMode: AgentRunMode;
  companyIdForReplay: string;
  includeSearchTool: boolean;
}): ResearchToolSet {
  const executedToolCalls: ExecutedToolCall[] = [];

  const fetchUrlTool = tool(
    'fetch_url',
    'Read a public web page. Live mode returns its HTML or text; offline mode returns saved page text. Use it whenever you were given or have found a URL.',
    { pageUrl: zodSchema.z.string().describe('absolute URL, including https://') },
    async ({ pageUrl }) => {
      executedToolCalls.push({ mcpToolName: FETCH_PAGE_MCP_TOOL_NAME, toolArguments: { pageUrl } });
      const pageMarkdown = await readPageAsToolText(options.runMode, pageUrl);
      // Cap it: this text lands in the context window and is paid for on every later turn.
      return {
        content: [{ type: 'text' as const, text: pageMarkdown.slice(0, MAX_RECORDED_PAGE_TEXT_CHARACTERS) }],
      };
    },
  );

  const searchWebTool = tool(
    'search_web',
    'Search the web and return the top results, each with a title, a URL and a snippet.',
    { searchQueryText: zodSchema.z.string().describe('what to search for, in plain words') },
    async ({ searchQueryText }) => {
      executedToolCalls.push({
        mcpToolName: SEARCH_WEB_MCP_TOOL_NAME,
        toolArguments: { searchQueryText },
      });
      const searchResultsText = await searchWebAsToolText(
        options.runMode,
        options.companyIdForReplay,
        searchQueryText,
      );
      return { content: [{ type: 'text' as const, text: searchResultsText }] };
    },
  );

  const toolsInThisServer = options.includeSearchTool ? [fetchUrlTool, searchWebTool] : [fetchUrlTool];

  return {
    researchToolServer: createSdkMcpServer({
      name: RESEARCH_MCP_SERVER_NAME,
      version: '1.0.0',
      tools: toolsInThisServer,
    }),
    allowedResearchToolNames: options.includeSearchTool
      ? [FETCH_PAGE_MCP_TOOL_NAME, SEARCH_WEB_MCP_TOOL_NAME]
      : [FETCH_PAGE_MCP_TOOL_NAME],
    replayableResearchTools: toolsInThisServer.map((toolDefinition) => ({
      mcpToolName: `mcp__${RESEARCH_MCP_SERVER_NAME}__${toolDefinition.name}`,
      async runToolCall(toolArguments: Record<string, string>): Promise<string> {
        // The real handler body, called directly because offline there is no subprocess to call it.
        const toolResult = await toolDefinition.handler(toolArguments as never, undefined);
        const firstTextBlock = (toolResult.content ?? []).find(
          (contentBlock) => (contentBlock as { type?: string }).type === 'text',
        ) as { text?: string } | undefined;
        return firstTextBlock?.text ?? '';
      },
    })),
    executedToolCalls,
  };
}
