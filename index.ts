import { ToolLoopAgent, isStepCount, tool } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { z } from "zod";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execSync } from "node:child_process";

export const cwd = resolve(process.argv[2] || process.cwd());

// Read-only commands. In interactive mode these run without asking.
const SAFE_PREFIXES = [
  "ls", "cat", "echo", "pwd", "which", "find",
  "head", "tail", "wc", "git log", "git status", "git diff",
];

// Shell tricks that never pass a prefix match: chaining (; & |), command
// substitution (` $), redirects (< >), and find's -exec/-delete. Without
// this, "ls; rm -rf ~" would pass because it starts with "ls".
const FORBIDDEN = /[;&|`$<>]|\s-(exec|execdir|delete|ok)\b/;

// True if the command is one of the prefixes as a whole word ("ls" and
// "ls -la" match "ls"; "lsof" doesn't) and contains no shell tricks.
// The course uses a bare startsWith, which lets both of those through.
function matchesAny(command: string, prefixes: string[]): boolean {
  const cmd = command.trim();
  if (FORBIDDEN.test(cmd)) return false;
  return prefixes.some((p) => cmd === p || cmd.startsWith(p + " "));
}

// The approval policy as data. "mode" is the discriminant: TypeScript only
// lets you touch `trust` after checking mode === "delegated". Defined as a
// Zod schema so a config loaded from a file or env var gets validated.
//   interactive  local use: safe-list commands run, everything else blocked
//   background   CI / automation: no human to ask, everything runs
//   delegated    subagents: only the commands the parent hands down run
export const ApprovalConfigSchema = z.discriminatedUnion("mode", [
  z.object({ mode: z.literal("interactive") }),
  z.object({ mode: z.literal("background") }),
  z.object({ mode: z.literal("delegated"), trust: z.array(z.string()) }),
]);
export type ApprovalConfig = z.infer<typeof ApprovalConfigSchema>;

// true = needs approval (so it's blocked), false = may run.
export type RequiresApproval = (input: { command: string }) => boolean;

// Turn a config into the yes/no function createBashTool uses.
export function createApproval(config: ApprovalConfig): RequiresApproval {
  return ({ command }) => {
    if (config.mode === "background") return false;
    if (config.mode === "delegated") return !matchesAny(command, config.trust);
    return !matchesAny(command, SAFE_PREFIXES);
  };
}

// Pick the mode for a CLI run, e.g.
//   APPROVAL_MODE=background bun run index.ts . "..."
//   APPROVAL_MODE=delegated APPROVAL_TRUST="git status,pwd" bun run index.ts . "..."
// Defaults to interactive. Bad values fail loudly via the schema.
function approvalFromEnv(): ApprovalConfig {
  return ApprovalConfigSchema.parse({
    mode: process.env.APPROVAL_MODE ?? "interactive",
    trust: (process.env.APPROVAL_TRUST ?? "").split(",").map((t) => t.trim()).filter(Boolean),
  });
}

export const read = tool({
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
    const abs = resolve(cwd, filePath);
    const content = readFileSync(abs, "utf-8");
    let lines = content.split("\n");

    if (offset) lines = lines.slice(offset - 1);
    if (limit) lines = lines.slice(0, limit);

    const MAX_LINES = 500;
    const truncated = lines.length > MAX_LINES;
    if (truncated) lines = lines.slice(0, MAX_LINES);

    const numbered = lines.map((l, i) => `${(offset || 1) + i}: ${l}`);
    return truncated
      ? numbered.join("\n") + `\n... (truncated at ${MAX_LINES} lines)`
      : numbered.join("\n");
  },
});

export const grep = tool({
  description: `Search file contents using regex. Returns matching lines with file paths.

WHEN TO USE: finding patterns across multiple files, locating function definitions,
  searching for imports, finding TODOs or error messages.

WHEN NOT TO USE: reading a known file (use read instead).
  Running commands (use bash instead).

DO NOT USE FOR: reading files (use read), listing directories (use bash).

USAGE: pattern is an extended regex string. glob filters by file name
  (default: all files). node_modules and .git are skipped.
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
    const dir = resolve(cwd, searchPath || ".");
    const escapedPattern = pattern.replace(/'/g, `'\\''`);
    const escapedGlob = (globFilter || "*").replace(/'/g, `'\\''`);
    const cmd = `grep -rn --exclude-dir=node_modules --exclude-dir=.git --include='${escapedGlob}' -E '${escapedPattern}' '${dir}' 2>/dev/null`;

    try {
      const stdout = execSync(cmd, { encoding: "utf-8", timeout: 10_000 });
      const lines = stdout.trim().split("\n").filter(Boolean);

      const MAX_MATCHES = 50;
      const truncated = lines.length > MAX_MATCHES;
      const result = truncated ? lines.slice(0, MAX_MATCHES) : lines;

      return truncated
        ? result.join("\n") + `\n... (${lines.length} total, showing first ${MAX_MATCHES})`
        : result.join("\n") || "No matches found.";
    } catch (error: any) {
      // grep exits 1 when nothing matches; that's a normal result, not a failure.
      const stdout = String(error?.stdout || "").trim();
      if (stdout) {
        const lines = stdout.split("\n").filter(Boolean);
        const MAX_MATCHES = 50;
        const truncated = lines.length > MAX_MATCHES;
        const result = truncated ? lines.slice(0, MAX_MATCHES) : lines;
        return truncated
          ? result.join("\n") + `\n... (${lines.length} total, showing first ${MAX_MATCHES})`
          : result.join("\n");
      }
      return "No matches found.";
    }
  },
});

