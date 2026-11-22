// TeensyCode CLI: wires a sandbox, tools, approval policy and system prompt
// into a ToolLoopAgent. Run: bun run index.ts <folder> "your question"
import { ToolLoopAgent, isStepCount } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { resolve } from "node:path";
import { buildSystemPrompt } from "./src/system";
import { createLocalSandbox } from "./src/sandbox-local";
import { createJustBashSandbox } from "./src/sandbox-just-bash";
import type { SandboxLifecycle } from "./src/sandbox";
import { createReadTool, createGrepTool, createBashTool, createTaskTool } from "./src/tools";
import { approvalFromEnv, createApproval } from "./src/approval";
import { createContextPolicy, createTokenBudget, formatStep, formatSummary, splitUsage } from "./src/telemetry";

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
const read = createReadTool(sandbox);
const grep = createGrepTool(sandbox);
// task delegates to a fresh explorer or executor subagent (src/tools.ts);
// the subagents reuse the parent's read and grep.
const tools = {
  read,
  grep,
  bash: createBashTool(sandbox, createApproval(approval)),
  task: createTaskTool(sandbox, { read, grep }),
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
  onStepEnd: ({ usage, stepNumber, toolResults }) => {
    for (const r of toolResults) {
      console.log(`[${r.toolName}] ${JSON.stringify(r.input)} -> ${String(r.output).split("\n")[0]}`);
    }
    const step = budget.record(stepNumber, splitUsage(usage), contextPolicy.prunedTokens());
    console.error(formatStep(step, budget.totals().cost));
  },
  // Before every step: prune old tool results, then mark cache breakpoints
  // (see src/telemetry.ts createContextPolicy).
  prepareStep: contextPolicy.prepareStep,
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
    console.error(formatSummary(budget.totals()));
    await lifecycle.beforeStop?.(sandbox);
    await sandbox.stop();
  }
}
