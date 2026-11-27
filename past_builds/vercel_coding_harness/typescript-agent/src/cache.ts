// Prompt-cache breakpoints. Anthropic caches a request's prefix up to each
// marked message, so the next request that starts with the same prefix is
// read from cache (cheaper, faster) instead of being processed again.
// Caching does not shrink the request: cached tokens still count toward the
// context window.
//
// Two messages are marked:
//   first  the original prompt. Everything before it (tool definitions,
//          system prompt) never changes, and pruning never removes it, so
//          this prefix stays cacheable even after pruning edits the middle.
//   last   each step appends to the end, so the next request begins with
//          exactly what this one sent.
// Anthropic limits breakpoints per request, so only these two are marked.
// Providers without cache control ignore the option.
import type { ModelMessage } from "ai";

export function addCacheControl(messages: ModelMessage[]): ModelMessage[] {
  return messages.map((msg, i) => {
    if (i === 0 || i === messages.length - 1) {
      return {
        ...msg,
        providerOptions: {
          ...msg.providerOptions,
          anthropic: { cacheControl: { type: "ephemeral" } },
        },
      };
    }
    return msg;
  });
}
