// The agent's tools, each built by a factory that takes a Sandbox. Tools
// only ever call sandbox.readFile / sandbox.exec, never Node's fs or
// child_process, so swapping the backend never touches this file. The
// model-facing contract (description, schema, caps) is unchanged.
import { ToolLoopAgent, isStepCount, tool } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { z } from "zod";
import type { Sandbox } from "./sandbox";
import { createApproval, type RequiresApproval } from "./approval";
import { createTokenBudget, splitUsage } from "./telemetry";

const MAX_LINES = 500;
const MAX_MATCHES = 50;
// bash keeps the LAST 5,000 characters: build errors, test failures and
// stack traces print at the end of the output.
const MAX_BASH_CHARS = 5000;

// Wrap a value in single quotes for the shell, escaping any ' inside, so
// grep's pattern/glob/path can't break out of the command string.
const shellQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

export function createReadTool(sandbox: Sandbox) {
  return tool({
    description: `Read a file from the project. Returns numbered lines.

WHEN TO USE: viewing file contents, checking configurations, reading source code,
  examining specific lines with offset/limit.

WHEN NOT TO USE: searching for patterns across files (use grep instead).
  Running commands (use bash instead).

DO NOT USE FOR: searching code (use grep), executing commands (use bash),
  listing directories (use bash).

USAGE: path is relative to working directory. offset (1-indexed) and limit
  are optional. Output is capped at 500 lines.

EXAMPLES:
  - Read a config: path "package.json"
  - Read lines 40-60 of a file: path "index.ts" offset 40 limit 21`,
    inputSchema: z.object({
      path: z.string().describe("File path relative to working directory"),
      offset: z.number().optional().describe("Start line (1-indexed)"),
      limit: z.number().optional().describe("Max lines to return"),
    }),
    execute: async ({ path: filePath, offset, limit }) => {
      const content = await sandbox.readFile(filePath);
      let lines = content.split("\n");

      if (offset) lines = lines.slice(offset - 1);
      if (limit) lines = lines.slice(0, limit);

      const truncated = lines.length > MAX_LINES;
      if (truncated) lines = lines.slice(0, MAX_LINES);

      const numbered = lines.map((l, i) => `${(offset || 1) + i}: ${l}`);
      return truncated
        ? numbered.join("\n") + `\n... (truncated at ${MAX_LINES} lines)`
        : numbered.join("\n");
    },
  });
}

export function createGrepTool(sandbox: Sandbox) {
  return tool({
    description: `Search file contents using regex. Returns matching lines with file paths.

WHEN TO USE: finding patterns across multiple files, locating function definitions,
  searching for imports, finding TODOs or error messages.

WHEN NOT TO USE: reading a known file (use read instead).
  Running commands (use bash instead).

DO NOT USE FOR: reading files (use read), listing directories (use bash).

USAGE: pattern is an extended regex string. glob filters by file name
  (default: all files). node_modules, .venv and .git are skipped.
  Results are capped at 50 matches.

EXAMPLES:
  - Find all TODO comments: pattern "TODO" glob "*.ts"
  - Find function definitions: pattern "function \\w+" glob "*.ts"
  - Find imports of a package: pattern "from 'express'" glob "*.ts"`,
    inputSchema: z.object({
      pattern: z.string().describe("Regex pattern to search for"),
      path: z.string().optional().describe("Directory to search (default: working dir)"),
      glob: z.string().optional().describe("File glob filter, e.g. '*.ts'"),
    }),
    execute: async ({ pattern, path: searchPath, glob: globFilter }) => {
      // Runs inside the sandbox, so paths are relative to its working directory.
      const cmd =
        `grep -rn --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git ` +
        `--include=${shellQuote(globFilter || "*")} -E ${shellQuote(pattern)} ${shellQuote(searchPath || ".")} 2>/dev/null`;
      const { stdout, exitCode } = await sandbox.exec(cmd);

      // grep exit codes: 0 = matches, 1 = no matches (a normal result),
      // 2+ = a real error such as a bad regex.
      if (exitCode === 1) return "No matches found.";
      if (exitCode > 1) return `grep failed (exit ${exitCode}). Check the regex and path.`;

      const lines = stdout.trim().split("\n").filter(Boolean);
      const truncated = lines.length > MAX_MATCHES;
      const result = truncated ? lines.slice(0, MAX_MATCHES) : lines;
      return truncated
        ? result.join("\n") + `\n... (${lines.length} total, showing first ${MAX_MATCHES})`
        : result.join("\n") || "No matches found.";
    },
  });
}

