// TeensyCode CLI: wires a sandbox, tools, approval policy and system prompt
// into a ToolLoopAgent. Run: bun run index.ts <folder> "your question"
import { ToolLoopAgent, isStepCount, pruneMessages } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { resolve } from "node:path";
import { buildSystemPrompt } from "./src/system";
import { createLocalSandbox } from "./src/sandbox-local";
import { createJustBashSandbox } from "./src/sandbox-just-bash";
import type { SandboxLifecycle } from "./src/sandbox";
import { createReadTool, createGrepTool, createBashTool } from "./src/tools";
import { approvalFromEnv, createApproval } from "./src/approval";
import { addCacheControl } from "./src/cache";

export const cwd = resolve(process.argv[2] || process.cwd());

// Where code lives and commands run. Every tool goes through this object.
// SANDBOX picks the backend at startup:
//   local      (default) your real folder and real commands
//   just-bash  reads your real files, keeps all writes in memory
const sandboxType = process.env.SANDBOX || "local";
export const sandbox =
  sandboxType === "just-bash" ? await createJustBashSandbox(cwd) : createLocalSandbox(cwd);

// Setup and teardown around the sandbox. Empty for now, so nothing extra
// runs; the hook points are in place for the cloud work in Module 7.
const lifecycle: SandboxLifecycle = {};
await lifecycle.afterStart?.(sandbox);

// Which Claude model does the thinking. Haiku is the cheap default; set
// MODEL=claude-sonnet-5-5 when instruction-following matters (e.g. Haiku
// skips the Verification contract; Sonnet follows it).
// Course uses "anthropic/claude-haiku-4.5" via AI Gateway (blocked on free
// tier), so we call Anthropic directly; reads ANTHROPIC_API_KEY from .env.
export const MODEL_ID = process.env.MODEL ?? "claude-haiku-4-5";
export const model = anthropic(MODEL_ID);

const approval = approvalFromEnv();
const tools = {
  read: createReadTool(sandbox),
  grep: createGrepTool(sandbox),
  bash: createBashTool(sandbox, createApproval(approval)),
};

// The current git branch, or undefined if the sandbox folder isn't a git repo.
// Asked through the sandbox, since the code may not live on this machine.
const branch = await sandbox.exec("git rev-parse --abbrev-ref HEAD");
const gitBranch = branch.exitCode === 0 ? branch.stdout.trim() : undefined;

// Project instructions: if the working directory has an AGENTS.md, its
// contents go into the prompt as a "Project Instructions" section. No file,
// no section. Read through the sandbox for the same reason as above.
const projectContext = await sandbox.readFile("AGENTS.md").catch(() => undefined);

// The system prompt, built from runtime facts (see src/system.ts).
// sandboxType and toolNames come from the real objects, so they can't drift.
export const instructions = buildSystemPrompt({
  workingDirectory: sandbox.workingDirectory,
  sandboxType: sandbox.type,
  toolNames: Object.keys(tools),
  gitBranch,
  projectContext,
});

export const agent = new ToolLoopAgent({
  model,
  instructions,
  tools,
  stopWhen: isStepCount(15),
  // After each step: print its tool calls (first line of each result), then
  // the token telemetry: what was sent (input) and what came back (output).
  // Every step resends the whole conversation, so input grows with each tool
  // result while output stays small. Printed live, so a run that crashes
  // midway still shows what it did. Telemetry goes to stderr to stay separate
  // from the agent's answer on stdout.
  onStepEnd: ({ usage, stepNumber, toolResults }) => {
    for (const r of toolResults) {
      console.log(`[${r.toolName}] ${JSON.stringify(r.input)} -> ${String(r.output).split("\n")[0]}`);
    }
    console.error(
      `Step ${stepNumber}: ${usage.inputTokens} input, ${usage.outputTokens} output, ` +
        `${usage.inputTokenDetails.cacheReadTokens ?? 0} cached`,
    );
  },
  // Runs before every step and rewrites the messages about to be sent:
  //   1. pruneMessages drops tool calls and results older than the last 3
  //      messages (the original prompt and the model's own text are kept),
  //      so input levels off instead of growing every step.
  //   2. addCacheControl marks the first and last surviving messages as
  //      cache breakpoints. Pruning runs first because it changes which
  //      messages exist.
  prepareStep: ({ messages }) => ({
    messages: addCacheControl(pruneMessages({ messages, toolCalls: "before-last-3-messages" })),
  }),
});

// CLI: only runs when this file is executed directly, not when imported.
// Tool calls and token counts print live from onStepEnd; the answer prints
// at the end.
if (import.meta.main) {
  const prompt = process.argv.slice(3).join(" ") || "Hello!";
  console.log(
    `(sandbox: ${sandbox.type} · model: ${MODEL_ID} · approval: ${JSON.stringify(approval)} · AGENTS.md: ${projectContext ? "loaded" : "none"})`,
  );
  // try/finally: beforeStop and stop run even if the agent throws mid-run.
  try {
    const result = await agent.generate({ prompt });
    console.log(`\n${result.text}\n\n(${result.steps.length} steps)`);
  } finally {
    await lifecycle.beforeStop?.(sandbox);
    await sandbox.stop();
  }
}
