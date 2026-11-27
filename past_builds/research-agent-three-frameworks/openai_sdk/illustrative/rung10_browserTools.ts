// ILLUSTRATIVE - not run
//
// Rung 10, "A browser tool for pages that need clicking" - for example a bakery's Order Ahead
// button that only reveals how orders work after a click. A second execution adapter behind the same
// function-call interface as rung 1. `browser.readEvidence` is a Playwright-based service you own,
// not an SDK method, and the limits below are only real if that service enforces them.

const browserTool = {
  type: 'function' as const, name: 'browser_read', strict: true,
  description: 'Read evidence from an interactive public bakery page.',
  parameters: {
    type: 'object',
    properties: { url: { type: 'string' }, question: { type: 'string' } },
    required: ['url', 'question'], additionalProperties: false,
  },
};

// Add browserTool to rung 2's tools and this branch to its dispatcher.
if (name === 'browser_read') {
  const args = BrowserArgs.parse(JSON.parse(rawArguments));
  return browser.readEvidence({
    ...args, workspaceId, maxSteps: 8, timeoutMs: 30_000,
    readOnly: true, maxTextCharacters: 20_000,
  });
}
// BrowserArgs and browser are app-owned validation + execution adapters.
// A browser service must enforce these limits; labels alone do not enforce them.
