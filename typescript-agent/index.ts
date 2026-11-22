// TeensyCode CLI: wires a sandbox, tools, approval policy and system prompt
// into a ToolLoopAgent.
// Run: bun run index.ts [--sandbox=local|just-bash] [--model=<id>] <folder> "your question"
import { parseArgs } from "node:util";
import { ToolLoopAgent, isStepCount } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { resolve } from "node:path";
import { buildSystemPrompt } from "./src/system";
import { createLocalSandbox } from "./src/sandbox-local";
import { createJustBashSandbox } from "./src/sandbox-just-bash";
import type { Sandbox, SandboxLifecycle } from "./src/sandbox";
import { createReadTool, createGrepTool, createBashTool, createTaskTool, createAskUserTool, createTodoTool } from "./src/tools";
import { discoverGates } from "./src/verification";
import { approvalFromEnv, createApproval } from "./src/approval";
import { createContextPolicy, createTokenBudget, formatStep, formatSummary, splitUsage } from "./src/telemetry";

// Command-line arguments:
//   --sandbox  local (default) or just-bash; falls back to the SANDBOX env var
//   --model    the parent agent's model (default Sonnet); falls back to MODEL
//   positionals: the working folder, then the prompt (all remaining words)
// strict: false so the web server, which imports this file with its own
// arguments, doesn't fail on flags meant for it.
const { values, positionals } = parseArgs({
  args: process.argv.slice(2),
  options: {
    sandbox: { type: "string", default: process.env.SANDBOX ?? "local" },
    model: { type: "string", default: process.env.MODEL ?? "claude-sonnet-5-5" },
  },
  allowPositionals: true,
  strict: false,
});

export const cwd = resolve(positionals[0] || process.cwd());
const prompt = positionals.slice(1).join(" ") || "Hello!";

// Where code lives and commands run. Every tool goes through this object.
//   local      your real folder and real commands
//   just-bash  reads your real files, keeps all writes in memory
async function sandboxFromFlag(name: string, dir: string): Promise<Sandbox> {
  if (name === "just-bash") return createJustBashSandbox(dir);
  return createLocalSandbox(dir);
}
export const sandbox = await sandboxFromFlag(String(values.sandbox), cwd);

// Setup and teardown around the sandbox. Empty for now, so nothing extra
// runs; the hook points are in place for the cloud work in Module 7.
const lifecycle: SandboxLifecycle = {};
await lifecycle.afterStart?.(sandbox);

// The parent agent's model: Sonnet, because the parent plans, routes work to
// subagents, follows the prompt's contracts (Verification) and writes the
// final answer. The subagents run on Haiku (src/tools.ts): their work is
// narrow and instruction-driven, so the cheaper model fits. --model (or the
// MODEL env var) overrides the parent's model, e.g. --model=claude-haiku-4-5.
// Course uses "anthropic/claude-haiku-4.5" via AI Gateway (blocked on free
// tier), so we call Anthropic directly; reads ANTHROPIC_API_KEY from .env.
export const MODEL_ID = String(values.model);
export const model = anthropic(MODEL_ID);

const approval = approvalFromEnv();
const read = createReadTool(sandbox);
const grep = createGrepTool(sandbox);
// task delegates to a fresh explorer or executor subagent (src/tools.ts);
// the subagents reuse the parent's read and grep.
const tools = {
  read,
  grep,
  bash: createBashTool(sandbox, createApproval(approval)),
  task: createTaskTool(sandbox, { read, grep }),
  askUser: createAskUserTool(),
  todo: createTodoTool(),
};

// The current git branch, or undefined if the sandbox folder isn't a git repo.
// Asked through the sandbox, since the code may not live on this machine.
const branch = await sandbox.exec("git rev-parse --abbrev-ref HEAD");
const gitBranch = branch.exitCode === 0 ? branch.stdout.trim() : undefined;

// Project instructions: if the working directory has an AGENTS.md, its
// contents go into the prompt as a "Project Instructions" section. No file,
// no section. Read through the sandbox for the same reason as above.
const projectContext = await sandbox.readFile("AGENTS.md").catch(() => undefined);

// The project's check commands (typecheck, lint, test, build), listed in the
// prompt's Verification section. See src/verification.ts.
const verificationCommands = await discoverGates(sandbox);

// The system prompt, built from runtime facts (see src/system.ts).
// sandboxType and toolNames come from the real objects, so they can't drift.
export const instructions = buildSystemPrompt({
  workingDirectory: sandbox.workingDirectory,
  sandboxType: sandbox.type,
  toolNames: Object.keys(tools),
  gitBranch,
  projectContext,
  verificationCommands,
});

// Module 5 context policy (prune, then cache) and the token budget that
// prices each step and the session.
const contextPolicy = createContextPolicy();
const budget = createTokenBudget(MODEL_ID);

export const agent = new ToolLoopAgent({
  model,
  instructions,
  tools,
  stopWhen: isStepCount(15),
  // After each step: print its tool calls (first line of each result), then
  // the step's token counts and cost. Printed live, so a run that crashes
  // midway still shows what it did. Token lines go to stderr to stay
  // separate from the agent's answer on stdout.
  onStepEnd: ({ usage, stepNumber }) => {
    const step = budget.record(stepNumber, splitUsage(usage), contextPolicy.prunedTokens());
    console.error(formatStep(step, budget.totals().cost));
  },
  // Before every step: prune old tool results, then mark cache breakpoints
  // (see src/telemetry.ts createContextPolicy).
  prepareStep: contextPolicy.prepareStep,
});

// CLI: only runs when this file is executed directly, not when imported.
// The answer streams to stdout as it is written; tool calls, tool results
// and token lines go to stderr, so `2>/dev/null` leaves only the answer.
// Shutdown always stops the sandbox: the finally covers normal exit and
// errors, the SIGINT handler covers Ctrl-C.
if (import.meta.main) {
  console.error(
    `(sandbox: ${sandbox.type} · model: ${MODEL_ID} · approval: ${JSON.stringify(approval)} · AGENTS.md: ${projectContext ? "loaded" : "none"})`,
  );

  process.on("SIGINT", async () => {
    console.error("\nShutting down...");
    await lifecycle.beforeStop?.(sandbox);
    await sandbox.stop();
    process.exit(0);
  });

  try {
    const result = await agent.stream({ prompt });
    for await (const chunk of result.stream) {
      switch (chunk.type) {
        case "text-delta":
          process.stdout.write(chunk.text);
          break;
        case "tool-call":
          console.error(`\n[tool] ${chunk.toolName}(${JSON.stringify(chunk.input)})`);
          break;
        case "tool-result": {
          const out = typeof chunk.output === "string" ? chunk.output : JSON.stringify(chunk.output);
          console.error(`  -> ${out.slice(0, 100).replace(/\n/g, " ⏎ ")}`);
          break;
        }
      }
    }
    console.log(`\n\n(${(await result.steps).length} steps)`);
  } finally {
    console.error(formatSummary(budget.totals()));
    await lifecycle.beforeStop?.(sandbox);
    await sandbox.stop();
  }
}
