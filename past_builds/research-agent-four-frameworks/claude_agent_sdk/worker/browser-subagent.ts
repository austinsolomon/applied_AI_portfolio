// ILLUSTRATIVE - not run. Rung 10: a real browser, behind a subagent with its own context window.
//
// The artifact page folds this into worker/run-research.ts; it is a separate file here so the
// runnable rung 2 stays runnable. It shells out to `npx` at run time, which needs the network and
// an install, and `@playwright/mcp@latest` is an unpinned version - pin one before any live demo.
// See docs/rung_status.md.

for await (const message of query({
  prompt: filledPrompt,
  options: {
    model: 'claude-sonnet-5',
    tools: ['WebSearch', 'Agent'], // 'Agent' lets the main agent call a subagent
    mcpServers: {
      research: researchToolServer,
      playwright: { command: 'npx', args: ['-y', '@playwright/mcp@latest', '--headless'] },
    },
    allowedTools: ['WebSearch', 'Agent', 'mcp__research__fetch_url', 'mcp__playwright__*'],
    agents: {
      'browser-operator': {
        description:
          'Opens a page in a real browser and follows one instruction, such as "click Order Ahead and ' +
          'return what the next page says". Use only when fetch_url returned a page that is missing the content.',
        prompt:
          'You operate a browser. Do exactly what you are asked, then reply with only the text that was requested.',
        tools: ['mcp__playwright__*'],
        model: 'haiku',
        maxTurns: 15,
      },
    },
    maxTurns: 12,
    maxBudgetUsd: 0.4,
    settingSources: [],
  },
})) {
  /* ... same result handling as rung 2 ... */
}