// The seam. Everything the model sees (description, schema, safety check)
// lives in createBashTool above it. Everything that actually runs commands
// lives in an operations object below it. Module 4 swaps localOps for a
// sandbox without touching the tool.
export interface BashOperations {
  exec(command: string): Promise<{ stdout: string; exitCode: number }>;
}

// The bash tool, built from two injected pieces:
//   operations        WHERE commands run (localOps today, a sandbox later)
//   requiresApproval  WHETHER a command may run (from createApproval)
// The gate lives inside execute, so a blocked command comes back to the
// model as a plain string it can read and adapt to.
export function createBashTool(operations: BashOperations, requiresApproval: RequiresApproval) {
  return tool({
    description: `Execute a shell command in the working directory.

WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.

WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).

DO NOT USE FOR: reading files (use read), searching code (use grep).

USAGE: command is a single shell string. Commands not approved by the
  approval policy are blocked and return a clear error message. 30s timeout.

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
      const { stdout, exitCode } = await operations.exec(command);
      if (exitCode !== 0) return `Exit ${exitCode}: ${stdout}`;
      return stdout || "(no output)";
    },
  });
}

// The local backend: the only place execSync lives now. Success and failure
// come back in the same { stdout, exitCode } shape.
export const localOps: BashOperations = {
  exec: async (command) => {
    try {
      const stdout = execSync(command, { cwd, encoding: "utf-8", timeout: 30_000 });
      return { stdout, exitCode: 0 };
    } catch (e: any) {
      return {
        stdout: e.stdout || e.stderr || e.message || "",
        exitCode: e.status ?? 1,
      };
    }
  },
};

// Course uses "anthropic/claude-haiku-4.5" via AI Gateway (blocked on free tier).
// We call Anthropic directly; reads ANTHROPIC_API_KEY from .env.
export const model = anthropic("claude-haiku-4-5");
export const instructions = `You are a coding agent.\nWorking directory: ${cwd}`;

const approval = approvalFromEnv();
const bash = createBashTool(localOps, createApproval(approval));

export const agent = new ToolLoopAgent({
  model,
  instructions,
  tools: { read, grep, bash },
  stopWhen: isStepCount(10),
});

// CLI: only runs when this file is executed directly, not when imported.
// Prints each tool call and the first line of its result, so you can see
// which mode let a command through.
if (import.meta.main) {
  const prompt = process.argv.slice(3).join(" ") || "Hello!";
  console.log(`(approval: ${JSON.stringify(approval)})`);
  const result = await agent.generate({ prompt });
  for (const step of result.steps) {
    for (const r of step.toolResults) {
      const firstLine = String(r.output).split("\n")[0];
      console.log(`[${r.toolName}] ${JSON.stringify(r.input)} -> ${firstLine}`);
    }
  }
  console.log(`\n${result.text}\n\n(${result.steps.length} steps)`);
}
