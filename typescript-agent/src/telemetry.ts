// Token budget: prices every step and tracks what the session would have
// cost without pruning and caching.
//
// Each step reports four token counts:
//   uncached    input processed at the full price
//   cacheRead   input read back from the prompt cache (about 0.1x price)
//   cacheWrite  input written to the cache this step (1.25x price)
//   output      tokens the model generated
//
// "Without pruning and caching" prices the same step as if:
//   - nothing were cached: all input at the full input price
//   - nothing were pruned: plus the tokens pruning has removed so far,
//     estimated from the size of the dropped messages (characters / 4)
import { pruneMessages, type ModelMessage } from "ai";
import { addCacheControl } from "./cache";

// USD per million tokens. Cache write is the 5-minute TTL rate.
const PRICES: Record<string, { input: number; output: number; cacheWrite: number; cacheRead: number }> = {
  "claude-haiku-4-5": { input: 1.0, output: 5.0, cacheWrite: 1.25, cacheRead: 0.1 },
  "claude-sonnet-5-5": { input: 2.0, output: 10.0, cacheWrite: 2.5, cacheRead: 0.1 },
};

export interface StepUsage {
  step: number;
  model: string;
  uncached: number;
  cacheRead: number;
  cacheWrite: number;
  output: number;
  prunedTokens: number; // estimated tokens pruning has removed so far
  cost: number | null; // null when the model has no price entry
  costWithout: number | null;
}

// The context policy from Module 5 (prune, then mark cache breakpoints),
// plus a running estimate of how many tokens pruning has removed. The SDK
// builds each step on the previous step's pruned messages, so each call's
// removal is new and the total accumulates.
export function createContextPolicy() {
  let removedChars = 0;
  return {
    prepareStep: ({ messages }: { messages: ModelMessage[] }) => {
      const pruned = pruneMessages({ messages, toolCalls: "before-last-3-messages" });
      removedChars += Math.max(0, JSON.stringify(messages).length - JSON.stringify(pruned).length);
      return { messages: addCacheControl(pruned) };
    },
    prunedTokens: () => Math.round(removedChars / 4),
  };
}

// Prices steps as they finish and keeps the session totals.
export function createTokenBudget(model: string) {
  const price = PRICES[model];
  const steps: StepUsage[] = [];

  const usd = (tokens: number, perMillion: number) => (tokens * perMillion) / 1_000_000;

  return {
    steps,
    record(
      step: number,
      usage: { uncached: number; cacheRead: number; cacheWrite: number; output: number },
      prunedTokens = 0,
    ): StepUsage {
      const entry: StepUsage = { step, model, ...usage, prunedTokens, cost: null, costWithout: null };
      if (price) {
        entry.cost =
          usd(usage.uncached, price.input) +
          usd(usage.cacheRead, price.cacheRead) +
          usd(usage.cacheWrite, price.cacheWrite) +
          usd(usage.output, price.output);
        const allInput = usage.uncached + usage.cacheRead + usage.cacheWrite + prunedTokens;
        entry.costWithout = usd(allInput, price.input) + usd(usage.output, price.output);
      }
      steps.push(entry);
      return entry;
    },
    totals() {
      const sum = (key: keyof StepUsage) => steps.reduce((a, s) => a + ((s[key] as number) ?? 0), 0);
      return {
        steps: steps.length,
        uncached: sum("uncached"),
        cacheRead: sum("cacheRead"),
        cacheWrite: sum("cacheWrite"),
        output: sum("output"),
        cost: price ? sum("cost") : null,
        costWithout: price ? sum("costWithout") : null,
      };
    },
  };
}

// Reads the AI SDK's per-step usage into the four counts the budget prices.
export function splitUsage(usage: {
  inputTokens?: number;
  outputTokens?: number;
  inputTokenDetails: { noCacheTokens?: number; cacheReadTokens?: number; cacheWriteTokens?: number };
}) {
  const cacheRead = usage.inputTokenDetails.cacheReadTokens ?? 0;
  const cacheWrite = usage.inputTokenDetails.cacheWriteTokens ?? 0;
  const uncached = usage.inputTokenDetails.noCacheTokens ?? (usage.inputTokens ?? 0) - cacheRead - cacheWrite;
  return { uncached, cacheRead, cacheWrite, output: usage.outputTokens ?? 0 };
}

// One-line formats shared by the CLI and the server log.
export const fmtUsd = (n: number | null) => (n === null ? "n/a" : `$${n.toFixed(4)}`);

export function formatStep(s: StepUsage, runningCost: number | null) {
  return (
    `Step ${s.step}: ${s.uncached} uncached, ${s.cacheRead} cached, ${s.cacheWrite} written, ` +
    `${s.output} output · step ${fmtUsd(s.cost)} · running ${fmtUsd(runningCost)}`
  );
}

export function formatSummary(t: ReturnType<ReturnType<typeof createTokenBudget>["totals"]>) {
  const saved =
    t.cost !== null && t.costWithout ? ` · saved ${Math.round((1 - t.cost / t.costWithout) * 100)}%` : "";
  return `Session: ${t.steps} steps · cost ${fmtUsd(t.cost)} · without pruning and caching ${fmtUsd(t.costWithout)}${saved}`;
}
