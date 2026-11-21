import { ToolLoopAgent, isStepCount, tool, type ModelMessage } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { z } from "zod";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execSync } from "node:child_process";
import { createInterface } from "node:readline/promises";

const cwd = resolve(process.argv[2] || process.cwd());

// List 1: run automatically (read-only).
const SAFE_PREFIXES = [
  "ls", "cat", "echo", "pwd", "which", "find",
  "head", "tail", "wc", "git log", "git status", "git diff",
];

// List 2: allowed, but only after a human says yes (changes files or installs).
const APPROVAL_PREFIXES = [
  "mkdir", "touch", "cp", "mv", "rm",
  "git add", "git commit", "git checkout", "git restore",
  "npm install", "bun add", "bun install", "bun test", "npx tsc",
];

// List 3: everything else is blocked outright. On top of that, these shell
// tricks are always blocked, even for list 1/2 commands, so "ls; rm -rf ~" or
// "find . -exec rm {} +" can't smuggle anything through.
const FORBIDDEN = /[;&|`$<>]|\s-(exec|execdir|delete|ok)\b/;

type Verdict = "safe" | "approval" | "blocked";

// Builds a classifier from whatever prefix lists you hand it, so the bash
// factory below and the agent's toolApproval share one rule set.
// Matches "ls" or "ls -la", but not "lsof".
function createClassifier(safePrefixes: string[], approvalPrefixes: string[] = []) {
  return (command: string): Verdict => {
    const cmd = command.trim();
    if (FORBIDDEN.test(cmd)) return "blocked";
    const matches = (p: string) => cmd === p || cmd.startsWith(p + " ");
    if (safePrefixes.some(matches)) return "safe";
    if (approvalPrefixes.some(matches)) return "approval";
    return "blocked";
  };
}

const classify = createClassifier(SAFE_PREFIXES, APPROVAL_PREFIXES);

const read = tool({
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

const grep = tool({
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

// Course signature is (operations, safePrefixes). approvalPrefixes is our
// addition so the third tier ("ask the human") survives the refactor.
export function createBashTool(
  operations: BashOperations,
  safePrefixes: string[],
  approvalPrefixes: string[] = [],
) {
  const classifyCommand = createClassifier(safePrefixes, approvalPrefixes);

  return tool({
    description: `Execute a shell command in the working directory.

WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.

WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).

DO NOT USE FOR: reading files (use read), searching code (use grep).

USAGE: command is a single shell string; pipes, redirects, ; and && are blocked.
  Read-only commands (ls, git status...) run immediately. Commands that change
  files or install packages (mkdir, rm, git commit, bun add...) wait for the
  user's approval. Anything else is blocked with an error. 30s timeout.

EXAMPLES:
  - List files: command "ls -la"
  - Check git status: command "git status"
  - Run the test suite (needs approval): command "bun test"`,
    inputSchema: z.object({
      command: z.string().describe("Shell command to execute"),
    }),
    // Second line of defense. toolApproval (on the agent) already denies
    // blocked commands and pauses approval ones, so execute only ever sees
    // safe or human-approved commands. This check stays in case that changes.
    // No execSync, no cwd, no child_process errors: just operations.exec.
    execute: async ({ command }) => {
      if (classifyCommand(command) === "blocked") {
        return `Blocked: "${command}" is not allowed. Safe: ${safePrefixes.join(", ")}. Needs approval: ${approvalPrefixes.join(", ")}.`;
      }
      const { stdout, exitCode } = await operations.exec(command);
      if (exitCode !== 0) return `Exit ${exitCode}: ${stdout}`;
      return stdout || "(no output)";
    },
  });
}

// The local backend: the only place execSync lives now. Success and failure
// come back in the same { stdout, exitCode } shape.
const localOps: BashOperations = {
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

const bash = createBashTool(localOps, SAFE_PREFIXES, APPROVAL_PREFIXES);

// Course uses "anthropic/claude-haiku-4.5" via AI Gateway (blocked on free tier).
// We call Anthropic directly; reads ANTHROPIC_API_KEY from .env.
// `export` so server.ts (the local web UI) can reuse this exact agent.
export const agent = new ToolLoopAgent({
  model: anthropic("claude-haiku-4-5"),
  instructions: `You are a coding agent.\nWorking directory: ${cwd}`,
  tools: { read, grep, bash },
  stopWhen: isStepCount(10),
  // Decides, per bash call, BEFORE execute runs:
  //   safe     -> "not-applicable": run it now
  //   approval -> "user-approval":  stop the loop and hand the request back
  //   blocked  -> "denied":         skip execute; the model is told it was denied
  toolApproval: {
    bash: ({ command }) => {
      const verdict = classify(command);
      if (verdict === "safe") return "not-applicable";
      if (verdict === "approval") {
        return { type: "user-approval", reason: `"${command}" changes files or installs packages` };
      }
      return { type: "denied", reason: `"${command}" is not on any allowlist` };
    },
  },
});

// Only run the CLI when this file is executed directly (bun run index.ts),
// not when server.ts imports it.
//
// The approval flow: generate() ends early when a tool needs approval and puts
// a "tool-approval-request" in result.content. The harness asks the human,
// appends a "tool-approval-response" message, and calls generate() again with
// the full history. Approved -> the SDK runs execute. Denied -> the model gets
// an execution-denied result and can adapt.
if (import.meta.main) {
  const prompt = process.argv.slice(3).join(" ") || "Hello!";
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const messages: ModelMessage[] = [{ role: "user", content: prompt }];
  let totalSteps = 0;

  while (true) {
    const result = await agent.generate({ messages });
    totalSteps += result.steps.length;
    messages.push(...result.responseMessages);

    // Skip isAutomatic requests: those are toolApproval's own "denied"
    // verdicts, which the SDK already answered. Responding again sends a
    // duplicate tool_result and Anthropic rejects the next call with a 400.
    const requests = result.content
      .filter((part) => part.type === "tool-approval-request")
      .filter((req) => !req.isAutomatic);
    if (requests.length === 0) {
      console.log(result.text);
      break;
    }

    const responses = [];
    for (const req of requests) {
      const input = req.toolCall.input as { command?: string };
      // If stdin has ended (piped input ran out), treat it as a "no".
      const answer = await rl
        .question(`\nThe agent wants to run: ${input.command}\nAllow? [y/N] `)
        .catch(() => "");
      const approved = answer.trim().toLowerCase() === "y";
      responses.push({
        type: "tool-approval-response" as const,
        approvalId: req.approvalId,
        approved,
        reason: approved ? undefined : "The user declined this command.",
      });
    }
    messages.push({ role: "tool", content: responses });
  }

  rl.close();
  console.log(`\n(${totalSteps} steps)`);
}