// The bash tool, built from two injected pieces:
//   sandbox           WHERE commands run
//   requiresApproval  WHETHER a command may run (from createApproval)
// The gate lives inside execute, so a blocked command comes back to the
// model as a plain string it can read and adapt to.
export function createBashTool(sandbox: Sandbox, requiresApproval: RequiresApproval) {
  return tool({
    description: `Execute a shell command in the working directory.

WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.

WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).

DO NOT USE FOR: reading files (use read), searching code (use grep).

USAGE: command is a single shell string. Commands not approved by the
  approval policy are blocked and return a clear error message. 30s timeout.
  Output is capped at the last 5000 characters.

EXAMPLES:
  - List files: command "ls -la"
  - Check git status: command "git status"
  - Run the test suite: command "bun test"`,
    inputSchema: z.object({
      command: z.string().describe("Shell command to execute"),
    }),
    execute: async ({ command }) => {
      if (requiresApproval({ command })) {
        return `Blocked: "${command}" requires approval.`;
      }
      const { stdout, exitCode } = await sandbox.exec(command);
      const output = stdout || "(no output)";
      // Every cap says it was applied, so the model knows output is missing
      // and can narrow the command instead of acting on a partial result.
      const capped =
        output.length > MAX_BASH_CHARS
          ? output.slice(-MAX_BASH_CHARS) + `\n... (truncated, showing last ${MAX_BASH_CHARS} chars)`
          : output;
      return exitCode !== 0 ? `Exit ${exitCode}: ${capped}` : capped;
    },
  });
}

// askUser: a multiple-choice question for the user. It does not wait for an
// answer: it prints the question (so the user sees it) and returns the same
// text (so the model sees it in its history). "(Awaiting user response.)"
// tells the model the question is still open, so it ends its turn and the
// user answers in the next message. The system prompt's "Handling
// Ambiguity" section decides when the model uses it.
export function createAskUserTool() {
  return tool({
    description: `Ask the user a multiple-choice question.
WHEN TO USE: scoping ambiguous tasks, choosing between approaches,
  resolving a missing detail before acting.
WHEN NOT TO USE: you already have enough context to proceed.
DO NOT USE FOR: rhetorical questions or progress updates.`,
    inputSchema: z.object({
      question: z.string().describe("The question to ask the user"),
      options: z.array(z.string()).min(2).max(4).describe("Two to four options for the user to pick from"),
    }),
    execute: async ({ question, options }) => {
      const formatted = options.map((o, i) => `${i + 1}. ${o}`).join("\n");
      console.log(`\nQuestion: ${question}\n${formatted}\n`);
      return `Asked: "${question}"\nOptions:\n${formatted}\n\n(Awaiting user response.)`;
    },
  });
}

// ---- Subagents (Module 6) ----
//
// The parent agent delegates through one `task` tool. Each call builds a
// fresh subagent with its own context window, so the parent receives the
// subagent's final answer, not every file it read on the way. Each role
// fixes its own tools, model and step budget. Neither role can ask the user
// anything; that stays with the parent.

type ParentTools = {
  read: ReturnType<typeof createReadTool>;
  grep: ReturnType<typeof createGrepTool>;
};

// Explorer: read-only research. Haiku because exploration is reading and
// summarizing; 5 steps, so larger questions get split into several tasks.
const EXPLORER_MODEL = "claude-haiku-4-5";
const EXPLORER_STEPS = 5;

function buildExplorer(sandbox: Sandbox, parentTools: ParentTools) {
  return new ToolLoopAgent({
    model: anthropic(EXPLORER_MODEL),
    instructions: `You are an explorer agent. Investigate and report back concisely.
Working directory: ${sandbox.workingDirectory}`,
    tools: { read: parentTools.read, grep: parentTools.grep },
    stopWhen: isStepCount(EXPLORER_STEPS),
  });
}

// Executor: implementation, on Haiku like the explorer. The parent (Sonnet)
// does the planning and hands over precise instructions, so the executor
// only has to carry them out; 15 steps. Its bash runs in delegated mode: only the
// commands on the trust list run, everything else is blocked. It gets its
// own bash rather than the parent's, because no human answers its prompts.
const EXECUTOR_MODEL = "claude-haiku-4-5";
const EXECUTOR_STEPS = 15;
const EXECUTOR_TRUST = ["npm test", "npm run build", "npx tsc"];

function buildExecutor(sandbox: Sandbox, parentTools: ParentTools) {
  const executorBash = createBashTool(sandbox, createApproval({ mode: "delegated", trust: EXECUTOR_TRUST }));
  return new ToolLoopAgent({
    model: anthropic(EXECUTOR_MODEL),
    instructions: `You are an executor agent. Follow instructions precisely.
Working directory: ${sandbox.workingDirectory}
Do NOT ask questions. Do NOT explore beyond what's needed. Execute the task.`,
    tools: { read: parentTools.read, grep: parentTools.grep, bash: executorBash },
    stopWhen: isStepCount(EXECUTOR_STEPS),
  });
}

