// Local web UI for the agent in index.ts. Not part of the course.
// Run: bun run dev  ->  http://localhost:3000
import {
  ToolLoopAgent,
  createUIMessageStream,
  createUIMessageStreamResponse,
  isStepCount,
  toUIMessageStream,
  type ModelMessage,
} from "ai";
import { MODEL_ID, instructions, model, sandbox, skills } from "./index";
import { createRegistry, registerBuiltins } from "./src/registry";
import { createApproval } from "./src/approval";
import type { SubagentListener } from "./src/tools";
import { createContextPolicy, createTokenBudget, formatSummary, splitUsage, type StepUsage } from "./src/telemetry";

const PORT = 3000;

// The web UI's own agent, for ad-hoc testing with Approve/Deny buttons.
// The course harness (index.ts) blocks unsafe commands outright; here a
// human gets asked instead:
//   1. toolApproval uses the interactive rule to decide whether to pause.
//      Safe-list commands run; anything else waits for your click.
//   2. The bash tool itself runs in background mode (no gate), because by
//      the time execute runs, either the command was safe or you approved it.
//   3. The same Module 5 context policy as the CLI (prune, then cache).
//
// One agent is built per run so its onStepEnd can report usage to that
// run's browser stream (the token-budget panel).
const needsHuman = createApproval({ mode: "interactive" });
function createWebAgent(onStep: (step: StepUsage) => void, onSubagent: SubagentListener) {
  const registry = createRegistry();
  registerBuiltins(registry, sandbox, skills, { approval: { mode: "background" }, onSubagent });
  const tools = Object.fromEntries(registry.entries());
  const contextPolicy = createContextPolicy();
  const budget = createTokenBudget(MODEL_ID);
  const agent = new ToolLoopAgent({
    model,
    instructions,
    tools,
    stopWhen: isStepCount(15),
    toolApproval: {
      bash: ({ command }) =>
        needsHuman({ command })
          ? { type: "user-approval", reason: `"${command}" is not on the safe list` }
          : "not-applicable",
    },
    prepareStep: contextPolicy.prepareStep,
    onStepEnd: ({ usage, stepNumber }) => {
      onStep(budget.record(stepNumber, splitUsage(usage), contextPolicy.prunedTokens()));
    },
  });
  return { agent, budget };
}

// Approval needs memory: when the agent pauses for a yes/no, the server must
// keep the conversation so it can resume with the human's answer. Kept in
// RAM, keyed by an id the browser sends back. Resets when the server restarts.
const conversations = new Map<string, ModelMessage[]>();

type Decision = { approvalId: string; approved: boolean };

// Run the agent on a conversation and stream every event to the browser.
// When the run ends (finished, or paused for approval), save the new
// messages so a later /api/approve call can pick up where this left off.
function runAgent(conversationId: string) {
  const messages = conversations.get(conversationId)!;
  return createUIMessageStreamResponse({
    headers: { "x-conversation-id": conversationId },
    stream: createUIMessageStream({
      async execute({ writer }) {
        // Each finished step is sent to the browser as a "data-usage" event.
        // Parent steps go out as "data-usage", subagent events as "data-subagent".
        const { agent, budget } = createWebAgent(
          (step) => writer.write({ type: "data-usage", data: step }),
          (event) => writer.write({ type: "data-subagent", data: event }),
        );
        const result = await agent.stream({ messages });
        writer.merge(toUIMessageStream({ stream: result.stream }));
        messages.push(...(await result.responseMessages));
        console.log(formatSummary(budget.totals()));
      },
      // Mid-stream failures (bad key, rate limit...) show up in the UI
      // instead of a silent blank reply.
      onError: (error) => {
        console.error("Agent error:", error);
        return error instanceof Error ? error.message : String(error);
      },
    }),
  });
}

Bun.serve({
  port: PORT,
  idleTimeout: 255, // agent runs can take a while; don't drop the stream
  async fetch(req) {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/") {
      return new Response(Bun.file(new URL("./public/index.html", import.meta.url)));
    }

    // New question: start a fresh conversation.
    if (req.method === "POST" && url.pathname === "/api") {
      const { prompt } = (await req.json()) as { prompt: string };
      const conversationId = crypto.randomUUID();
      conversations.set(conversationId, [{ role: "user", content: prompt }]);
      return runAgent(conversationId);
    }

    // Approve/Deny clicked: append the decisions as a tool-approval-response
    // message and resume the same conversation. Approved -> the SDK runs the
    // command. Denied -> the model sees the denial and adapts.
    if (req.method === "POST" && url.pathname === "/api/approve") {
      const { conversationId, decisions } = (await req.json()) as {
        conversationId: string;
        decisions: Decision[];
      };
      const messages = conversations.get(conversationId);
      if (!messages) {
        return new Response("Conversation not found (server restarted?). Ask again.", { status: 404 });
      }
      messages.push({
        role: "tool",
        content: decisions.map((d) => ({
          type: "tool-approval-response" as const,
          approvalId: d.approvalId,
          approved: d.approved,
          reason: d.approved ? undefined : "The user declined this command.",
        })),
      });
      return runAgent(conversationId);
    }

    return new Response("Not found", { status: 404 });
  },
});

console.log(`TeensyCode UI on http://localhost:${PORT} (model: ${MODEL_ID})`);