// What a subagent run reports while it works. The task tool forwards these
// to an optional listener (the web UI's Subagents panel); the CLI passes
// none. Same shapes in the Python app.
//   start  the run began: role, model, the parent's instructions
//   tool   one tool call finished: name, input, start of the output
//   usage  one step finished: token counts and cost (priced like the parent)
//   end    the run finished: status, steps, total cost, start of the answer
export type SubagentEvent =
  | { kind: "start"; id: string; role: string; model: string; description: string }
  | { kind: "tool"; id: string; toolName: string; input: unknown; output: string }
  | { kind: "usage"; id: string; step: number; cost: number | null; costWithout: number | null }
  | {
      kind: "end";
      id: string;
      status: "done" | "error";
      steps: number;
      cost: number | null;
      costWithout: number | null;
      durationMs: number;
      answer: string;
    };
export type SubagentListener = (event: SubagentEvent) => void;

type Subagent = {
  generate(call: { prompt: string; onStepEnd?: (step: any) => void }): Promise<{
    text: string;
    steps: unknown[];
    totalUsage: { inputTokens?: number; outputTokens?: number };
  }>;
};

// Runs a subagent and formats its answer as "[role: N steps]". Errors come
// back as text, because the parent expects a string from every tool and an
// exception would break its loop. Each step is priced with the role's
// model and reported to the listener; the stderr line shows what happened
// inside the subagent, which the parent's own trace never shows.
async function runSubagent(
  role: string,
  model: string,
  agent: Subagent,
  description: string,
  onEvent?: SubagentListener,
) {
  const id = crypto.randomUUID();
  const started = Date.now();
  const budget = createTokenBudget(model);
  onEvent?.({ kind: "start", id, role, model, description });
  const end = (status: "done" | "error", steps: number, answer: string) => {
    const totals = budget.totals();
    onEvent?.({
      kind: "end", id, status, steps, cost: totals.cost, costWithout: totals.costWithout,
      durationMs: Date.now() - started, answer: answer.slice(0, 400),
    });
  };
  try {
    const { text, steps, totalUsage } = await agent.generate({
      prompt: description,
      onStepEnd: ({ usage, stepNumber, toolResults }) => {
        for (const r of toolResults) {
          onEvent?.({ kind: "tool", id, toolName: r.toolName, input: r.input, output: String(r.output).slice(0, 300) });
        }
        const step = budget.record(stepNumber, splitUsage(usage));
        onEvent?.({ kind: "usage", id, step: stepNumber, cost: step.cost, costWithout: step.costWithout });
      },
    });
    console.error(
      `[${role}] done · ${steps.length} steps · ${totalUsage.inputTokens ?? 0} input, ${totalUsage.outputTokens ?? 0} output · answer ${text.length} chars`,
    );
    end("done", steps.length, text);
    return text ? `[${role}: ${steps.length} steps]\n${text}` : `(no response from ${role})`;
  } catch (e: any) {
    console.error(`[${role}] error after ${budget.steps.length} steps · ${e.message}`);
    end("error", budget.steps.length, e.message);
    return `${role} error: ${e.message}`;
  }
}

// The task tool is a router: pick the role, build that subagent, run it.
// Adding a role is one build function and one branch. onEvent is optional:
// pass a listener to watch subagent runs live (the web UI does).
export function createTaskTool(sandbox: Sandbox, parentTools: ParentTools, onEvent?: SubagentListener) {
  return tool({
    description: `Delegate work to a subagent.
Explorer (default): read-only research with Haiku. Use for searching across files,
  understanding patterns, and gathering context.
Executor: implementation with Haiku and delegated bash. Use for focused
  changes with explicit instructions and a known verification step.

WHEN TO USE: research across many files (explorer), bulk implementation (executor).
WHEN NOT TO USE: ambiguous requirements (use askUser), architectural decisions
  (the parent decides).
DO NOT USE FOR: single-step tasks the parent can do directly.`,
    inputSchema: z.object({
      description: z.string().describe("Task instructions for the subagent"),
      subagentType: z.enum(["explorer", "executor"]).default("explorer").describe("Subagent role"),
    }),
    execute: async ({ description, subagentType }) => {
      const [agent, model] =
        subagentType === "executor"
          ? [buildExecutor(sandbox, parentTools), EXECUTOR_MODEL]
          : [buildExplorer(sandbox, parentTools), EXPLORER_MODEL];
      return runSubagent(subagentType, model, agent, description, onEvent);
    },
  });
}

// Spawn permissions (not enforced yet): once subagents can call `task`
// themselves, a map like this limits which roles each role may start, and
// the check goes at the top of execute.
//   const SPAWN_PERMISSIONS: Record<string, string[]> = {
//     orchestrator: ["explorer", "executor"],
//     executor: ["explorer"],
//     explorer: [],
//   };
